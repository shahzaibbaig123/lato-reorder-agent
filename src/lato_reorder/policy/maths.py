"""Pure reorder maths. No I/O; every function is deterministic and unit-tested.

Fan-out f (how many bike models use a part) plays two roles:
  * consumption proxy: with no demand data, a part used by more models is used up faster;
  * impact measure:    a stockout of that part halts f models.
The tier carries impact (how much buffer to hold); cover carries urgency.
"""

import math
from enum import StrEnum


class Tier(StrEnum):
    T1_CRITICAL = "T1_CRITICAL"
    T2_SHARED = "T2_SHARED"
    T3_SINGLE = "T3_SINGLE"
    T0_ORPHAN = "T0_ORPHAN"


def tier_for(fan_out: int, catalogue_size: int, t1_min_share: float) -> Tier:
    if fan_out <= 0:
        return Tier.T0_ORPHAN
    if catalogue_size > 0 and fan_out / catalogue_size >= t1_min_share:
        return Tier.T1_CRITICAL
    if fan_out >= 2:
        return Tier.T2_SHARED
    return Tier.T3_SINGLE


def cycle_demand(k: float, units_per_bike: int, fan_out: int) -> float:
    """Assumed units consumed per review cycle: D = K x q x f."""
    return k * units_per_bike * fan_out


def reorder_point(demand: float, lead_time: float, safety_cycles: float) -> int:
    """ROP = ceil((LT + ss) x D). Reorder when inventory position <= ROP."""
    return math.ceil((lead_time + safety_cycles) * demand)


def order_up_to(demand: float, lead_time: float, review_period: float, safety_cycles: float) -> int:
    """OUT = ceil((LT + R + ss) x D). Orders top inventory position up to this level."""
    return math.ceil((lead_time + review_period + safety_cycles) * demand)


def round_to_lot(quantity: int, lot: int) -> int:
    if quantity <= 0:
        return 0
    return math.ceil(quantity / lot) * lot


def order_quantity(inventory_position: int, out: int, lot: int) -> int:
    """Q = ceil_lot(OUT - IP). Zero when already at or above OUT."""
    return round_to_lot(out - inventory_position, lot)
