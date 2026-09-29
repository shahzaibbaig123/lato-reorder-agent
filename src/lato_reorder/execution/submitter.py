"""Submit approved PO intents to SAP exactly once.

Per intent: expiry check -> pre-flight on fresh inventory -> APPROVED->SUBMITTING committed (compare-and-set)
-> RaiseSAPPurchaseOrder (no retries once sent) -> classify the reply -> record the outcome.
A file lock allows one submitter per ledger; a circuit breaker stops after repeated bad outcomes.
"""

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lato_reorder.domain.ingest import build_snapshot
from lato_reorder.domain.models import Snapshot
from lato_reorder.execution.confirmation import classify
from lato_reorder.execution.preflight import preflight
from lato_reorder.ledger.repo import Intent, Ledger, LedgerError, StateConflict
from lato_reorder.ledger.states import IntentStatus
from lato_reorder.mcp_gateway.client import McpAuthError, McpClient, McpError, McpTransportError
from lato_reorder.mcp_gateway.inventory import fetch_raw
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.decision import LedgerState
from lato_reorder.settings import Mode

S = IntentStatus
WRITE_TIMEOUT = 30.0
BREAKER_THRESHOLD = 2
PO_TOOL = "RaiseSAPPurchaseOrder"


class SubmitRefused(Exception):
    pass


@dataclass
class LineResult:
    intent_id: str
    item_name: str
    quantity: int
    status: str
    po_number: str | None = None
    message: str = ""


@dataclass
class SubmitReport:
    results: list[LineResult] = field(default_factory=list)
    breaker_tripped: bool = False
    write_calls: int = 0

    def count(self, status: str) -> int:
        return sum(r.status == status for r in self.results)


@contextmanager
def submit_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SubmitRefused("another submitter is running") from exc
        yield
    finally:
        os.close(fd)


class Submitter:
    def __init__(
        self,
        ledger: Ledger,
        policy: PolicyConfig,
        client_factory: Callable[[], McpClient],
        *,
        mode: Mode,
        submit_enabled: bool = False,
        lock_path: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.ledger = ledger
        self.policy = policy
        self.client_factory = client_factory
        self.mode = mode
        self.submit_enabled = submit_enabled
        self.lock_path = lock_path
        self.clock = clock

    def _guard(self, execute: bool) -> None:
        if self.mode is Mode.PRODUCTION and not (self.submit_enabled and execute):
            raise SubmitRefused("PRODUCTION needs SUBMIT_ENABLED=true and an explicit execute flag")

    def _ordered(self, intents: list[Intent]) -> list[Intent]:
        """Most urgent first, using each intent's priority from its plan."""
        priority: dict[str, float] = {}
        for run_id in {i.run_id for i in intents}:
            try:
                plan = self.ledger.run_plan(run_id)
            except LedgerError:
                continue
            for r in plan.recommendations:
                priority[r.decision.facts.part_key] = r.decision.priority
        return sorted(intents, key=lambda i: -priority.get(i.part_key, 0.0))

    async def submit(self, intent_ids: list[str] | None = None, *, execute: bool = False) -> SubmitReport:
        self._guard(execute)
        report = SubmitReport()
        with submit_lock(self.lock_path):
            approved = self.ledger.intents([S.APPROVED])
            if intent_ids is not None:
                approved = [i for i in approved if i.intent_id in set(intent_ids)]
            if not approved:
                return report
            async with self.client_factory() as client:
                products, parts = await fetch_raw(client)  # contract check + fresh inventory
                fresh = build_snapshot(products, parts)
                consecutive_bad = 0
                for intent in self._ordered(approved):
                    if consecutive_bad >= BREAKER_THRESHOLD:
                        report.breaker_tripped = True
                        report.results.append(
                            LineResult(
                                intent.intent_id,
                                intent.item_name,
                                intent.quantity,
                                "NOT_ATTEMPTED",
                                message="circuit breaker open",
                            )
                        )
                        continue
                    result = await self._submit_one(client, intent, fresh, report)
                    report.results.append(result)
                    if result.status in (S.UNKNOWN, S.FAILED, "AUTH_FAILED"):
                        consecutive_bad += 1
                        if result.status == "AUTH_FAILED":
                            consecutive_bad = BREAKER_THRESHOLD
                    elif result.status in (S.SUBMITTED, S.SUBMITTED_MISMATCH):
                        consecutive_bad = 0
        return report

    async def _submit_one(
        self, client: McpClient, intent: Intent, fresh: Snapshot, report: SubmitReport
    ) -> LineResult:
        now = self.clock()

        def line(status: str, po_number: str | None = None, message: str = "") -> LineResult:
            return LineResult(
                intent.intent_id, intent.item_name, intent.quantity, str(status), po_number, message
            )

        if intent.approved_expires_at and datetime.fromisoformat(intent.approved_expires_at) < now:
            self.ledger.expire_approval(intent.intent_id, now)
            return line(S.EXPIRED, message="approval expired; re-approve")

        view = self.ledger.ledger_view(now, exclude_intent=intent.intent_id)
        check = preflight(intent, fresh, self.policy, view.get(intent.part_key, LedgerState()), now)
        if not check.ok:
            self.ledger.mark_stale(
                intent.intent_id, now, {"reason": check.reason, "fresh_quantity": check.fresh_quantity}
            )
            return line(S.STALE, message=check.reason)

        try:
            self.ledger.begin_submit(intent.intent_id, now, on_hand=check.on_hand)
        except StateConflict:
            return line("SKIPPED", message="already being submitted by someone else")
        except LedgerError as exc:
            return line("SKIPPED", message=str(exc))

        report.write_calls += 1
        try:
            reply = await client.call_tool(
                PO_TOOL,
                {"ItemName": intent.item_name, "Quantity": int(intent.quantity)},
                timeout=WRITE_TIMEOUT,
            )
        except McpTransportError as exc:
            if not exc.request_sent:
                self.ledger.release_unsent(intent.intent_id, self.clock(), str(exc))
                return line(S.APPROVED, message="not sent (connection failed); will retry on the next submit")
            self.ledger.finish_submit(intent.intent_id, S.UNKNOWN, self.clock(), raw_response=str(exc))
            return line(S.UNKNOWN, message="request may have reached SAP; reconcile before re-ordering")
        except McpAuthError as exc:
            # Rejected at the auth layer: the tool never ran. Safe to release, but stop the batch.
            self.ledger.release_unsent(intent.intent_id, self.clock(), str(exc))
            return line("AUTH_FAILED", message="MCP authentication failed; stopping")
        except McpError as exc:
            self.ledger.finish_submit(intent.intent_id, S.UNKNOWN, self.clock(), raw_response=str(exc))
            return line(S.UNKNOWN, message=f"server error: {exc}")

        outcome = classify(reply.text, reply.is_error, intent.item_name, intent.supplier, intent.quantity)
        self.ledger.finish_submit(
            intent.intent_id,
            outcome.status,
            self.clock(),
            raw_response=reply.text,
            sap_po_number=outcome.po_number,
            parsed_quantity=outcome.parsed_quantity,
            detail={"notes": outcome.notes} if outcome.notes else None,
        )
        return line(
            outcome.status, po_number=outcome.po_number, message="; ".join(outcome.notes) or reply.text
        )
