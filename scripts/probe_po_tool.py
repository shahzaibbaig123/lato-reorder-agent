"""One-off M1 probe of RaiseSAPPurchaseOrder against the SANDBOX (no side effects there).

Learns: confirmation format, PO-number uniqueness, how invalid input is reported, name matching,
and latency. Uses one session and never retries. Output: tests/fixtures/live/po_probe.json
"""

import asyncio
import json
import time
from pathlib import Path

from lato_reorder.mcp_gateway.client import McpClient, McpError
from lato_reorder.settings import get_settings

OUT = Path(__file__).parents[1] / "tests" / "fixtures" / "live" / "po_probe.json"

CASES: list[tuple[str, dict[str, object]]] = [
    ("valid", {"ItemName": "Gel Comfort Seat", "Quantity": 5}),
    ("valid_repeat", {"ItemName": "Gel Comfort Seat", "Quantity": 5}),
    ("name_case_variant", {"ItemName": "gel comfort seat", "Quantity": 1}),
    ("unknown_name", {"ItemName": "Nonexistent Test Part", "Quantity": 1}),
    ("qty_zero", {"ItemName": "Gel Comfort Seat", "Quantity": 0}),
    ("qty_negative", {"ItemName": "Gel Comfort Seat", "Quantity": -3}),
    ("qty_decimal", {"ItemName": "Gel Comfort Seat", "Quantity": 2.5}),
    ("qty_huge", {"ItemName": "Gel Comfort Seat", "Quantity": 1_000_000}),
]


async def main() -> None:
    s = get_settings()
    if s.mode.value == "PRODUCTION":
        raise SystemExit("refusing to probe the PO tool in PRODUCTION mode")
    results = []
    async with McpClient(s.mcp_url, s.mcp_username, s.mcp_password.get_secret_value(), timeout=30) as mcp:
        for label, args in CASES:
            started = time.perf_counter()
            try:
                res = await mcp.call_tool("RaiseSAPPurchaseOrder", args)
                entry = {"text": res.text, "is_error": res.is_error, "raw": res.raw}
            except McpError as exc:
                entry = {"exception": type(exc).__name__, "message": str(exc)}
            entry.update(case=label, arguments=args, latency_ms=round((time.perf_counter() - started) * 1000))
            results.append(entry)
            shown = entry.get("text") or entry.get("message")
            print(f"{label:<18} {entry['latency_ms']:>5} ms  error={entry.get('is_error')}  {shown}")
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
