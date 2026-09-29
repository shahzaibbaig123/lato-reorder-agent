"""In-process fake of the Lato MCP server (httpx MockTransport) with fault injection.

Serves the captured live inventory, mimics RaiseSAPPurchaseOrder's real replies (including refusals that
come back with isError=false), and counts every write call so tests can assert "exactly once".
"""

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from lato_reorder.mcp_gateway.client import McpClient

LIVE = Path(__file__).parents[1] / "fixtures" / "live"


@dataclass
class Fault:
    kind: str  # "timeout_after_send" | "connect_error" | "garbled" | "wrong_quantity" | "refuse" | "auth"
    times: int = 1


@dataclass
class FakeLatoServer:
    products: list[dict[str, Any]] = field(
        default_factory=lambda: json.loads((LIVE / "03_GetProductDetails_parsed.json").read_text())
    )
    parts: list[dict[str, Any]] = field(
        default_factory=lambda: json.loads((LIVE / "04_GetPartDetails_parsed.json").read_text())
    )
    tools: list[dict[str, Any]] = field(
        default_factory=lambda: json.loads((LIVE / "02_tools_list.json").read_text())["result"]["tools"]
    )
    faults: dict[str, Fault] = field(default_factory=dict)  # by ItemName
    write_calls: list[dict[str, Any]] = field(default_factory=list)  # every call that reached the "server"
    created_pos: list[str] = field(default_factory=list)
    _po_numbers: Any = field(default_factory=lambda: itertools.count(101))

    def set_stock(self, item_name: str, stock: int) -> None:
        next(p for p in self.parts if p["Title"] == item_name)["StockQuantity"] = stock

    def client(self) -> McpClient:
        return McpClient(
            "https://fake.lato/mcp", "User", "test-password", transport=httpx.MockTransport(self._handle)
        )

    # ------------------------------------------------------------------ protocol

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            return self._reply(
                body["id"],
                {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake"}},
                headers={"mcp-session-id": "fake-session"},
            )
        if method == "notifications/initialized":
            return httpx.Response(200)
        if request.headers.get("authorization") != "test-password":
            return httpx.Response(401, json={"error": {"code": 401, "message": "Authentication failed"}})
        if method == "tools/list":
            return self._reply(body["id"], {"tools": self.tools})
        if method == "tools/call":
            name, args = body["params"]["name"], body["params"].get("arguments", {})
            if name == "GetProductDetails":
                return self._text(body["id"], json.dumps(self.products))
            if name == "GetPartDetails":
                return self._text(body["id"], json.dumps(self.parts))
            if name == "RaiseSAPPurchaseOrder":
                return self._raise_po(request, body["id"], args)
        return self._reply(body["id"], None, error={"code": -32601, "message": "method not found"})

    def _raise_po(self, request: httpx.Request, rid: int, args: dict[str, Any]) -> httpx.Response:
        item, qty = args.get("ItemName"), args.get("Quantity")
        fault = self.faults.get(item or "")
        if fault and fault.times > 0:
            fault.times -= 1
            if fault.kind == "connect_error":
                raise httpx.ConnectError("refused", request=request)  # never reached the server
            if fault.kind == "auth":
                return httpx.Response(401, json={"error": {"code": 401, "message": "Authentication failed"}})
            self.write_calls.append(dict(args))
            if fault.kind == "timeout_after_send":
                self._create(item, qty)  # SAP created it, but the reply never arrives
                raise httpx.ReadTimeout("no reply", request=request)
            if fault.kind == "garbled":
                return self._text(rid, "Service temporarily busy <html>")
            if fault.kind == "wrong_quantity":
                po = self._create(item, qty)
                return self._text(
                    rid,
                    f"Purchase order {po} for {item} from {self._supplier(item)} "
                    f"created for {int(qty) + 1} items.",
                )
            if fault.kind == "refuse":
                return self._text(rid, f"No part named '{item}' found. Call GetPartDetails for valid names.")
        self.write_calls.append(dict(args))
        if not any(p["Title"] == item for p in self.parts):
            return self._text(rid, f"No part named '{item}' found. Call GetPartDetails for valid names.")
        if not isinstance(qty, int) or qty <= 0:
            return self._text(rid, "Quantity is required and must be a positive integer.")
        po = self._create(item, qty)
        return self._text(
            rid, f"Purchase order {po} for {item} from {self._supplier(item)} created for {qty} items."
        )

    def _supplier(self, item: str) -> str:
        return str(next(p["Supplier"] for p in self.parts if p["Title"] == item))

    def _create(self, item: str, qty: int) -> str:
        po = f"{item[:3]}-{next(self._po_numbers)}"
        self.created_pos.append(po)
        return po

    def _text(self, rid: int, text: str, is_error: bool = False) -> httpx.Response:
        return self._reply(rid, {"content": [{"type": "text", "text": text}], "isError": is_error})

    def _reply(
        self,
        rid: int,
        result: dict[str, Any] | None,
        *,
        error: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
        msg.update({"error": error} if error else {"result": result})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", **(headers or {})},
            text=f"data: {json.dumps(msg)}\n\n",
        )
