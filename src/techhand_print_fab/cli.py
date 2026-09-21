"""Console entry. stdio for Cursor; --http for a local Streamable HTTP connector."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from techhand_print_fab.bambu_config import BambuError
from techhand_print_fab.bridges import BridgeNotInstalled
from techhand_print_fab.preset_expand import (
    ExpandError,
    ensure_presets,
    find_profile_root,
    format_incomplete,
    missing_reports,
    preset_destination,
    presets_ready,
)
from techhand_print_fab.profiles import DEFAULT_BED_TYPE, DEFAULT_MATERIAL, material_list, resolve_print_material
from techhand_print_fab.server import create_server
from techhand_print_fab.slicer import find_slicer, orca_bed_name
from techhand_print_fab.store import Store, set_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="techhand-print-fab",
        description=(
            "Parametric fab MCP. Design export, plus optional Bambu X1 Carbon print push "
            "over LAN Developer Mode or Farm Manager."
        ),
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP instead of stdio. Binds to 127.0.0.1 unless --host is set.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-dir",
        default="",
        help="Project store. Defaults to FAB_DATA_DIR or ~/.local/share/techhand-print-fab.",
    )
    parser.add_argument(
        "--expand-presets",
        action="store_true",
        help=(
            "Flatten installed Orca or Bambu profiles into machine.json, process.json, "
            "and filament/<MATERIAL>.json, then exit. Does not start the MCP server."
        ),
    )
    parser.add_argument(
        "--profile-root",
        default="",
        help="resources/profiles directory. Overrides FAB_SLICER_PROFILE_ROOT for --expand-presets.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Preset directory to write. Overrides FAB_SLICER_PRESETS for --expand-presets.",
    )
    parser.add_argument(
        "--material",
        default=DEFAULT_MATERIAL,
        help=f"Filament preset to flatten. Default {DEFAULT_MATERIAL}.",
    )
    parser.add_argument(
        "--bed",
        default=DEFAULT_BED_TYPE,
        help=(
            f"Plate written to process.json curr_bed_type. Default {DEFAULT_BED_TYPE} "
            "(Textured PEI Plate). Stock X1 Carbon profiles often keep Cool Plate."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.expand_presets:
        raise SystemExit(_expand_presets(args))
    if args.data_dir:
        set_store(Store(Path(args.data_dir).expanduser()))
    try:
        server = create_server()
    except BridgeNotInstalled as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    if args.http:
        server.run(transport="streamable-http", host=args.host, port=args.port)
        return
    server.run(transport="stdio")


def _expand_presets(args: argparse.Namespace) -> int:
    material = resolve_print_material(str(args.material or ""), "")
    if not material:
        print(f"material must be {material_list()}.", file=sys.stderr)
        return 1
    try:
        bed_label = orca_bed_name(str(args.bed or "").strip().lower() or DEFAULT_BED_TYPE)
    except BambuError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    binary = find_slicer()
    binary_path = Path(binary.path) if binary is not None and binary.path else None
    explicit_root = str(args.profile_root or "").strip()
    profile_root, profile_note = find_profile_root(
        binary_path,
        explicit=explicit_root if explicit_root else None,
    )
    explicit_out = str(args.out or "").strip()
    dest, dest_problem = preset_destination(explicit_out if explicit_out else None)
    if dest is None:
        reports = missing_reports(material)
        print(
            format_incomplete(reports, None, profile_root=profile_root, profile_note=dest_problem),
            file=sys.stderr,
        )
        return 1
    if profile_root is None:
        print(
            format_incomplete(missing_reports(material), dest, profile_root=None, profile_note=profile_note),
            file=sys.stderr,
        )
        return 1
    try:
        reports = ensure_presets(dest, profile_root, material, bed_label, force=True)
    except ExpandError as exc:
        print(
            format_incomplete(missing_reports(material), dest, profile_root=profile_root, profile_note=str(exc)),
            file=sys.stderr,
        )
        return 1
    if not presets_ready(reports):
        print(
            format_incomplete(reports, dest, profile_root=profile_root, profile_note=""),
            file=sys.stderr,
        )
        return 1
    print(f"Wrote {dest / 'machine.json'}")
    print(f"Wrote {dest / 'process.json'}")
    print(f"Wrote {dest / 'filament' / f'{material}.json'}")
    if bed_label:
        print(f"process.json curr_bed_type is {bed_label}.")
    return 0
