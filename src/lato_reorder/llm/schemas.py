"""Structured-output schemas for the LLM analyst (constrained decoding via messages.parse).

Enums (RuleId, Tier, Decision, Route, ConcernCode) are part of the schema, so the model cannot cite a
rule or raise a kind of concern that does not exist. Every field is required (nullable where optional)
and extra fields are forbidden. Length limits are enforced client-side by Pydantic.
"""

from pydantic import BaseModel, ConfigDict, Field

from lato_reorder.plan import Concern
from lato_reorder.policy.maths import Tier
from lato_reorder.policy.rules import Decision, Route, RuleId


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Echo(_Strict):
    """The engine's verdict, copied back. Any difference is a violation (the LLM never decides)."""

    decision: Decision
    tier: Tier | None
    quantity: int
    route: Route


class PartNarrative(_Strict):
    part_key: str
    echo: Echo
    cited_rules: list[RuleId]
    justification: str = Field(max_length=500)
    quantity_explanation: str = Field(max_length=400)
    concerns: list[Concern]
    suggested_bom_qty: int | None


class SummaryOutput(_Strict):
    headline: str = Field(max_length=300)
    top_risks: list[str]
    capital_notes: list[str]
