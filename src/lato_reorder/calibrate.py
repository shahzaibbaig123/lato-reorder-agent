"""Calibration report: run the reorder maths over a real snapshot under candidate settings,
so a human (procurement) can choose and freeze the policy parameters. Read-only; never orders.
"""

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lato_reorder.domain.models import Part, Snapshot
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.maths import (
    Tier,
    cycle_demand,
    order_quantity,
    order_up_to,
    reorder_point,
    tier_for,
)
from lato_reorder.policy.rules import BOM_SUSPECT_PATTERN as BOM_SUSPECT

K_CANDIDATES = (2, 3, 5, 10)


@dataclass(frozen=True)
class Line:
    part: Part
    tier: Tier
    units_per_bike: int
    demand: float
    rop: int
    out: int
    triggered: bool
    quantity: int
    needs_human: bool
    blocked: bool


@dataclass(frozen=True)
class Scenario:
    k: float
    lot_mode: str
    bom: str  # "all 1" | "wheels/pedals 2"
    lines: tuple[Line, ...]

    @property
    def triggered(self) -> list[Line]:
        return [ln for ln in self.lines if ln.triggered]

    def summary(self) -> dict[str, object]:
        trig = self.triggered
        return {
            "K": self.k,
            "lot": self.lot_mode,
            "units/bike": self.bom,
            "reorder": f"{len(trig)}/{len(self.lines)}",
            "critical": sum(ln.tier is Tier.T1_CRITICAL for ln in trig),
            "units": sum(ln.quantity for ln in trig),
            "max Q": max((ln.quantity for ln in trig), default=0),
            "to human": sum(ln.needs_human for ln in trig),
            "blocked": sum(ln.blocked for ln in trig),
        }


def bom_suspects(snapshot: Snapshot) -> list[str]:
    return [p.item_name for p in snapshot.parts if BOM_SUSPECT.search(p.item_name)]


def simulate(
    snapshot: Snapshot, policy: PolicyConfig, k: float, lot_mode: str, bom: dict[str, int]
) -> Scenario:
    n = snapshot.catalogue_size
    lines = []
    for part in snapshot.parts:
        f = len(part.used_by)
        tier = tier_for(f, n, policy.tiers.t1_min_share)
        q = bom.get(part.item_name, 1)
        if tier is Tier.T0_ORPHAN:
            lines.append(Line(part, tier, q, 0, 0, 0, False, 0, False, False))
            continue
        d = cycle_demand(k, q, f)
        ss = policy.safety_cycles[tier]
        rop = reorder_point(d, policy.demand.lead_time_cycles, ss)
        out = order_up_to(d, policy.demand.lead_time_cycles, policy.demand.review_period_cycles, ss)
        lot = (part.per_crate or 1) if lot_mode == "crate" else 1
        triggered = part.stock <= rop  # calibration assumes nothing on order (inventory position = on hand)
        qty = order_quantity(part.stock, out, lot) if triggered else 0
        blocked = qty > policy.routing.hard_max_units_per_po
        needs_human = triggered and (
            tier is Tier.T1_CRITICAL
            or part.stock == 0
            or qty > policy.routing.auto_max_units_per_po
            or (q == 1 and bool(BOM_SUSPECT.search(part.item_name)))
        )
        lines.append(Line(part, tier, q, d, rop, out, triggered, qty, needs_human, blocked))
    return Scenario(k, lot_mode, "wheels/pedals 2" if bom else "all 1", tuple(lines))


def k_anchors(snapshot: Snapshot, bom: dict[str, int]) -> dict[str, float]:
    """Two independent readings of 'builds per model per cycle' from the data itself."""
    cover = [p.stock / (len(p.used_by) * bom.get(p.item_name, 1)) for p in snapshot.parts if p.used_by]
    return {
        "median bike stock (bikes held per model)": statistics.median(p.stock for p in snapshot.products),
        "median part cover per model / 3 (parts hold ~3 cycles)": round(statistics.median(cover) / 3, 2),
    }


def _table(rows: Sequence[Mapping[str, object]]) -> str:
    if not rows:
        return "_none_\n"
    head = list(rows[0])
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(r[h]) for h in head) + " |" for r in rows]
    return "\n".join(out) + "\n"


