# techhand-print-fab

Shareable MCP server for original parts: idea → parametric model → STL/3MF, plus FDM notes, Bambu X1 Carbon starting settings, a rough BOM, and an optional print push to an X1 Carbon.

OpenSCAD is the primary model. A CadQuery script is written beside it and is not executed here. Design export stays on disk. A sliced `.gcode.3mf` can be sent to the printer when you confirm it. TNT is not required.

## Install

Python 3.10+. From a checkout:

```bash
python3 -m pip install -e .
```

That installs the `techhand-print-fab` command and pins `mcp` to the 2.x line (`requirements.txt`). Use `python3 -m pip install -e ".[dev]"` when you also want pytest (`requirements-dev.txt`).

OpenSCAD is optional for the built-in kinds (box, plate, mount plate, cylinder, tube, L-bracket). When `openscad` is on `PATH`, or `OPENSCAD_BIN` points at it, STL/3MF export shells out to it and boolean holes are in the mesh. Without it, those kinds still export from a built-in mesh. The bundled trainer grip files are `custom_scad` and need OpenSCAD to mesh. See the dogfood section.

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
| `fab_discover_printers` | LAN detect probe, optional SSDP, optional Farm Manager device list. Does not print. |
| `fab_queue_print` | Send a sliced `.gcode.3mf`, or write a Studio handoff for an unsliced STL/3MF plus X1C profile notes. |

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

`custom_scad` takes `scad_body` or `source_path`. `source_path` may be `cad-v0` (the bundled trainer grip) or a `.scad` file or directory under the project folder or `FAB_IMPORT_ROOTS` (`os.pathsep`-separated). A directory becomes one part per file, named `{part_name}-{relative-stem}`, up to 50 files. A relative `include <file.scad>` inside that directory is inlined. Absolute includes, `../`, `use`, and `import()` are rejected.

Call shape:

1. `fab_create_project` with a name.
2. `fab_param_model` with `project_id`, `part_name`, and `params`.
3. `fab_export_stl` / `fab_export_3mf`.
4. `fab_dfm_check`, `fab_x1c_profile_notes`, `fab_bom_sketch` as needed.

`output_path` on export must stay inside the part directory or `FAB_EXPORT_ROOTS`.

## PRINT dogfood: trainer grip CAD v0

Bundled at `src/techhand_print_fab/cad_v0/` and installed with the package. Training grip block only. `source_path` `cad-v0` needs no `FAB_IMPORT_ROOTS` entry.

| File | Slug when `part_name` is `trainer` |
| --- | --- |
| `grip_shell.scad` | `trainer-grip-shell` |
| `grip_shell_left.scad` | `trainer-grip-shell-left` |
| `grip_shell_right.scad` | `trainer-grip-shell-right` |
| `backstrap_insert.scad` | `trainer-backstrap-insert` |
| `laser_clamp.scad` | `trainer-laser-clamp` |
| `spring_seat.scad` | `trainer-spring-seat` |
| `trigger_lever.scad` | `trainer-trigger-lever` |
| `assembly_preview.scad` | `trainer-assembly-preview` |

Left and right shells `include <grip_shell.scad>`. The import step inlines that file. `assembly_preview.scad` is a pose stub (`import_grip` is not a module in this set) and is not the STL target.

1. `fab_create_project` with `name` `Trainer grip v0` and a short description of the original trainer block.
2. `fab_param_model` with that `project_id`, `part_name` `trainer`, `source_path` `cad-v0`, and `params` `{"material": "PETG"}`.
3. `fab_list_parts` returns the eight slugs above.
4. `fab_dfm_check` on `trainer-grip-shell`. Header numbers are read from the file: wall about 2.4 mm, clearance 0.25 mm, box 110 × 32 × 120 mm. `fab_dfm_check` on `trainer-laser-clamp` warns on the 0.15 mm diametral clearance.
5. `fab_export_stl` on `trainer-grip-shell`.

Step 5 needs OpenSCAD. These parts are not a built-in primitive. The server still writes `model.scad`. If `openscad` is missing, the tool returns an error that names OpenSCAD and does not report a mesh or a printer job. Install OpenSCAD, or set `OPENSCAD_BIN`, and call `fab_export_stl` again. `fab_export_3mf` is the same gate. Box, plate, and the other primitive kinds export without OpenSCAD.

