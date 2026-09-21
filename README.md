# techhand-print-fab

Shareable MCP server for original parts: idea → parametric model → STL/3MF, plus FDM notes, Bambu X1 Carbon starting settings, and a rough BOM.

OpenSCAD is the primary model. A CadQuery script is written beside it and is not executed here. The server never starts a printer. TNT is not required.

## Install

Python 3.10+. From a checkout:

```bash
python3 -m pip install -e .
```

That installs the `techhand-print-fab` command and pins `mcp` to the 2.x line (`requirements.txt`). Use `python3 -m pip install -e ".[dev]"` when you also want pytest (`requirements-dev.txt`).

OpenSCAD is optional. When `openscad` is on `PATH` (or `OPENSCAD_BIN` points at it), STL/3MF export shells out to it. Without it, box, plate, mount plate, cylinder, tube, and L-bracket still export from a built-in mesh. Boolean holes and corner radii stay in the `.scad` file until OpenSCAD runs. `custom_scad` needs OpenSCAD to mesh.

CadQuery is not a dependency. `model.py` is a script for a machine that has CadQuery.

## Run

stdio (Cursor and most local MCP clients):

```bash
techhand-print-fab
```

Streamable HTTP, for a connector that wants a URL. Default bind is loopback only:

```bash
techhand-print-fab --http --host 127.0.0.1 --port 8765
```

The MCP path is `/mcp`.

Projects live in `FAB_DATA_DIR`, or `~/.local/share/techhand-print-fab` when that is unset. `--data-dir` overrides both.

## Install as an MCP connector

Cursor, after `techhand-print-fab` is on `PATH`. This is the shape in `examples/cursor-mcp.json`:

```json
{
  "mcpServers": {
    "techhand-print-fab": {
      "command": "techhand-print-fab"
    }
  }
}
```

Pin a data directory and an import root (for an existing `.scad` tree such as a grip CAD checkout):

```json
{
  "mcpServers": {
    "techhand-print-fab": {
      "command": "techhand-print-fab",
      "env": {
        "FAB_DATA_DIR": "/home/me/.local/share/techhand-print-fab",
        "FAB_IMPORT_ROOTS": "/path/to/cad-v0"
      }
    }
  }
}
```

From a checkout before the script is on `PATH`, point Python at `src`:

```json
{
  "mcpServers": {
    "techhand-print-fab": {
      "command": "python3",
      "args": ["-m", "techhand_print_fab"],
      "env": {
        "PYTHONPATH": "/path/to/techhand-print-fab/src"
      }
    }
  }
}
```

Grok Bot or any client that speaks Streamable HTTP: run `techhand-print-fab --http --host 127.0.0.1 --port 8765` and point the connector at `http://127.0.0.1:8765/mcp`. Do not bind a public interface unless you have your own auth in front. This server has none.

Nothing in that setup calls TNT.

## Tools

| Tool | What it does |
| --- | --- |
| `fab_create_project` | Local project. Units are millimeters. |
| `fab_list_parts` | Parts already generated. |
| `fab_param_model` | OpenSCAD (`model.scad`) and, by default, a CadQuery script (`model.py`) from params JSON. |
| `fab_export_stl` | Binary STL on disk. |
| `fab_export_3mf` | Geometry-only 3MF. Not a Bambu/Orca project and not a toolpath. |
| `fab_dfm_check` | Wall, hole, overhang, clearance, and 256 mm bed heuristics. |
| `fab_x1c_profile_notes` | Starting notes for PETG, ASA, TPU, PA, and PA-CF. |
| `fab_bom_sketch` | Filament mass and a fastener guess from hole diameters. |

`backend` on `fab_param_model` is `openscad`, `cadquery`, or `both` (default). OpenSCAD stays the primary file whenever it is written.

### Params

`kind` is `box`, `cylinder`, `tube`, `plate`, `l_bracket`, `mount_plate`, or `custom_scad`.

Prismatic parts use a corner at the origin: `+X` length, `+Y` width, `+Z` height. Round parts are centered on Z. An L bracket is a base plate plus an upright on the back edge (`+Y`).

`face` on a hole is `base` (drill along Z) or `upright` (L bracket only; `x_mm` is along the length and `y_mm` is the Z height). Cylinder hole `x_mm` / `y_mm` are offsets from the axis.

