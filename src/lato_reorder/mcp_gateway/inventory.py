"""Read the inventory (the two read-only tools) into a validated Snapshot."""

import json
from pathlib import Path
from typing import Any

from lato_reorder.domain.ingest import build_snapshot
from lato_reorder.domain.models import Snapshot
from lato_reorder.mcp_gateway.client import McpClient, McpError
from lato_reorder.mcp_gateway.contract import check_contract
from lato_reorder.settings import Settings


class ContractDriftError(McpError):
    pass


async def fetch_raw(client: McpClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    problems = check_contract(await client.list_tools())
    if problems:
        raise ContractDriftError("; ".join(problems))
    products = (await client.call_tool("GetProductDetails")).json()
    parts = (await client.call_tool("GetPartDetails")).json()
    return products, parts


async def fetch_snapshot(settings: Settings) -> Snapshot:
    async with McpClient(
        settings.mcp_url, settings.mcp_username, settings.mcp_password.get_secret_value()
    ) as client:
        products, parts = await fetch_raw(client)
    return build_snapshot(products, parts)


def load_fixture_snapshot(directory: Path) -> Snapshot:
    """Offline snapshot from captured tool payloads (tests, demos, calibration without network)."""
    products = json.loads((directory / "03_GetProductDetails_parsed.json").read_text())
    parts = json.loads((directory / "04_GetPartDetails_parsed.json").read_text())
    return build_snapshot(products, parts)