Prefer Push stays held. Ticket attach stays the optional `extras/tnt` package.

## Bambu X1 Carbon print push

The printer runs a sliced `.gcode.3mf`. `fab_export_stl` and `fab_export_3mf` write geometry only. `fab_queue_print` on those files writes a Studio handoff and does not start the printer. Slice the mesh in Bambu Studio or OrcaSlicer with an X1 Carbon profile, then queue the `.gcode.3mf`.

The tool contract is also in `openapi/print-fab.openapi.json`. That file describes MCP tools. It is not a second HTTP API. Streamable HTTP is still the MCP endpoint at `/mcp`.

### Why LAN Developer Mode

Bambu documents Developer Mode as the third-party control channel: MQTT on port 8883 and FTP on port 990, after LAN Only mode is on. That is the path this server uses for one X1 Carbon.

Farm Manager is optional. Its local REST API listens on port 8888. Use it when `BAMBU_TRANSPORT=farm`, or when `BAMBU_FARM_URL` is set and no LAN host is configured. A printer bound to Farm Manager closes its own MQTT port, so LAN and farm are two attachments. The cloud API is unused.

`fab_discover_printers` reports `path: lan_developer_mode` and a `why` string with this choice. With `BAMBU_LAN_HOST` set it sends the printer's TCP 3000 detect frame. SSDP (UDP 2021 and 1990) runs only when you pass `ssdp: true` or set `BAMBU_DISCOVER_SSDP=1`.

### Printer setup

1. On the X1 Carbon, turn on LAN Only mode.
2. Turn on Developer Mode and accept the notice on the printer.
3. Record the LAN IP, the access code, and the serial number.

### Environment

Put these in the MCP process environment. Keep them out of the repo. This package does not read a vault and does not write secrets into the project directory. Export them from your secret store when the process starts (a systemd `EnvironmentFile`, a vault agent template, or the `env` block of the MCP client).

| Variable | LAN print | Purpose |
| --- | --- | --- |
| `BAMBU_LAN_HOST` | yes | Private IP or `.local` name. |
| `BAMBU_ACCESS_CODE` | yes | LAN access code. FTPS and MQTT password. The username is `bblp`. |
| `BAMBU_SERIAL` | yes | Printer serial. MQTT topics are `device/{serial}/request` and `device/{serial}/report`. |
| `BAMBU_PRINT_ENABLED` | yes | `1` allows a confirmed queue to send. Unset or `0` returns a plan. |
| `BAMBU_DISCOVER_SSDP` | no | `1` also runs SSDP discovery. |
| `BAMBU_ALLOW_NONPRIVATE_HOST` | no | `1` allows a public address. Leave unset. |
| `BAMBU_TRANSPORT` | no | `lan` when a LAN host is set, otherwise `farm` if a farm URL is set. |
| `BAMBU_FARM_URL` | farm | `https://192.168.x.x:8888` |
| `BAMBU_FARM_TOKEN` | farm | Bearer JWT. Prefer this over the password. |
| `BAMBU_FARM_USERNAME` | farm | Local Farm Manager user, used when the token is unset. |
| `BAMBU_FARM_PASSWORD` | farm | Local Farm Manager password. Login is `POST /login/local/tickets` then `POST /login/local/tokens`. |
| `BAMBU_FARM_SERVER_ID` | no | Value for `x-bbl-sec-sid` when you already know it. |
| `BAMBU_FARM_CA_FILE` | no | CA bundle for the farm server certificate. |
| `BAMBU_FARM_CERT_FILE` | no | Optional client certificate for mTLS. |
| `BAMBU_FARM_KEY_FILE` | no | Optional client key for mTLS. |
| `BAMBU_FARM_TLS_INSECURE` | no | `1` skips farm TLS verification. The host still has to be private. Leave unset. |
| `FAB_EXPORT_ROOTS` | no | Directory that holds a sliced `.gcode.3mf` from Studio. |

`fab_queue_print` also requires `confirm: true` before it sends. An example connector with empty values is `examples/cursor-mcp.bambu.json`. Fill the values in the client config on the machine that runs the server, not in git.

