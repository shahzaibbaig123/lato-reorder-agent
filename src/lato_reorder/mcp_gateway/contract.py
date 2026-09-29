"""Tool-contract check: refuse to run if the server's tools drift from what the agent was built for."""

from typing import Any

EXPECTED_TOOLS: dict[str, set[str]] = {
    "GetProductDetails": set(),
    "GetPartDetails": set(),
    "RaiseSAPPurchaseOrder": {"ItemName", "Quantity"},
}


def check_contract(tools: list[dict[str, Any]]) -> list[str]:
    """Return a list of contract violations (empty means OK)."""
    by_name = {t.get("name"): t for t in tools}
    problems: list[str] = []
    for name, expected_args in EXPECTED_TOOLS.items():
        tool = by_name.get(name)
        if tool is None:
            problems.append(f"missing tool {name}")
            continue
        schema = tool.get("inputSchema", {})
        props = set(schema.get("properties", {}))
        if props != expected_args:
            problems.append(f"{name}: expected arguments {sorted(expected_args)}, got {sorted(props)}")
        if name == "RaiseSAPPurchaseOrder":
            required = set(schema.get("required", []))
            if required != expected_args:
                problems.append(f"{name}: expected required {sorted(expected_args)}, got {sorted(required)}")
            qty_type = schema.get("properties", {}).get("Quantity", {}).get("type")
            if qty_type not in ("integer", "number"):
                problems.append(f"{name}: Quantity type {qty_type!r} is not numeric")
    return problems
