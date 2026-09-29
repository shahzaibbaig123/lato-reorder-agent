"""Minimal MCP (2024-11-05, streamable HTTP) client for the Lato inventory server.

Written directly on httpx rather than the MCP SDK so the money path is fully explicit:
no hidden retries, per-call timeouts, and a clear split between "failed before the request
was sent" (safe to retry) and "may have been sent" (must never be retried automatically).
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import httpx

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "lato-reorder-agent", "version": "0.1.0"}


class McpError(Exception):
    """Base class for MCP gateway errors."""


class McpAuthError(McpError):
    """Server rejected the credentials (HTTP 401). Never retried: the sandbox account is shared,
    and repeated failures can lock it for everyone."""


class McpProtocolError(McpError):
    """Server answered, but not with a usable JSON-RPC response."""


class McpRpcError(McpError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code, self.message, self.data = code, message, data


class McpTransportError(McpError):
    """Network-level failure. `request_sent` is False only when the request provably never left
    this process (connect failure/timeout). Otherwise the server may have acted on it."""

    def __init__(self, message: str, *, request_sent: bool) -> None:
        super().__init__(message)
        self.request_sent = request_sent


@dataclass(frozen=True)
class ToolResult:
    text: str  # concatenated text content blocks
    is_error: bool  # MCP-level tool error (result.isError)
    raw: dict[str, Any] = field(repr=False)

    def json(self) -> Any:
        """Tools return JSON inside a text block; parse it."""
        return json.loads(self.text)


def parse_response_body(response: httpx.Response, request_id: int) -> dict[str, Any]:
    """Return the JSON-RPC message answering `request_id` from a JSON or SSE body."""
    content_type = response.headers.get("content-type", "")
    text = response.text
    if "text/event-stream" in content_type or text.lstrip().startswith(("event:", "data:")):
        messages = [
            json.loads(line[5:].strip())
            for line in text.splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
    else:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpProtocolError(f"unparseable body: {text[:200]!r}") from exc
        messages = parsed if isinstance(parsed, list) else [parsed]
    for message in messages:
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    raise McpProtocolError(f"no response for request id {request_id}: {text[:200]!r}")


class McpClient:
    """One MCP session. Use as an async context manager:

    async with McpClient(url, username, password) as mcp:
        parts = (await mcp.call_tool("GetPartDetails")).json()
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        *,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._http = httpx.AsyncClient(
            headers={
                "Username": username,
                "Authorization": password,  # literal value, not a Bearer scheme
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=timeout,
            transport=transport,
        )
        self._ids = itertools.count(1)
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}

    async def __aenter__(self) -> McpClient:
        await self.connect()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self._http.aclose()

    async def connect(self) -> None:
        result = await self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.server_info = result.get("serverInfo", {})
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools: list[dict[str, Any]] = result.get("tools", [])
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> ToolResult:
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        )
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return ToolResult(text="\n".join(texts), is_error=bool(result.get("isError")), raw=result)

    async def _request(
        self, method: str, params: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        request_id = next(self._ids)
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        response = await self._post(payload, timeout=timeout)
        message = parse_response_body(response, request_id)
        if "error" in message:
            err = message["error"]
            raise McpRpcError(err.get("code", 0), err.get("message", ""), err.get("data"))
        result: dict[str, Any] = message.get("result", {})
        return result

    async def _post(self, payload: dict[str, Any], *, timeout: float | None = None) -> httpx.Response:
        headers = {"Mcp-Session-Id": self.session_id} if self.session_id else {}
        try:
            response = await self._http.post(
                self._url, json=payload, headers=headers, timeout=timeout or self._timeout
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise McpTransportError(f"connect failed: {exc!r}", request_sent=False) from exc
        except httpx.TransportError as exc:
            raise McpTransportError(f"transport failed: {exc!r}", request_sent=True) from exc

        if response.status_code == 401:
            raise McpAuthError(f"authentication failed: {response.text[:200]}")
        if response.status_code >= 400:
            raise McpProtocolError(f"HTTP {response.status_code}: {response.text[:200]}")
        if self.session_id is None and (sid := response.headers.get("mcp-session-id")):
            self.session_id = sid
        return response
