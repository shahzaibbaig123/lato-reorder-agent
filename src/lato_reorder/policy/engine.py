"""Deterministic reorder engine: one EngineDecision per part. Pure: no I/O, no LLM, no clock.

Evaluation order (first match is the primary rule):
  1. invalid data                      -> BLOCKED  (RTE-B-DATA / RTE-B-NAME)
  2. used by no model                  -> HOLD-ORPHAN
  3. earlier PO with unknown outcome   -> BLOCKED  (RTE-B-INFLIGHT)
  4. IP <= ROP but a PO is already open -> HOLD-OPEN-PO-INSUFFICIENT (flag; never a second PO)
  5. nothing on hand and IP <= ROP     -> TRIG-STOCKOUT
  6. IP <= ROP                         -> TRIG-ROP
  7. on hand <= ROP < IP               -> HOLD-OPEN-PO
  8. otherwise                         -> HOLD-ABOVE-ROP
"""

import math
from collections.abc import Mapping
from datetime import datetime

from lato_reorder.domain.models import DataIssue, Part, Snapshot
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.decision import Calc, DerivationStep, EngineDecision, LedgerState, PartFacts
from lato_reorder.policy.maths import (
    Tier,
    cycle_demand,
    order_up_to,
    reorder_point,
    round_to_lot,
    tier_for,
)
from lato_reorder.policy.rules import (
    BOM_SUSPECT_PATTERN,
    INSTRUCTION_LIKE_PATTERN,
    Decision,
    Flag,
    RuleId,
)

TIER_RULE = {
    Tier.T1_CRITICAL: RuleId.TIER_1,
    Tier.T2_SHARED: RuleId.TIER_2,
    Tier.T3_SINGLE: RuleId.TIER_3,
    Tier.T0_ORPHAN: RuleId.TIER_0,
}
WARN_ISSUE_FLAGS = {
    "UNMATCHED_MODEL": Flag.UNMATCHED_MODEL,
    "NEAR_DUPLICATE_NAME": Flag.NEAR_DUPLICATE_NAME,
    "DUPLICATE_PRODUCT_REF": Flag.DUPLICATE_PRODUCT_REF,
}
NAME_ISSUES = {"NAME_COLLISION", "NAME_CONTROL_CHARS"}


def _fmt(x: float) -> str:
    return f"{x:g}"


def priority(share: float, on_hand: int, ip: int, rop: int, fg_exposed: bool) -> float:
    """Ranks work only (queue order, auto budget, submit order); tiers make the decisions."""
    urgency = 1.0 if on_hand == 0 else (min(max(1 - ip / rop, 0.0), 1.0) if rop > 0 else 0.0)
    return round(share * urgency * (1.5 if fg_exposed else 1.0), 4)


def decide_blocked(
    name: str, issues: list[DataIssue], part: Part | None, policy: PolicyConfig
) -> EngineDecision:
    codes = [i.code for i in issues]
    rule = RuleId.RTE_B_NAME if set(codes) & NAME_ISSUES else RuleId.RTE_B_DATA
    facts = PartFacts(
        part_key=part.key if part else name,
        item_name=name,
        supplier=part.supplier if part else None,
        on_hand=part.stock if part else None,
        inventory_position=None,
        data_issues=[f"{i.code}: {i.detail}" for i in issues],
    )
    return EngineDecision(
        facts=facts,
        calc=None,
        tier=None,
        decision=Decision.BLOCKED,
        quantity=0,
        primary_rule=rule,
        rules_applied=[rule],
        flags=[],
        priority=0,
        policy_version=policy.version,
    )


