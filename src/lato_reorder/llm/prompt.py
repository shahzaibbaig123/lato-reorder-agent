"""Render the analyst prompts. The policy section is generated from PolicyConfig and the rule catalogue,
so the prompt and the engine cannot drift apart."""

import hashlib
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from lato_reorder.plan import ReorderPlan
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.decision import EngineDecision
from lato_reorder.policy.rules import RULE_DESCRIPTIONS

PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts"
UNTRUSTED_TEXT_LIMIT = 500


def _env(prompt_dir: Path) -> Environment:
    return Environment(loader=FileSystemLoader(prompt_dir), undefined=StrictUndefined, autoescape=False)


def render_system_prompt(policy: PolicyConfig, prompt_dir: Path = PROMPT_DIR) -> str:
    return (
        _env(prompt_dir).get_template("analyst_system.md.j2").render(policy=policy, rules=RULE_DESCRIPTIONS)
    )


def render_summary_prompt(prompt_dir: Path = PROMPT_DIR) -> str:
    return _env(prompt_dir).get_template("summary_system.md.j2").render()


def prompt_hash(*texts: str) -> str:
    return hashlib.sha256("\n".join(texts).encode()).hexdigest()[:16]


def _clip(text: str) -> str:
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in " \t")
    return cleaned[:UNTRUSTED_TEXT_LIMIT]


def fact_sheet(d: EngineDecision) -> dict[str, Any]:
    f, c = d.facts, d.calc
    sheet: dict[str, Any] = {
        "part_key": f.part_key,
        "decision": d.decision.value,
        "tier": d.tier.value if d.tier else None,
        "quantity": d.quantity,
        "route": d.route.value,
        "primary_rule": d.primary_rule.value,
        "rules_applied": [r.value for r in d.rules_applied],
        "route_reasons": [r.value for r in d.route_reasons],
        "flags": [x.value for x in d.flags],
        "on_hand": f.on_hand,
        "on_order": f.on_order,
        "inventory_position": f.inventory_position,
        "fan_out": f.fan_out,
        "catalogue_size": f.catalogue_size,
        "units_per_bike": f.units_per_bike,
        "units_source": f.units_source,
        "units_per_bike_confirmed_by_procurement": f.units_source == "OVERRIDE",
        "finished_bike_stock_of_dependent_models": f.fg_by_model,
        "low_stock_dependent_models": f.exposed_models,
        "low_stock_dependent_model_count": len(f.exposed_models),
        "data_issues": f.data_issues,
    }
    if c is not None:
        sheet["calc"] = {
            "cycle_demand": c.cycle_demand,
            "reorder_point": c.reorder_point,
            "order_up_to": c.order_up_to,
            "cover_before_cycles": c.cover_before,
            "cover_after_cycles": c.cover_after,
            "derivation": [s.expression for s in c.steps],
        }
    return sheet


def untrusted(d: EngineDecision) -> dict[str, Any]:
    f = d.facts
    return {
        "part_name": _clip(f.item_name),
        "description": _clip(f.description),
        "supplier": _clip(f.supplier or ""),
        "used_by_models": [_clip(m) for m in f.used_by],
    }


def part_user_message(d: EngineDecision) -> str:
    return (
        f"<fact_sheet>\n{json.dumps(fact_sheet(d), indent=1)}\n</fact_sheet>\n"
        f"<untrusted_data>\n{json.dumps(untrusted(d), indent=1, ensure_ascii=False)}\n</untrusted_data>\n"
        f"Write the PartNarrative for part_key {json.dumps(d.facts.part_key)}."
    )


def summary_user_message(plan: ReorderPlan) -> str:
    orders = [
        {
            "part": r.decision.facts.item_name,
            "tier": r.decision.tier.value if r.decision.tier else None,
            "used_by_models": r.decision.facts.fan_out,
            "on_hand": r.decision.facts.on_hand,
            "quantity": r.decision.quantity,
            "cover_cycles_now": r.decision.calc.cover_before if r.decision.calc else None,
            "route": r.final_route.value,
            "why": r.narrative.justification,
            "concerns": [c.code.value for c in r.concerns],
        }
        for r in plan.recommendations
        if r.decision.quantity > 0 or r.final_route.value == "BLOCK"
    ]
    held = [
        {
            "part": r.decision.facts.item_name,
            "rule": r.decision.primary_rule.value,
            "flags": [f.value for f in r.decision.flags],
        }
        for r in plan.recommendations
        if r.decision.quantity == 0 and r.final_route.value != "BLOCK"
    ]
    units_by_route: dict[str, int] = {}
    units_by_tier: dict[str, int] = {}
    for r in plan.orders():
        units_by_route[r.final_route.value] = units_by_route.get(r.final_route.value, 0) + r.decision.quantity
        tier = r.decision.tier.value if r.decision.tier else "NONE"
        units_by_tier[tier] = units_by_tier.get(tier, 0) + r.decision.quantity
    awaiting = [h["part"] for h in held if h["rule"] == "HOLD-OPEN-PO"]
    flag_counts: dict[str, int] = {}
    for r in plan.recommendations:
        for f in r.decision.flags:
            flag_counts[f.value] = flag_counts.get(f.value, 0) + 1
    payload = {
        "totals": plan.totals.model_dump(),
        "units_by_route": units_by_route,
        "units_by_tier": units_by_tier,
        "parts_not_ordered": len(held),
        "parts_awaiting_open_po": len(awaiting),
        "awaiting_open_po": awaiting,
        "parts_per_flag": flag_counts,
        "orders": orders,
        "not_ordered": held,
    }
    return f"<plan>\n{json.dumps(payload, indent=1, ensure_ascii=False)}\n</plan>\nWrite the SummaryOutput."
