"""Deterministic explanations built only from engine output.

Used when the LLM is off (`--llm none`), unavailable, or its output fails validation. Every number
comes straight from the decision, so a template explanation can never contradict the engine.
"""

from lato_reorder.policy.decision import EngineDecision
from lato_reorder.policy.maths import Tier
from lato_reorder.policy.rules import RULE_DESCRIPTIONS, Decision, Flag, RuleId

TIER_LABEL = {
    Tier.T1_CRITICAL: "CRITICAL",
    Tier.T2_SHARED: "SHARED",
    Tier.T3_SINGLE: "SINGLE-MODEL",
    Tier.T0_ORPHAN: "ORPHAN",
}


def _impact(d: EngineDecision) -> str:
    f = d.facts
    label = TIER_LABEL[d.tier] if d.tier else "UNTIERED"
    return f"{label} part used by {f.fan_out} of {f.catalogue_size} bikes"


def explain(d: EngineDecision) -> tuple[str, str]:
    """Return (justification, quantity_explanation)."""
    f, c = d.facts, d.calc
    if d.decision is Decision.BLOCKED:
        why = RULE_DESCRIPTIONS[d.primary_rule]
        detail = "; ".join(f.data_issues) or "an earlier PO for this part needs reconciling"
        return f"Blocked: {why.split(': ', 1)[-1]}. {detail}.", "No quantity until the problem is resolved."
    if d.tier is Tier.T0_ORPHAN or c is None:
        return (
            f"No order: no bike uses {f.item_name}; {f.on_hand} units on hand are not needed.",
            "Orphan parts are never reordered.",
        )

    position = (
        f"inventory position {f.inventory_position}"
        + (f" ({f.on_hand} on hand + {f.on_order} on order)" if f.on_order else "")
        + f" vs reorder point {c.reorder_point}, {c.cover_before:g} cycles of cover"
    )
    if d.decision is Decision.REORDER:
        lead = "Reorder now, out of stock" if d.primary_rule is RuleId.TRIG_STOCKOUT else "Reorder"
        justification = f"{lead}: {_impact(d)}; {position}."
        qty = (
            f"Order {d.quantity} = order-up-to {c.order_up_to} - inventory position {f.inventory_position}"
            + (f", rounded up to lots of {c.lot}" if c.lot > 1 else "")
            + f". Capped by the {TIER_LABEL[d.tier] if d.tier else ''} order-up-to level; "
            f"lifts cover to {c.cover_after:g} cycles."
        )
        return justification, qty

    held = {
        RuleId.HOLD_ABOVE_ROP: "No order",
        RuleId.HOLD_OPEN_PO: "No order, already on order",
        RuleId.HOLD_OPEN_PO_INSUFFICIENT: "No order, but check the open PO",
    }.get(d.primary_rule, "No order")
    justification = f"{held}: {_impact(d)}; {position}."
    if d.primary_rule is RuleId.HOLD_OPEN_PO_INSUFFICIENT:
        qty = (
            "Below the reorder point while a PO is open. No second PO is raised; chase or amend the open one."
        )
    elif Flag.AWAITING_DELIVERY in d.flags:
        qty = "Nothing on hand but the open PO covers it. Chase the supplier."
    else:
        qty = f"Would trigger when inventory position falls to {c.reorder_point} or below."
    return justification, qty
