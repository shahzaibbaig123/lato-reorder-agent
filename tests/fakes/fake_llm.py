"""Scripted stand-in for the LLM: returns canned structured outputs so the analyst pipeline can be tested
deterministically and for free."""

from collections.abc import Callable
from typing import Any

from lato_reorder.llm.analyst import LlmResult
from lato_reorder.llm.schemas import Echo, PartNarrative, SummaryOutput
from lato_reorder.plan import Concern, ConcernCode
from lato_reorder.policy.decision import EngineDecision
from lato_reorder.policy.rules import RuleId


def good_narrative(d: EngineDecision, **overrides: Any) -> PartNarrative:
    c = d.calc
    rop = c.reorder_point if c else 0
    fields: dict[str, Any] = {
        "part_key": d.facts.part_key,
        "echo": Echo(decision=d.decision, tier=d.tier, quantity=d.quantity, route=d.route),
        "cited_rules": [d.primary_rule],
        "justification": f"Used by {d.facts.fan_out} of {d.facts.catalogue_size} models; inventory position "
        f"{d.facts.inventory_position} against reorder point {rop}.",
        "quantity_explanation": f"Quantity {d.quantity} follows the order-up-to rule.",
        "concerns": [],
        "suggested_bom_qty": None,
    }
    fields.update(overrides)
    return PartNarrative(**fields)


class FakeCaller:
    """`script(decision_key, attempt)` returns a PartNarrative, an error string, or None (= good answer)."""

    model = "fake-model"

    def __init__(self, decisions: dict[str, EngineDecision], script: Callable[[str, int], Any] | None = None):
        self.decisions = decisions
        self.script = script or (lambda key, attempt: None)
        self.calls: list[tuple[str, int]] = []
        self._attempts: dict[str, int] = {}

    async def __call__(
        self, *, system: str, user: str, output_format: type, max_tokens: int
    ) -> LlmResult[Any]:
        if output_format is SummaryOutput:
            self.calls.append(("__summary__", 1))
            return LlmResult(
                SummaryOutput(
                    headline="Orders ready for review.",
                    top_risks=[],
                    capital_notes=[],
                ),
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        key = next(k for k in self.decisions if f'"part_key": "{k}"' in user)
        attempt = self._attempts[key] = self._attempts.get(key, 0) + 1
        self.calls.append((key, attempt))
        out = self.script(key, attempt)
        usage = {"input_tokens": 100, "output_tokens": 20}
        if out is None:
            return LlmResult(good_narrative(self.decisions[key]), usage=usage)
        if isinstance(out, str):
            return LlmResult(None, out, usage)
        return LlmResult(out, usage=usage)


def concern(code: ConcernCode, evidence: str, escalate: bool = True) -> Concern:
    return Concern(code=code, evidence=evidence, escalate=escalate)


__all__ = ["FakeCaller", "RuleId", "concern", "good_narrative"]
