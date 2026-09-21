from __future__ import annotations

import json
from pathlib import Path

from techhand_print_fab.openapi_doc import build_openapi
from tests.conftest import tool_names

_DOCUMENT = Path("openapi/print-fab.openapi.json")


def test_openapi_matches_registered_tools(server) -> None:
    document = build_openapi(server)
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"]
    names = {path.rsplit("/", 1)[-1] for path in document["paths"]}
    assert names == set(tool_names(server))
    assert "fab_bambu_push_3mf" in names
    assert "fab_bambu_discover" in names
    assert "fab_bambu_status" in names
    on_disk = json.loads(_DOCUMENT.read_text(encoding="utf-8"))
    assert on_disk == document
    blob = json.dumps(document)
    assert "BAMBU_ACCESS_CODE" not in blob or "environment" in document["info"]["description"]
    assert "99887766" not in blob
