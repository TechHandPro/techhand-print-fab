from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from techhand_print_fab.server import create_server
from techhand_print_fab.store import Store, set_store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    monkeypatch.delenv("TECHHAND_FAB_ENABLE_TNT", raising=False)
    monkeypatch.delenv("FAB_IMPORT_ROOTS", raising=False)
    monkeypatch.delenv("FAB_EXPORT_ROOTS", raising=False)
    for name in (
        "BAMBU_LAN_HOST",
        "BAMBU_ACCESS_CODE",
        "BAMBU_SERIAL",
        "BAMBU_PRINT_ENABLED",
        "BAMBU_DISCOVER_SSDP",
        "BAMBU_ALLOW_NONPRIVATE_HOST",
        "BAMBU_TRANSPORT",
        "BAMBU_MQTT_PORT",
        "BAMBU_FTP_PORT",
        "BAMBU_MQTT_TIMEOUT_S",
        "BAMBU_FTP_TIMEOUT_S",
        "BAMBU_FARM_URL",
        "BAMBU_FARM_USERNAME",
        "BAMBU_FARM_PASSWORD",
        "BAMBU_FARM_TOKEN",
        "BAMBU_FARM_SERVER_ID",
        "BAMBU_FARM_CA_FILE",
        "BAMBU_FARM_CERT_FILE",
        "BAMBU_FARM_KEY_FILE",
        "BAMBU_FARM_TLS_INSECURE",
        "ORCA_SLICER_BIN",
        "BAMBU_STUDIO_BIN",
        "FAB_SLICER_PRESETS",
        "FAB_SLICER_PROFILE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FAB_DATA_DIR", str(tmp_path / "fab"))
    store = Store(tmp_path / "fab")
    set_store(store)
    yield store
    set_store(None)


@pytest.fixture
def server() -> MCPServer:
    return create_server()


def tool_payload(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def _run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    result = asyncio.run(_run())
    assert result.is_error is False, result.content
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def tool_names(server: MCPServer) -> dict[str, Any]:
    async def _run() -> Any:
        async with Client(server) as client:
            listed = await client.list_tools()
            return {tool.name: tool for tool in listed.tools}

    return asyncio.run(_run())
