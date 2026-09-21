"""Console entry. stdio for Cursor; --http for a local Streamable HTTP connector."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from techhand_print_fab.bridges import BridgeNotInstalled
from techhand_print_fab.server import create_server
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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
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