def build_report(snapshot: Snapshot, policy: PolicyConfig) -> str:
    n = snapshot.catalogue_size
    suspects = bom_suspects(snapshot)
    doubled = {name: 2 for name in suspects}

    tiers = Counter(tier_for(len(p.used_by), n, policy.tiers.t1_min_share) for p in snapshot.parts)
    t1_frac = tiers[Tier.T1_CRITICAL] / len(snapshot.parts)
    lo, hi = policy.tiers.t1_target_fraction
    fan_out = Counter(len(p.used_by) for p in snapshot.parts)
    fg = sorted(p.stock for p in snapshot.products)
    fg_p25 = statistics.quantiles(fg, n=4)[0]

    parts_rows = [
        {
            "part": p.item_name,
            "stock": p.stock,
            "used by": len(p.used_by),
            "tier": tier_for(len(p.used_by), n, policy.tiers.t1_min_share).value,
            "supplier": p.supplier,
            "crate": p.per_crate,
            "stock / model": round(p.stock / len(p.used_by), 1),
        }
        for p in sorted(snapshot.parts, key=lambda p: (-len(p.used_by), p.stock))
    ]
    scenarios = [
        simulate(snapshot, policy, k, lot, bom)
        for bom in ({}, doubled)
        for lot in ("unit", "crate")
        for k in K_CANDIDATES
    ]
    t1_verdict = (
        "within target, keep t1_min_share" if lo <= t1_frac <= hi else "OUTSIDE target, review threshold"
    )
    suppliers = Counter(p.supplier for p in snapshot.parts)
    critical_suppliers = Counter(
        p.supplier
        for p in snapshot.parts
        if tier_for(len(p.used_by), n, policy.tiers.t1_min_share) is not Tier.T3_SINGLE
    )

    return "\n".join(
        [
            f"# Calibration report\n\nSnapshot `{snapshot.content_hash[:16]}` "
            f"fetched {snapshot.fetched_at:%Y-%m-%d %H:%M} UTC. Policy `{policy.version}`. "
            f"{n} bikes, {len(snapshot.parts)} parts, {len(snapshot.issues)} data issues.\n",
            "## 1. Tiers\n",
            f"Fan-out histogram (models using a part → number of parts): "
            f"{', '.join(f'{f}→{c}' for f, c in sorted(fan_out.items(), reverse=True))}.\n",
            _table([{"tier": t.value, "parts": tiers[t]} for t in Tier]),
            f"CRITICAL share = {t1_frac:.0%} (target {lo:.0%} to {hi:.0%}): **{t1_verdict}**.\n",
            "## 2. Parts\n",
            _table(parts_rows),
            "## 3. Finished-bike stock\n",
            f"Bike stock: {fg}. 25th percentile = {fg_p25:.0f} → suggested `fg_low` = {round(fg_p25)}. "
            f"Exposed bikes: {', '.join(p.title for p in snapshot.products if p.stock <= fg_p25)}.\n",
            "## 4. Units per bike (BOM)\n",
            f"Likely 2 per bike (name matches wheels/pedals): {', '.join(suspects)}. "
            "Default stays 1 until a human sets `bom_overrides.yaml`; "
            "until then these parts go to a human.\n",
            "## 5. Demand knob K (builds per model per cycle)\n",
            _table([{"anchor": k, "value": v} for k, v in k_anchors(snapshot, {}).items()]),
            "Sensitivity (nothing on order; routes approximate: human = critical, stockout, "
            f"> {policy.routing.auto_max_units_per_po} units, or unset wheel/pedal quantity; "
            f"blocked = > {policy.routing.hard_max_units_per_po} units):\n",
            _table([s.summary() for s in scenarios]),
            "## 6. Suppliers\n",
            _table(
                [
                    {"supplier": s, "parts": c, "shared/critical parts": critical_suppliers.get(s, 0)}
                    for s, c in suppliers.most_common()
                ]
            ),
            "## 7. Decisions needed\n",
            "1. K (builds per model per cycle).\n2. Lot mode: `unit` or full crates.\n"
            "3. Wheels/pedals units per bike.\n4. `auto_max_units_per_po` and `hard_max_units_per_po`.\n"
            "5. `fg_low`.\n",
        ]
    )
