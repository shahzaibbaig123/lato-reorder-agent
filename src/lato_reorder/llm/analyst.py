"""LLM Reorder Analyst: explains each engine decision and flags what a human should double-check.

Flow per part: call -> validate (V1-V6) -> on violation, one retry with the violations fed back -> if still
invalid, use the deterministic template and route the order to a human. Concerns can only escalate:
final_route = max(engine route, HUMAN if a concern asks for it). The analyst never sees tools or
credentials and nothing it returns is used as a quantity, a route or an ItemName.
"""

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from lato_reorder.domain.models import Snapshot
from lato_reorder.llm.prompt import (
    part_user_message,
    prompt_hash,
    render_summary_prompt,
    render_system_prompt,
    summary_user_message,
)
from lato_reorder.llm.schemas import PartNarrative, SummaryOutput
from lato_reorder.llm.validators import validate_part, validate_summary
from lato_reorder.plan import (
    Narrative,
    Recommendation,
    ReorderPlan,
    RunSummary,
    compute_totals,
    sort_recommendations,
)
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.rules import Decision, Route, RuleId, strictest

T = TypeVar("T", bound=BaseModel)

# Models that still accept `temperature` (it moved to extra_body in SDK 1.x; newer models reject it).
TEMPERATURE_MODELS = (
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-5",
    "claude-opus-4-6",
)


@dataclass
class LlmResult[M: BaseModel]:
    parsed: M | None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class LlmCaller(Protocol):
    model: str

    async def __call__(
        self, *, system: str, user: str, output_format: type[T], max_tokens: int
    ) -> LlmResult[T]: ...


class AnthropicCaller:
    """Structured-output call to the LLM. Never raises: failures come back as LlmResult.error.

    Two time limits: the HTTP timeout per request, and a wall-clock deadline around the whole call
    (SDK retries included) that does not depend on the HTTP stack behaving."""

    def __init__(
        self, client: anthropic.AsyncAnthropic, model: str, *, timeout: float = 30.0, deadline: float = 75.0
    ) -> None:
        self.client, self.model, self.timeout, self.deadline = client, model, timeout, deadline

    async def __call__(
        self, *, system: str, user: str, output_format: type[T], max_tokens: int
    ) -> LlmResult[T]:
        extra: dict[str, Any] = {"temperature": 0} if self.model.startswith(TEMPERATURE_MODELS) else {}
        try:
            async with asyncio.timeout(self.deadline):
                resp = await self.client.messages.parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=output_format,
                    timeout=self.timeout,
                    extra_body=extra or None,
                )
        except TimeoutError:
            return LlmResult(None, f"API no answer within {self.deadline:.0f}s")
        except (ValidationError, ValueError) as exc:
            return LlmResult(None, f"V1 schema: {str(exc)[:300]}")
        except anthropic.APIError as exc:
            return LlmResult(None, f"API {type(exc).__name__}: {str(exc)[:200]}")
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        if resp.stop_reason in ("refusal", "max_tokens"):
            return LlmResult(None, f"stop_reason={resp.stop_reason}", usage)
        parsed = resp.parsed_output
        if parsed is None:
            return LlmResult(None, "V1 no parsed output", usage)
        return LlmResult(parsed, None, usage)


@dataclass
class PartReview:
    rec: Recommendation
    attempts: int
    first_attempt_violations: list[str]
    usage: Counter[str]