def decide_part(
    part: Part,
    snapshot: Snapshot,
    policy: PolicyConfig,
    ledger: LedgerState,
    now: datetime,
) -> EngineDecision:
    n = snapshot.catalogue_size
    f = len(part.used_by)
    share = f / n if n else 0.0
    tier = tier_for(f, n, policy.tiers.t1_min_share)
    q = policy.units_for(part.item_name)
    oh, oo = part.stock, ledger.on_order
    ip = oh + oo

    fg_by_model = {m: (p.stock if (p := snapshot.product(m)) else None) for m in part.used_by}
    fg_low = policy.finished_goods.fg_low
    exposed = [m for m, s in fg_by_model.items() if fg_low is not None and s is not None and s <= fg_low]

    flags: set[Flag] = set()
    part_issues = [i for i in snapshot.issues if i.subject == part.item_name]
    flags |= {WARN_ISSUE_FLAGS[i.code] for i in part_issues if i.code in WARN_ISSUE_FLAGS}
    if exposed and len(exposed) >= math.ceil(f / 2):
        flags.add(Flag.FG_EXPOSED)
    if BOM_SUSPECT_PATTERN.search(part.item_name) and part.item_name not in policy.units_per_bike:
        flags.add(Flag.BOM_SUSPECT)
    if INSTRUCTION_LIKE_PATTERN.search(f"{part.item_name} {part.description} {part.supplier}"):
        flags.add(Flag.INSTRUCTION_LIKE_TEXT)
    if ledger.rejected_recently:
        flags.add(Flag.PREVIOUSLY_REJECTED)

    facts = PartFacts(
        part_key=part.key,
        item_name=part.item_name,
        supplier=part.supplier,
        description=part.description,
        on_hand=oh,
        on_order=oo,
        inventory_position=ip,
        used_by=list(part.used_by),
        fan_out=f,
        catalogue_size=n,
        share=round(share, 4),
        units_per_bike=q,
        units_source="OVERRIDE" if part.item_name in policy.units_per_bike else "DEFAULT",
        fg_by_model=fg_by_model,
        exposed_models=exposed,
        data_issues=[f"{i.code}: {i.detail}" for i in part_issues],
    )
    tier_rule = TIER_RULE[tier]

    def done(
        decision: Decision,
        primary: RuleId,
        qty: int = 0,
        calc: Calc | None = None,
        extra_rules: list[RuleId] | None = None,
        prio: float = 0.0,
    ) -> EngineDecision:
        return EngineDecision(
            facts=facts,
            calc=calc,
            tier=tier,
            decision=decision,
            quantity=qty,
            primary_rule=primary,
            rules_applied=[tier_rule, primary, *(extra_rules or [])],
            flags=sorted(flags),
            priority=prio,
            policy_version=policy.version,
        )

    if tier is Tier.T0_ORPHAN:
        if oh > 0:
            flags.add(Flag.ORPHAN_STOCK)
        return done(Decision.NO_ORDER, RuleId.HOLD_ORPHAN)

    d_cfg = policy.demand
    k = d_cfg.k_builds_per_model_per_cycle
    ss = policy.safety_cycles[tier]
    d = cycle_demand(k, q, f)
    rop = reorder_point(d, d_cfg.lead_time_cycles, ss)
    out = order_up_to(d, d_cfg.lead_time_cycles, d_cfg.review_period_cycles, ss)
    lot = (part.per_crate or 1) if policy.ordering.lot_mode == "crate" else 1
    steps = [
        DerivationStep(
            rule_id=tier_rule,
            expression=f"used by {f} of {n} bike models (share {share:.2f})",
            value=round(share, 4),
        ),
        DerivationStep(
            rule_id=None, expression=f"D = K x q x f = {_fmt(k)} x {q} x {f} = {_fmt(d)} units/cycle", value=d
        ),
        DerivationStep(
            rule_id=None,
            expression=f"ROP = ceil((LT + ss) x D) = ceil(({_fmt(d_cfg.lead_time_cycles)} "
            f"+ {_fmt(ss)}) x {_fmt(d)}) = {rop}",
            value=rop,
        ),
        DerivationStep(
            rule_id=None,
            expression=f"OUT = ceil((LT + R + ss) x D) = ceil(({_fmt(d_cfg.lead_time_cycles)}"
            f" + {_fmt(d_cfg.review_period_cycles)} + {_fmt(ss)}) x {_fmt(d)}) = {out}",
            value=out,
        ),
        DerivationStep(rule_id=None, expression=f"IP = on hand + on order = {oh} + {oo} = {ip}", value=ip),
    ]
    cover_before = round(ip / d, 2) if d else 0.0

    def calc(
        raw: int = 0, cover_after: float | None = None, extra: list[DerivationStep] | None = None
    ) -> Calc:
        return Calc(
            k=k,
            cycle_demand=d,
            safety_cycles=ss,
            reorder_point=rop,
            order_up_to=out,
            cover_before=cover_before,
            cover_after=cover_after,
            raw_quantity=raw,
            lot=lot,
            steps=steps + (extra or []),
        )

    prio = priority(share, oh, ip, rop, Flag.FG_EXPOSED in flags)
    if ip <= rop + 0.5 * d and ip > rop and tier in (Tier.T1_CRITICAL, Tier.T2_SHARED):
        flags.add(Flag.WATCH)
    if ip > 2 * out:
        flags.add(Flag.EXCESS)

    if ledger.inflight_unresolved:
        return done(Decision.BLOCKED, RuleId.RTE_B_INFLIGHT, calc=calc(), prio=prio)

    if ip <= rop and ledger.has_open_po:
        flags.add(Flag.OPEN_PO_INSUFFICIENT)
        return done(Decision.NO_ORDER, RuleId.HOLD_OPEN_PO_INSUFFICIENT, calc=calc(), prio=prio)

    if ip <= rop:
        trigger = RuleId.TRIG_STOCKOUT if oh == 0 else RuleId.TRIG_ROP
        raw = out - ip
        qty = round_to_lot(raw, lot)
        qty_rules = [RuleId.QTY_OUT] + ([RuleId.QTY_LOT] if lot > 1 else [])
        qty_steps = [
            DerivationStep(
                rule_id=RuleId.QTY_OUT, expression=f"Q = OUT - IP = {out} - {ip} = {raw}", value=raw
            )
        ]
        if lot > 1:
            qty_steps.append(
                DerivationStep(
                    rule_id=RuleId.QTY_LOT, expression=f"rounded up to lots of {lot} = {qty}", value=qty
                )
            )
        cover_after = round((ip + qty) / d, 2)
        return done(
            Decision.REORDER,
            trigger,
            qty=qty,
            calc=calc(raw, cover_after, qty_steps),
            extra_rules=qty_rules,
            prio=prio,
        )

    if oh <= rop:
        if oh == 0:
            flags.add(Flag.AWAITING_DELIVERY)
        return done(Decision.NO_ORDER, RuleId.HOLD_OPEN_PO, calc=calc(), prio=prio)
    return done(Decision.NO_ORDER, RuleId.HOLD_ABOVE_ROP, calc=calc(), prio=prio)


def decide_all(
    snapshot: Snapshot,
    policy: PolicyConfig,
    ledger: Mapping[str, LedgerState],
    now: datetime,
) -> list[EngineDecision]:
    """Decide every part in the snapshot, plus a BLOCKED line for every part with invalid data."""
    blocked = snapshot.blocked_part_names()
    decisions = []
    for part in snapshot.parts:
        if part.item_name in blocked:
            issues = [i for i in snapshot.issues if i.subject == part.item_name and i.severity == "BLOCK"]
            decisions.append(decide_blocked(part.item_name, issues, part, policy))
        else:
            decisions.append(decide_part(part, snapshot, policy, ledger.get(part.key, LedgerState()), now))
    known = {p.item_name for p in snapshot.parts}
    for name in sorted(blocked - known):
        issues = [i for i in snapshot.issues if i.subject == name and i.severity == "BLOCK"]
        decisions.append(decide_blocked(name, issues, None, policy))
    return decisions