Example (`examples/l-bracket.params.json`):

```json
{
  "kind": "l_bracket",
  "length_mm": 40,
  "width_mm": 30,
  "height_mm": 25,
  "thickness_mm": 3,
  "material": "PETG",
  "clearance_mm": 0.3,
  "holes": [
    {"diameter_mm": 3.4, "x_mm": 12, "y_mm": 10, "face": "base"}
  ]
}
```

`custom_scad` takes `scad_body` or `source_path`. `source_path` must be a `.scad` file or a directory of them under the project folder or `FAB_IMPORT_ROOTS` (`os.pathsep`-separated). A directory becomes one part per file, named `{part_name}-{relative-stem}`, up to 50 files. OpenSCAD `include`, `use`, and `import()` are rejected so a prompt cannot pull in arbitrary files.

Call shape:

1. `fab_create_project` with a name.
2. `fab_param_model` with `project_id`, `part_name`, and `params`.
3. `fab_export_stl` / `fab_export_3mf`.
4. `fab_dfm_check`, `fab_x1c_profile_notes`, `fab_bom_sketch` as needed.

`output_path` on export must stay inside the part directory or `FAB_EXPORT_ROOTS`.

## Guardrails

The server refuses a 1:1 copy of a proprietary commercial product.

- `reproduction` is `original` (default), `interoperable_fixture`, or `proprietary_clone`.
- `proprietary_clone` is always refused, before any file is written.
- Phrases such as "exact copy", "1:1 clone", "counterfeit", "knock-off", and "copy the commercial product" are refused on names, intent, notes, and imported OpenSCAD.
- An original bracket, a fixture you designed, or geometry from your own measurements is in scope. Saying "1:1 in millimeters" about your own sketch is not a clone request.

Every tool result sets `dry_fire: true` and `printer_dispatched: false`. Export copy says the mesh was written and no printer job was submitted. Profile notes are starting temperatures and habits for a person to type into OrcaSlicer or Bambu Studio. They are not an official Bambu profile, they are not applied to a slicer, and they are not a completed print. Confirm them against the filament datasheet.

DFM numbers assume a 0.4 mm nozzle and a 256 mm X1 Carbon build axis. They do not inspect a sliced gcode file.

## Optional TNT bridge

Default `pip install` of this package does not include ticket attach and does not import a TNT client.

The extra lives in `extras/tnt` and registers `fab_attach_to_ticket` only when both of these are true:

1. `techhand-print-fab-tnt` is installed (entry point group `techhand_print_fab.bridges`).
2. `TECHHAND_FAB_ENABLE_TNT=1` is set when the server starts. If the flag is set and the extra is missing, the process exits instead of silently dropping the tool.

```bash
python -m pip install -e .
python -m pip install -e extras/tnt --no-deps
```

`--no-deps` avoids looking up `techhand-print-fab` on PyPI when you installed the core from this checkout. Once both packages are published, a normal install of `techhand-print-fab-tnt` is enough.

The tool runs `user-tnt attach-fab` (override the binary with `USER_TNT_COMMAND`, split like a command line, not a shell) and writes a JSON payload to stdin:

```json
{
  "action": "attach_fab_artifact",
  "ticket_id": 403,
  "project_id": "...",
  "part_name": "clip",
  "note": "",
  "files": [{"name": "model.stl", "path": "/absolute/model.stl", "bytes": 123}],
  "printer_dispatched": false
}
```

`user-tnt` has to be installed and logged in on that machine. This repo does not ship it. Exit 0 is the only confirmation the bridge reports. It still does not start a printer.

Cursor snippet with the bridge turned on:

```json
{
  "mcpServers": {
    "techhand-print-fab": {
      "command": "techhand-print-fab",
      "env": {
        "TECHHAND_FAB_ENABLE_TNT": "1"
      }
    }
  }
}
```

Leave that variable unset for a TNT-free connector. `fab_attach_to_ticket` will not be in the tool list.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

CI runs that on Python 3.12 and does not install OpenSCAD. Tests cover tool schemas, the clone refusal, and STL/3MF export through the built-in mesh, plus a mocked OpenSCAD success path.

## Out of scope

Live printer control, sliced toolpaths, Bambu or CAD vendor connectors, and a hard dependency on TNT.