LAN upload uses implicit FTPS on port 990 and stores the file in `/model` when that directory exists. The start command is MQTT `print.project_file` with `url` `ftp:///model/<file>.gcode.3mf` and `param` `Metadata/plate_1.gcode`. The `md5` field is sent empty. Profile notes ride along in the tool result. `profile_applied` stays false: this server does not ship a Bambu slicer profile and does not run a slicer.

### PRINT dogfood: test plate

1. Export the LAN variables for the X1 Carbon and set `BAMBU_PRINT_ENABLED=1`.
2. `fab_create_project` with `name` `Test plate`.
3. `fab_param_model` with `part_name` `plate` and `params` `{"kind":"plate","length_mm":20,"width_mm":20,"thickness_mm":3,"material":"PETG"}`.
4. `fab_export_stl` on `plate`.
5. `fab_queue_print` with that `project_id` and `part_name`. Expect `mode` `studio_handoff`, `printer_dispatched` false, and `studio-handoff/x1c-profile-notes.json` next to the part. Open the STL in Bambu Studio. Pick X1 Carbon, a 0.4 mm nozzle, and PETG. Check the notes against the spool datasheet. Slice. Export `plate.gcode.3mf` into the part directory or a folder listed in `FAB_EXPORT_ROOTS`.
6. `fab_discover_printers`. Expect the LAN host. `reachable: true` means the TCP 3000 detect probe answered.
7. `fab_queue_print` with `file_path` of that sliced file, `material` `PETG`, and `confirm` true. Expect `printer_dispatched` true and `dry_fire` false when the MQTT ack result is success.
8. If the ack times out, Developer Mode is still off or the serial or access code does not match. The tool does not claim the job started. The file may already be on the printer.

A call with `confirm: false` returns `mode` `print_plan` and the `project_file` body. The access code is not in that body.

### Farm Manager

Set `BAMBU_TRANSPORT=farm`, `BAMBU_FARM_URL`, and `BAMBU_FARM_TOKEN` (or the username and password). `fab_discover_printers` calls `GET /devices`. `fab_queue_print` uploads with `POST /file/upload3mf` and creates the job with `POST /task`. `queue_only: true` sends `task_print_model` 0, which the Farm Manager client uses as a queue. Direct print sends `task_print_model` 1. Pass `device_id` when more than one printer is listed.

## Guardrails

The server refuses a 1:1 copy of a proprietary commercial product.

- `reproduction` is `original` (default), `interoperable_fixture`, or `proprietary_clone`.
- `proprietary_clone` is always refused, before any file is written.
- Phrases such as "exact copy", "1:1 clone", "counterfeit", "knock-off", and "copy the commercial product" are refused on names, intent, notes, and imported OpenSCAD.
- An original bracket, a fixture you designed, or geometry from your own measurements is in scope. Saying "1:1 in millimeters" about your own sketch is not a clone request.

Design tools (`fab_create_project` through `fab_bom_sketch`) set `dry_fire: true` and `printer_dispatched: false`. Export copy says the mesh was written and no printer job was submitted. Profile notes are starting temperatures and habits for a person to type into OrcaSlicer or Bambu Studio. They are not an official Bambu profile and they are not applied to a slicer. Confirm them against the filament datasheet.

`fab_discover_printers` does not print. `fab_queue_print` sets `printer_dispatched: true` and `dry_fire: false` only after the printer or Farm Manager accepts a sliced job. A geometry STL or 3MF writes a Studio handoff. A call without `confirm: true`, or with `BAMBU_PRINT_ENABLED` unset, returns a plan.

Print tools refuse firearm and other weapon-part requests from the job name, intent, and file name. Training-tool and general fab jobs stay in scope. A disclaimer in the text is not permission to queue a weapon part.

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

CI runs that on Python 3.12 and does not install OpenSCAD. Tests cover tool schemas, the clone refusal, STL/3MF export through the built-in mesh, a mocked OpenSCAD success path, and mocked LAN MQTT / FTPS and Farm Manager HTTP clients. They do not contact a printer.

## Out of scope

The Bambu cloud API, slicing inside this process, a hard dependency on TNT, and Prefer Push. DFM follow-ups that stay out of the print-push work: hole parsing, clamp bounding box, louder overhang warnings, BOM mass after an STL exists, and slotted-clamp clearance.
