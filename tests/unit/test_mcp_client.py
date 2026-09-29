import json
from pathlib import Path

import httpx
import pytest

from lato_reorder.mcp_gateway.client import (
    McpAuthError,
    McpClient,
    McpRpcError,
    McpTransportError,
    parse_response_body,
)
from lato_reorder.mcp_gateway.contract import check_contract

LIVE = Path(__file__).parents[1] / "fixtures" / "live"


def sse(message: dict) -> str:
    return f"data: {json.dumps(message)}\n\n"


def test_parse_sse_body_picks_matching_id():
    body = sse({"jsonrpc": "2.0", "method": "notifications/message"}) + sse(
        {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}
    )
    resp = httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
    assert parse_response_body(resp, 7)["result"] == {"ok": True}


def test_parse_plain_json_body():
    resp = httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": {}})
    assert parse_response_body(resp, 3)["id"] == 3


def make_server(handler):
    """Fake MCP server: `handler(method, params, request)` returns (status, message|None)."""
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((body.get("method"), dict(request.headers)))
        if body["method"] == "initialize":
            msg = {"jsonrpc": "2.0", "id": body["id"], "result": {"serverInfo": {"name": "fake"}}}
            return httpx.Response(
                200, headers={"content-type": "text/event-stream", "mcp-session-id": "s1"}, text=sse(msg)
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(200)
        status, result = handler(body["method"], body.get("params"), request)
        if result is None:
            return httpx.Response(
                status, json={"error": {"code": status, "message": "Authentication failed"}}
            )
        msg = {"jsonrpc": "2.0", "id": body["id"], **result}
        return httpx.Response(status, headers={"content-type": "text/event-stream"}, text=sse(msg))

    return httpx.MockTransport(respond), seen


async def test_session_headers_and_tool_json():
    def handler(method, params, request):
        payload = [{"Title": "Gel Comfort Seat"}]
        return 200, {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}

    transport, seen = make_server(handler)
    async with McpClient("https://x/mcp", "User", "test-password", transport=transport) as mcp:
        result = await mcp.call_tool("GetPartDetails")
    assert result.json() == [{"Title": "Gel Comfort Seat"}]
    method, headers = seen[-1]
    assert method == "tools/call"
    assert headers["mcp-session-id"] == "s1"
    assert headers["authorization"] == "test-password"  # literal, no Bearer
    assert headers["username"] == "User"


async def test_401_raises_auth_error_without_retry():
    calls = []

    def handler(method, params, request):
        calls.append(method)
        return 401, None

    transport, _ = make_server(handler)
    async with McpClient("https://x/mcp", "User", "bad", transport=transport) as mcp:
        with pytest.raises(McpAuthError):
            await mcp.list_tools()
    assert calls == ["tools/list"]


async def test_rpc_error_surfaces():
    transport, _ = make_server(lambda m, p, r: (200, {"error": {"code": -32603, "message": "boom"}}))
    async with McpClient("https://x/mcp", "User", "test-password", transport=transport) as mcp:
        with pytest.raises(McpRpcError) as info:
            await mcp.list_tools()
    assert info.value.code == -32603


async def test_read_timeout_is_marked_as_possibly_sent():
    def handler(method, params, request):
        raise httpx.ReadTimeout("slow", request=request)

    transport, _ = make_server(handler)
    async with McpClient("https://x/mcp", "User", "test-password", transport=transport) as mcp:
        with pytest.raises(McpTransportError) as info:
            await mcp.call_tool("RaiseSAPPurchaseOrder", {"ItemName": "x", "Quantity": 1})
    assert info.value.request_sent is True


async def test_connect_failure_is_marked_as_not_sent():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client = McpClient("https://x/mcp", "User", "test-password", transport=httpx.MockTransport(refuse))
    with pytest.raises(McpTransportError) as info:
        await client.connect()
    assert info.value.request_sent is False


def test_live_tool_list_satisfies_contract():
    tools = json.loads((LIVE / "02_tools_list.json").read_text())["result"]["tools"]
    assert check_contract(tools) == []


def test_contract_detects_drift():
    tools = [
        {"name": "GetProductDetails", "inputSchema": {"properties": {}}},
        {"name": "RaiseSAPPurchaseOrder", "inputSchema": {"properties": {"Item": {}}, "required": ["Item"]}},
    ]
    problems = check_contract(tools)
    assert "missing tool GetPartDetails" in problems
    assert any("RaiseSAPPurchaseOrder" in p for p in problems)
