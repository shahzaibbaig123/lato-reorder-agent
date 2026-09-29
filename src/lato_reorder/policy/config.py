"""Policy configuration: loaded from config/policy.yaml and config/bom_overrides.yaml."""

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from lato_reorder.policy.maths import Tier


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TierConfig(_Frozen):
    t1_min_share: float = Field(0.5, gt=0, le=1)
    t1_target_fraction: tuple[float, float] = (0.15, 0.35)


class DemandConfig(_Frozen):
    k_builds_per_model_per_cycle: float = Field(10, gt=0)
    lead_time_cycles: float = Field(1, ge=0)
    review_period_cycles: float = Field(1, gt=0)


class OrderingConfig(_Frozen):
    lot_mode: Literal["unit", "crate"] = "unit"


class RoutingConfig(_Frozen):
    auto_max_units_per_po: int = Field(100, gt=0)
    hard_max_units_per_po: int = Field(1000, gt=0)
    auto_budget_pos_per_run: int = Field(5, ge=0)
    auto_budget_units_per_run: int = Field(300, ge=0)


class FinishedGoodsConfig(_Frozen):
    fg_low: int | None = None


class PolicyConfig(_Frozen):
    version: str
    calibrated_on: str | None = None
    approved_by: str | None = None
    tiers: TierConfig = TierConfig()
    demand: DemandConfig = DemandConfig()
    safety_cycles: dict[Tier, float] = {Tier.T1_CRITICAL: 1.0, Tier.T2_SHARED: 0.5, Tier.T3_SINGLE: 0.0}
    ordering: OrderingConfig = OrderingConfig()
    routing: RoutingConfig = RoutingConfig()
    finished_goods: FinishedGoodsConfig = FinishedGoodsConfig()
    units_per_bike: dict[str, int] = {}  # from bom_overrides.yaml

    def units_for(self, item_name: str) -> int:
        return self.units_per_bike.get(item_name, 1)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:16]


def load_policy(config_dir: Path) -> PolicyConfig:
    raw = yaml.safe_load((config_dir / "policy.yaml").read_text())
    bom_path = config_dir / "bom_overrides.yaml"
    bom = yaml.safe_load(bom_path.read_text()) if bom_path.exists() else {}
    raw["units_per_bike"] = (bom or {}).get("units_per_bike") or {}
    return PolicyConfig.model_validate(raw)