class Analyst:
    def __init__(
        self,
        caller: LlmCaller,
        policy: PolicyConfig,
        *,
        concurrency: int = 5,
        max_tokens: int = 1024,
        review_required_for_auto: bool = True,
        summary: bool = True,
        plan_deadline: float = 240.0,
    ) -> None:
        self.caller = caller
        self.system = render_system_prompt(policy)
        self.summary_system = render_summary_prompt()
        self.prompt_hash = prompt_hash(self.system, self.summary_system)
        self.semaphore = asyncio.Semaphore(concurrency)
        self.max_tokens = max_tokens
        self.review_required_for_auto = review_required_for_auto
        self.with_summary = summary
        self.plan_deadline = plan_deadline
        self.last_reviews: list[PartReview] = []
        self.summary_violations: list[list[str]] = []

    # ------------------------------------------------------------------ per part

    async def review_part(self, rec: Recommendation) -> PartReview:
        d = rec.decision
        base = part_user_message(d)
        usage: Counter[str] = Counter()
        first: list[str] = []
        user = base
        violations: list[str] = []
        narrative: PartNarrative | None = None
        attempts = 0
        for attempt in (1, 2):
            attempts = attempt
            async with self.semaphore:
                result = await self.caller(
                    system=self.system, user=user, output_format=PartNarrative, max_tokens=self.max_tokens
                )
            usage.update(result.usage)
            if result.parsed is None:
                violations = [result.error or "V1 no output"]
            else:
                violations = validate_part(result.parsed, d, base)
            if attempt == 1:
                first = list(violations)
            if not violations:
                narrative = result.parsed
                break
            if result.error and result.error.startswith(("API", "stop_reason")):
                break  # retrying a transport failure or refusal with feedback does not help
            user = (
                f"{base}\n\nYour previous answer was rejected for these reasons:\n- "
                + "\n- ".join(violations)
                + "\nReturn a corrected PartNarrative. Copy the echo fields exactly and use only numbers "
                "from the input. If you are unsure about a concern, leave it out: "
                "concerns [] is a good answer."
            )
        return PartReview(self._merge(rec, narrative, violations), attempts, first, usage)

    def _merge(self, rec: Recommendation, n: PartNarrative | None, violations: list[str]) -> Recommendation:
        d = rec.decision
        is_order = d.decision is Decision.REORDER
        if n is None:
            # No valid review: keep the template, and never let an unreviewed order go through automatically.
            unreviewed_auto = is_order and self.review_required_for_auto and rec.final_route is Route.AUTO
            return rec.model_copy(
                update={
                    "final_route": Route.HUMAN if unreviewed_auto else rec.final_route,
                    "review_reasons": [RuleId.RTE_H_NO_REVIEW] if unreviewed_auto else [],
                    "violations": violations,
                }
            )
        escalate = [c for c in n.concerns if c.escalate]
        route = rec.final_route
        reasons: list[RuleId] = []
        if escalate and is_order and rec.final_route in (Route.AUTO, Route.HUMAN):
            route = strictest(rec.final_route, Route.HUMAN)
            reasons = [RuleId.RTE_H_LLM_CONCERN]
        return rec.model_copy(
            update={
                "narrative": Narrative(
                    justification=n.justification, quantity_explanation=n.quantity_explanation, source="LLM"
                ),
                "concerns": list(n.concerns),
                "final_route": route,
                "review_reasons": reasons,
                "violations": [],
            }
        )

    # ------------------------------------------------------------------ whole plan

    async def narrate_plan(self, plan: ReorderPlan, snapshot: Snapshot | None = None) -> ReorderPlan:
        """Review every part within plan_deadline. The LLM is optional: a part whose review has not
        finished (or failed unexpectedly) keeps its template explanation, and an unreviewed AUTO order
        goes to a human, so a slow or unavailable model never holds up a run."""
        tasks = [asyncio.create_task(self.review_part(r)) for r in plan.recommendations]
        _, late = await asyncio.wait(tasks, timeout=self.plan_deadline)
        for t in late:
            t.cancel()
        await asyncio.gather(*late, return_exceptions=True)
        reviews: list[PartReview] = []
        for rec, task in zip(plan.recommendations, tasks, strict=True):
            if task.cancelled() or task.exception() is not None:
                why = "API narration deadline exceeded" if task.cancelled() else f"API {task.exception()!r}"
                reviews.append(PartReview(self._merge(rec, None, [why]), 0, [why], Counter()))
            else:
                reviews.append(task.result())
        self.last_reviews = reviews
        recs = sort_recommendations([r.rec for r in reviews])
        usage: Counter[str] = Counter()
        for r in reviews:
            usage.update(r.usage)
        reviewed = plan.model_copy(update={"recommendations": recs, "totals": compute_totals(recs)})
        summary = None
        self.summary_violations = []
        if self.with_summary and not late:
            summary, summary_usage = await self.summarize(reviewed)
            usage.update(summary_usage)
        usage["llm_calls"] = sum(r.attempts for r in reviews) + len(self.summary_violations)
        usage["fallbacks"] = sum(r.rec.narrative.source == "TEMPLATE" for r in reviews)
        return reviewed.model_copy(
            update={
                "llm": f"{self.caller.model} (prompt {self.prompt_hash})",
                "summary": summary,
                "llm_usage": dict(usage),
            }
        )

    async def summarize(self, plan: ReorderPlan) -> tuple[RunSummary | None, Counter[str]]:
        """Run briefing, same protocol as a part: validate, one retry with the violations fed back, and
        no briefing at all rather than a wrong one. Violations are kept for the audit and the evals."""
        base = summary_user_message(plan)
        user = base
        usage: Counter[str] = Counter()
        self.summary_violations = []
        for _ in (1, 2):
            async with self.semaphore:
                result = await self.caller(
                    system=self.summary_system,
                    user=user,
                    output_format=SummaryOutput,
                    max_tokens=self.max_tokens,
                )
            usage.update(result.usage)
            violations = (
                [result.error or "V1 no output"]
                if result.parsed is None
                else validate_summary(result.parsed, base)
            )
            self.summary_violations.append(violations)
            if result.parsed is not None and not violations:
                s = result.parsed
                return RunSummary(
                    headline=s.headline, top_risks=s.top_risks, capital_notes=s.capital_notes
                ), usage
            if result.error and result.error.startswith(("API", "stop_reason")):
                break
            user = (
                f"{base}\n\nYour previous answer was rejected for these reasons:\n- "
                + "\n- ".join(violations)
                + "\nReturn a corrected SummaryOutput. Use only numbers that appear in the plan, "
                "in digits or words; do not count or add anything up yourself."
            )
        return None, usage


def make_analyst(api_key: str, model: str, policy: PolicyConfig, **kwargs: Any) -> Analyst:
    # Client-wide limits so no call path can fall back to the SDK's 10-minute default read timeout.
    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=30.0, max_retries=1)
    return Analyst(AnthropicCaller(client, model), policy, **kwargs)


def narration_json(rec: Recommendation) -> str:
    """Compact audit view of what the analyst contributed to one line."""
    return json.dumps(
        {
            "part": rec.decision.facts.item_name,
            "source": rec.narrative.source,
            "concerns": [c.model_dump(mode="json") for c in rec.concerns],
            "review_reasons": [r.value for r in rec.review_reasons],
            "violations": rec.violations,
        }
    )
