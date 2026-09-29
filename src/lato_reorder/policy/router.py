"""Approval routing: who must sign off each order. Strictest route wins: NONE < AUTO < HUMAN < BLOCK."""

from collections.abc import Sequence

from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.decision import EngineDecision
from lato_reorder.policy.maths import Tier
from lato_reorder.policy.rules import ESCALATING_FLAGS, Decision, Route, RuleId


def line_route(d: EngineDecision, policy: PolicyConfig) -> tuple[Route, list[RuleId]]:
    if d.decision is Decision.BLOCKED:
        return Route.BLOCK, [d.primary_rule]
    if d.decision is Decision.NO_ORDER:
        return Route.NONE, []

    caps = policy.routing
    calc = d.calc
    invariant_ok = calc is not None and (
        calc.reorder_point < (d.facts.inventory_position or 0) + d.quantity <= calc.order_up_to + calc.lot - 1
    )
    if d.quantity > caps.hard_max_units_per_po or not invariant_ok:
        return Route.BLOCK, [RuleId.RTE_B_SIZE]

    reasons = []
    if d.tier is Tier.T1_CRITICAL:
        reasons.append(RuleId.RTE_H_TIER)
    if d.facts.on_hand == 0:
        reasons.append(RuleId.RTE_H_STOCKOUT)
    if d.quantity > caps.auto_max_units_per_po:
        reasons.append(RuleId.RTE_H_SIZE)
    if ESCALATING_FLAGS & set(d.flags):
        reasons.append(RuleId.RTE_H_FLAG)
    return (Route.HUMAN, reasons) if reasons else (Route.AUTO, [RuleId.RTE_AUTO])


def _with_route(d: EngineDecision, route: Route, reasons: Sequence[RuleId]) -> EngineDecision:
    rules = list(dict.fromkeys([*d.rules_applied, *reasons]))
    return d.model_copy(update={"route": route, "route_reasons": list(reasons), "rules_applied": rules})


def route_all(decisions: list[EngineDecision], policy: PolicyConfig) -> list[EngineDecision]:
    """Return routed, hashed decisions."""
    routed = [_with_route(d, *line_route(d, policy)) for d in decisions]

    # Auto budget: spend it on the highest-priority auto lines; the rest go to a human.
    budget_pos, budget_units = (
        policy.routing.auto_budget_pos_per_run,
        policy.routing.auto_budget_units_per_run,
    )
    used_pos = used_units = 0
    over_budget: set[str] = set()
    for d in sorted((d for d in routed if d.route is Route.AUTO), key=lambda d: -d.priority):
        if used_pos + 1 > budget_pos or used_units + d.quantity > budget_units:
            over_budget.add(d.facts.part_key)
        else:
            used_pos, used_units = used_pos + 1, used_units + d.quantity
    routed = [
        _with_route(d, Route.HUMAN, [RuleId.RTE_H_BUDGET]) if d.facts.part_key in over_budget else d
        for d in routed
    ]

    return [d.with_hash(policy.content_hash) for d in routed]
