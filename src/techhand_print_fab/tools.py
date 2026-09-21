"""MCP tool handlers. Descriptions on these functions are the tool contract."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from techhand_print_fab.bom import bom_sketch
from techhand_print_fab.cadquery_gen import render_cadquery
from techhand_print_fab.dfm import dfm_report
from techhand_print_fab.exporting import ExportError, build_mesh, write_mesh_file
from techhand_print_fab.openscad_gen import render_openscad
from techhand_print_fab.paths import bundled_cad_v0, export_roots, import_roots, resolve_under
from techhand_print_fab.policy import screen
from techhand_print_fab.profiles import MATERIALS, canonical_material, material_list, profile_notes
from techhand_print_fab.results import failure, ok
from techhand_print_fab.scad_source import load_scad_tree_file, scad_parameter_hints
from techhand_print_fab.spec import SpecError, normalize
from techhand_print_fab.store import StoreError, get_store, slugify, spec_to_dict, utc_now

_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
_REPLACE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_READ = ToolAnnotations(
    read_only_hint=True,
    open_world_hint=False,
)

_EXPORT_MESSAGE = (
    "Mesh file written. No printer job was submitted. This is a dry-fire design export."
)
_MAX_IMPORTS = 50


def register_core_tools(server: MCPServer) -> None:
    server.add_tool(fab_create_project, annotations=_WRITE)
    server.add_tool(fab_list_parts, annotations=_READ)
    server.add_tool(fab_param_model, annotations=_REPLACE)
    server.add_tool(fab_export_stl, annotations=_REPLACE)
    server.add_tool(fab_export_3mf, annotations=_REPLACE)
    server.add_tool(fab_dfm_check, annotations=_READ)
    server.add_tool(fab_x1c_profile_notes, annotations=_READ)
    server.add_tool(fab_bom_sketch, annotations=_READ)


def fab_create_project(
    name: Annotated[str, Field(description="Name for an original design project.", max_length=120)],
    description: Annotated[
        str,
        Field(description="What the original part or fixture is for. Not a commercial clone brief."),
    ] = "",
    units: Annotated[str, Field(description="Length unit. v1 accepts mm only.")] = "mm",
    reproduction: Annotated[
        str,
        Field(
            description="original, interoperable_fixture, or proprietary_clone. "
            "proprietary_clone is refused."
        ),
    ] = "original",
) -> dict[str, Any]:
    """Create a local project directory. Does not talk to a printer or to TNT."""
    blocked = screen(name, description, reproduction=reproduction)
    if blocked:
        return blocked
    if units.strip().lower() != "mm":
        return failure("v1 supports millimeters only")
    try:
        project = get_store().create_project(name, description)
    except StoreError as exc:
        return failure(str(exc))
    return ok("Created project.", project_id=project["id"], project=project)


def fab_list_parts(
    project_id: Annotated[str, Field(description="Id returned by fab_create_project.")],
) -> dict[str, Any]:
    """List parts already generated in a project."""
    try:
        store = get_store()
        project = store.read_project(project_id)
        if project is None:
            return failure("unknown project id")
        blocked = screen(str(project.get("name", "")), str(project.get("description", "")))
        if blocked:
            return blocked
        parts = [_summarize(record) for record in store.list_parts(project_id)]
    except StoreError as exc:
        return failure(str(exc))
    return ok(
        f"{len(parts)} part(s).",
        project_id=project_id,
        parts=parts,
    )


def fab_param_model(
    project_id: Annotated[str, Field(description="Id returned by fab_create_project.")],
    part_name: Annotated[str, Field(description="Part name. Stored as a filesystem slug.")],
    params: Annotated[
        dict[str, Any],
        Field(
            description=(
                "Parametric model JSON. kind is box, cylinder, tube, plate, l_bracket, "
                "mount_plate, or custom_scad. Dimensions are millimeters."
            )
        ),
    ],
    intent: Annotated[
        str,
        Field(description="Why this original part exists. Clone requests are refused."),
    ] = "",
    backend: Annotated[
        str,
        Field(description="openscad (primary), cadquery (script only), or both. Default both."),
    ] = "both",
    source_path: Annotated[
        str,
        Field(
            description=(
                "Optional .scad file or directory to ingest. Use cad-v0 for the bundled "
                "trainer grip set. Other paths must sit under the project directory or "
                "FAB_IMPORT_ROOTS. Relative include <> inside that root is inlined. "
                "Absolute includes, ../, use, and import() are rejected."
            )
        ),
    ] = "",
    reproduction: Annotated[
        str,
        Field(description="original, interoperable_fixture, or proprietary_clone."),
    ] = "original",
) -> dict[str, Any]:
    """Generate OpenSCAD and, by default, a CadQuery script from params JSON.

    OpenSCAD is the primary backend. The CadQuery file is source to run later
    with CadQuery installed; this server does not execute it. No mesh is sent
    to a printer.
    """
    if not isinstance(params, dict):
        return failure("params must be a JSON object")
    chosen = backend.strip().lower() or "both"
    if chosen not in {"openscad", "cadquery", "both"}:
        return failure("backend must be openscad, cadquery, or both")
    try:
        store = get_store()
        project = store.read_project(project_id)
        if project is None:
            return failure("unknown project id")
        slug = slugify(part_name)
        sources = _load_sources(store, project_id, source_path) if source_path else []
    except (StoreError, SpecError, OSError) as exc:
        return failure(str(exc))

    if sources and params.get("kind") not in (None, "", "custom_scad"):
        return failure("source_path ingest uses kind custom_scad. Remove other kind values.")

    jobs = _jobs(part_name, slug, params, sources)
    texts = [
        part_name,
        intent,
        str(project.get("name", "")),
        str(project.get("description", "")),
    ]
    for _job_slug, _job_name, job_params in jobs:
        texts.append(str(job_params.get("notes") or ""))
        texts.append(str(job_params.get("scad_body") or ""))
        texts.append(str(job_params.get("material") or ""))
    blocked = screen(*texts, reproduction=reproduction)
    if blocked:
        return blocked

    written: list[dict[str, Any]] = []
    try:
        for job_slug, job_name, job_params in jobs:
            written.append(
                _write_part(
                    store,
                    project_id,
                    job_slug,
                    job_name,
                    job_params,
                    intent,
                    reproduction,
                    chosen,
                )
            )
    except (StoreError, SpecError, OSError, SyntaxError) as exc:
        return failure(str(exc))
    return ok(
        "Generated OpenSCAD as the primary model."
        if chosen != "cadquery"
        else "Generated a CadQuery script. Export still uses the fallback mesh until OpenSCAD is selected.",
        project_id=project_id,
        primary_backend="openscad" if chosen != "cadquery" else "cadquery",
        parts=written,
    )


def fab_export_stl(
    project_id: Annotated[str, Field(description="Project id.")],
    part_name: Annotated[str, Field(description="Part name or slug.")],
    output_path: Annotated[
        str,
        Field(description="Optional extra copy path under the part dir or FAB_EXPORT_ROOTS."),
    ] = "",
) -> dict[str, Any]:
    """Write a binary STL. Dry-fire: the file is not sent to a printer."""
    return _export(project_id, part_name, "stl", output_path)


def fab_export_3mf(
    project_id: Annotated[str, Field(description="Project id.")],
    part_name: Annotated[str, Field(description="Part name or slug.")],
    output_path: Annotated[
        str,
        Field(description="Optional extra copy path under the part dir or FAB_EXPORT_ROOTS."),
    ] = "",
) -> dict[str, Any]:
    """Write a geometry-only 3MF mesh. Not a Bambu project and not a sliced job."""
    result = _export(project_id, part_name, "3mf", output_path)
    if result.get("ok"):
        result["slicer_project"] = False
        result["message"] = (
            _EXPORT_MESSAGE + " Geometry-only 3MF. It has no Bambu or Orca toolpath."
        )
    return result


def fab_dfm_check(
    project_id: Annotated[str, Field(description="Project id.")],
    part_name: Annotated[str, Field(description="Part name or slug.")],
) -> dict[str, Any]:
    """Heuristics for wall thickness, holes, overhang, clearance, and X1C bed size.

    Notes are for a human reviewing an FDM print. Nothing is sliced or printed.
    """
    loaded = _load_spec(project_id, part_name)
    if isinstance(loaded, dict):
        return loaded
    _slug, spec = loaded
    report = dfm_report(spec)
    return ok(
        f"DFM summary: {report['summary']}. Heuristics only; no slicer and no printer.",
        project_id=project_id,
        part_name=_slug,
        **report,
    )


def fab_x1c_profile_notes(
    material: Annotated[
        str,
        Field(description=f"{material_list()}. Notes are not a slicer profile."),
    ],
    intent: Annotated[str, Field(description="Optional context. Clone requests are refused.")] = "",
) -> dict[str, Any]:
    """Starting nozzle, bed, fan, and drying notes for a Bambu X1 Carbon class setup.

    These are notes to type into Orca or Bambu Studio. This tool does not apply
    a profile and does not start a print.
    """
    blocked = screen(material, intent)
    if blocked:
        return blocked
    notes = profile_notes(material)
    if notes is None:
        return failure(
            f"material must be {material_list()}",
            supported=list(MATERIALS),
        )
    return ok(str(notes["disclaimer"]), **notes)


def fab_bom_sketch(
    project_id: Annotated[str, Field(description="Project id.")],
    part_name: Annotated[str, Field(description="Part name or slug.")],
) -> dict[str, Any]:
    """Rough filament mass and fastener guesses from part metadata. Not a quote."""
    loaded = _load_spec(project_id, part_name)
    if isinstance(loaded, dict):
        return loaded
    slug, spec = loaded
    sketch = bom_sketch(spec)
    return ok(
        "Rough BOM sketch. Not a sliced estimate and not a price.",
        project_id=project_id,
        part_name=slug,
        material=canonical_material(spec.material) if spec.material else "",
        **sketch,
    )


def _export(project_id: str, part_name: str, kind: str, output_path: str) -> dict[str, Any]:
    loaded = _load_spec(project_id, part_name)
    if isinstance(loaded, dict):
        return loaded
    slug, spec = loaded
    try:
        directory = get_store().part_dir(project_id, slug)
        target: Path | None = None
        if output_path.strip():
            target = _resolve_output(output_path, directory)
            if target is None:
                return failure("output_path is outside the part directory and FAB_EXPORT_ROOTS")
        built = build_mesh(spec, directory / "model.scad")
        filename = "model.stl" if kind == "stl" else "model.3mf"
        canonical = directory / filename
        triangle_count = write_mesh_file(built.mesh, canonical, kind, slug)
        paths = [str(canonical)]
        if target is not None and target != canonical.resolve():
            if target.is_symlink():
                return failure(f"refusing to write through symlink {target.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical.read_bytes())
            paths.append(str(target))
    except (StoreError, ExportError, OSError, ValueError) as exc:
        return failure(str(exc))
    low, high = built.mesh.bounds()
    return ok(
        _EXPORT_MESSAGE,
        project_id=project_id,
        part_name=slug,
        format=kind,
        path=str(canonical),
        paths=paths,
        triangle_count=triangle_count,
        bytes=canonical.stat().st_size,
        geometry=built.source,
        booleans_omitted=built.booleans_omitted,
        warning=built.warning,
        bounding_box_mm={
            "min": {"x": low[0], "y": low[1], "z": low[2]},
            "max": {"x": high[0], "y": high[1], "z": high[2]},
        },
    )


def _load_spec(project_id: str, part_name: str) -> dict[str, Any] | tuple[str, Any]:
    try:
        store = get_store()
        if store.read_project(project_id) is None:
            return failure("unknown project id")
        slug = slugify(part_name)
        record = store.read_part(project_id, slug)
    except StoreError as exc:
        return failure(str(exc))
    if record is None:
        return failure("unknown part")
    blocked = screen(
        str(record.get("name", "")),
        str(record.get("intent", "")),
        str(record.get("spec", {}).get("notes", "")),
        str(record.get("spec", {}).get("scad_body", "")),
        reproduction=str(record.get("reproduction") or "original"),
    )
    if blocked:
        return blocked
    try:
        spec = normalize(dict(record.get("spec") or {}))
    except SpecError as exc:
        return failure(str(exc))
    return slug, spec


def _load_sources(store: Any, project_id: str, source_path: str) -> list[tuple[str, str]]:
    roots = import_roots([store.project_dir(project_id)])
    located = _locate(source_path, roots)
    if located is None or not located.exists():
        raise SpecError("source_path is outside the project directory and FAB_IMPORT_ROOTS")
    if located.is_file():
        return [(located.stem, _read_scad(located, roots))]
    if not located.is_dir():
        raise SpecError("source_path is not a .scad file or directory")
    files = sorted(path for path in located.rglob("*.scad") if path.is_file())
    if not files:
        raise SpecError("source directory has no .scad files")
    if len(files) > _MAX_IMPORTS:
        raise SpecError(f"source directory has more than {_MAX_IMPORTS} .scad files")
    loaded: list[tuple[str, str]] = []
    for path in files:
        resolved = resolve_under(roots, path)
        if resolved is None:
            raise SpecError("a .scad path escaped the import roots")
        relative = resolved.relative_to(located.resolve()).with_suffix("")
        loaded.append((relative.as_posix().replace("/", "-"), _read_scad(resolved, roots)))
    return loaded


def _locate(source_path: str, roots: list[Path]) -> Path | None:
    token = source_path.strip().replace("\\", "/").strip("/")
    if token in {"cad-v0", "cad_v0"}:
        bundle = bundled_cad_v0()
        if bundle.is_dir():
            return bundle
    candidate = Path(source_path).expanduser()
    options = [candidate] if candidate.is_absolute() else [Path.cwd() / candidate]
    if not candidate.is_absolute():
        options.extend(root / candidate for root in roots)
    for option in options:
        resolved = resolve_under(roots, option)
        if resolved is not None and resolved.exists():
            return resolved
    return None


def _read_scad(path: Path, roots: list[Path]) -> str:
    return load_scad_tree_file(path, roots)


def _jobs(
    part_name: str,
    slug: str,
    params: dict[str, Any],
    sources: list[tuple[str, str]],
) -> list[tuple[str, str, dict[str, Any]]]:
    if not sources:
        return [(slug, part_name.strip(), dict(params))]
    if len(sources) == 1:
        _stem, text = sources[0]
        return [(slug, part_name.strip(), _scad_job(params, text))]
    jobs: list[tuple[str, str, dict[str, Any]]] = []
    for stem, text in sources:
        name = f"{part_name.strip()}-{stem}" if part_name.strip() else stem
        jobs.append((slugify(name), name, _scad_job(params, text)))
    return jobs


def _scad_job(params: dict[str, Any], text: str) -> dict[str, Any]:
    job = dict(params)
    job["kind"] = "custom_scad"
    job["scad_body"] = text
    for key, value in scad_parameter_hints(text).items():
        if job.get(key) in (None, ""):
            job[key] = value
    return job


def _write_part(
    store: Any,
    project_id: str,
    slug: str,
    part_name: str,
    params: dict[str, Any],
    intent: str,
    reproduction: str,
    backend: str,
) -> dict[str, Any]:
    spec = normalize(params)
    if spec.kind == "custom_scad" and not spec.scad_body.strip():
        raise SpecError("custom_scad needs scad_body or a source_path")
    if spec.kind == "custom_scad" and backend == "cadquery":
        raise SpecError("custom_scad is stored as OpenSCAD. Use backend openscad or both.")
    write_scad = backend in {"openscad", "both"} or spec.kind == "custom_scad"
    write_cq = backend in {"cadquery", "both"}
    text_files: dict[str, str] = {}
    file_names: dict[str, str] = {}
    if write_scad:
        text_files["model.scad"] = render_openscad(spec)
        file_names["openscad"] = "model.scad"
    if write_cq:
        script = render_cadquery(spec)
        ast.parse(script)
        text_files["model.py"] = script
        file_names["cadquery"] = "model.py"
    existing = store.read_part(project_id, slug)
    now = utc_now()
    record = {
        "name": part_name,
        "slug": slug,
        "intent": intent,
        "reproduction": reproduction,
        "params": params,
        "spec": spec_to_dict(spec),
        "primary_backend": "openscad" if write_scad else "cadquery",
        "backends": [key for key in ("openscad", "cadquery") if key in file_names],
        "files": file_names,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }
    directory = store.save_part(project_id, slug, record, text_files)
    return {
        "name": part_name,
        "slug": slug,
        "kind": spec.kind,
        "primary_backend": record["primary_backend"],
        "files": {key: str(directory / filename) for key, filename in file_names.items()},
    }


def _summarize(record: dict[str, Any]) -> dict[str, Any]:
    spec = record.get("spec") or {}
    return {
        "name": record.get("name"),
        "slug": record.get("slug"),
        "kind": spec.get("kind"),
        "material": spec.get("material"),
        "primary_backend": record.get("primary_backend"),
        "files": record.get("files"),
        "updated_at": record.get("updated_at"),
    }


def _resolve_output(output_path: str, part_directory: Path) -> Path | None:
    roots = export_roots([part_directory])
    candidate = Path(output_path).expanduser()
    if not candidate.is_absolute():
        candidate = part_directory / candidate
    resolved = resolve_under(roots, candidate)
    if resolved is None or resolved.is_dir():
        return None
    if resolve_under(roots, resolved.parent) is None:
        return None
    return resolved
