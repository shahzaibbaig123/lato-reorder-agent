"""SQLite PO ledger. Every state change is a compare-and-set (UPDATE ... WHERE status = expected)
inside a BEGIN IMMEDIATE transaction, logged to the append-only events table."""

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lato_reorder.domain.models import Snapshot
from lato_reorder.ledger.states import HAS_OPEN_PO, ON_ORDER, OPEN, UNRESOLVED, IntentStatus, can_transition
from lato_reorder.plan import ReorderPlan
from lato_reorder.policy.decision import LedgerState
from lato_reorder.policy.rules import Decision, Route

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
S = IntentStatus

APPROVAL_TTL = timedelta(hours=24)
PENDING_TTL = timedelta(days=7)
REJECTION_MEMORY = timedelta(days=7)
STALE_OPEN_PO_AGE = timedelta(days=21)
RECEIPT_FRACTION = 0.8


class LedgerError(Exception):
    pass


class StateConflict(LedgerError):
    """The intent was not in the expected state (someone else acted first). Nothing was changed."""


def binding_hash(part_key: str, item_name: str, quantity: int, decision_hash: str) -> str:
    payload = json.dumps([part_key, item_name, quantity, decision_hash])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Intent:
    intent_id: str
    run_id: str
    part_key: str
    item_name: str
    supplier: str | None
    quantity: int
    engine_quantity: int
    route: str
    decision_hash: str
    status: IntentStatus
    created_at: str
    updated_at: str
    on_hand_at_plan: int | None
    on_hand_at_submit: int | None
    approved_expires_at: str | None
    binding_hash: str | None
    sap_po_number: str | None
    parsed_quantity: int | None
    raw_response: str | None
    submitted_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Intent":
        data = dict(row)
        data["status"] = IntentStatus(data["status"])
        return cls(**data)


@dataclass(frozen=True)
class PersistResult:
    created: list[str]
    kept: list[str]
    superseded: list[str]
    skipped: list[str]


def _iso(t: datetime) -> str:
    return t.isoformat()


class Ledger:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    # ---------------------------------------------------------------- helpers

    def _event(
        self,
        conn: sqlite3.Connection,
        now: datetime,
        kind: str,
        *,
        intent_id: str | None = None,
        run_id: str | None = None,
        src: str | None = None,
        dst: str | None = None,
        detail: dict[str, Any] | str | None = None,
    ) -> None:
        text = json.dumps(detail, default=str) if isinstance(detail, dict) else detail
        conn.execute(
            "INSERT INTO events (at, intent_id, run_id, kind, from_status, to_status, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_iso(now), intent_id, run_id, kind, src, dst, text),
        )

    def _transition(
        self,
        conn: sqlite3.Connection,
        intent_id: str,
        src: IntentStatus,
        dst: IntentStatus,
        now: datetime,
        *,
        kind: str | None = None,
        detail: dict[str, Any] | str | None = None,
        **fields: Any,
    ) -> None:
        if not can_transition(src, dst):
            raise LedgerError(f"illegal transition {src} -> {dst}")
        sets = ", ".join(["status = ?", "updated_at = ?", *(f"{k} = ?" for k in fields)])
        cur = conn.execute(
            f"UPDATE po_intents SET {sets} WHERE intent_id = ? AND status = ?",
            (dst.value, _iso(now), *fields.values(), intent_id, src.value),
        )
        if cur.rowcount != 1:
            raise StateConflict(f"intent {intent_id} is not {src}")
        self._event(conn, now, kind or f"{src}->{dst}", intent_id=intent_id, src=src, dst=dst, detail=detail)

    def _one(self, conn: sqlite3.Connection, intent_id: str) -> Intent:
        row = conn.execute("SELECT * FROM po_intents WHERE intent_id = ?", (intent_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no intent {intent_id}")
        return Intent.from_row(row)

    # ---------------------------------------------------------------- reads

    def get(self, intent_id: str) -> Intent:
        return self._one(self._conn, intent_id)

    def intents(
        self, statuses: Iterable[IntentStatus] | None = None, run_id: str | None = None
    ) -> list[Intent]:
        sql, args = "SELECT * FROM po_intents WHERE 1=1", []
        if statuses is not None:
            wanted = [s.value for s in statuses]
            sql += f" AND status IN ({','.join('?' * len(wanted))})"
            args += wanted
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        return [Intent.from_row(r) for r in self._conn.execute(sql + " ORDER BY created_at", args)]

    def events(self, intent_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events", []
        if intent_id:
            sql, args = sql + " WHERE intent_id = ?", [intent_id]
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY seq", args)]

    def run_plan(self, run_id: str) -> ReorderPlan:
        row = self._conn.execute("SELECT plan_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no run {run_id}")
        return ReorderPlan.model_validate_json(row["plan_json"])

    def runs(self) -> list[dict[str, Any]]:
        cols = "run_id, created_at, mode, llm, policy_version, snapshot_hash"
        return [dict(r) for r in self._conn.execute(f"SELECT {cols} FROM runs ORDER BY created_at DESC")]

    def ledger_view(
        self,
        now: datetime,
        *,
        exclude_intent: str | None = None,
    ) -> dict[str, LedgerState]:
        """What the engine needs to know per part: on-order quantity, open/unresolved POs, rejections.
        `exclude_intent` leaves one intent out (pre-flight re-checks an order as if it were not placed)."""
        view: dict[str, dict[str, Any]] = {}
        for it in self.intents(OPEN):
            if it.intent_id == exclude_intent:
                continue
            v = view.setdefault(it.part_key, {"on_order": 0})
            if it.status in ON_ORDER:
                qty = (
                    max(it.quantity, it.parsed_quantity or 0)
                    if it.status is S.SUBMITTED_MISMATCH
                    else it.quantity
                )
                v["on_order"] += qty
            if it.status in HAS_OPEN_PO:
                v["has_open_po"] = True
            if it.status in UNRESOLVED:
                v["inflight_unresolved"] = True
        rejections = self._conn.execute(
            "SELECT i.part_key, a.reason, a.created_at "
            "FROM approvals a JOIN po_intents i USING (intent_id) WHERE a.action = 'REJECT' "
            "ORDER BY a.created_at"
        ).fetchall()
        for r in rejections:  # later rows overwrite earlier: the latest rejection wins
            if datetime.fromisoformat(r["created_at"]) < now - REJECTION_MEMORY:
                continue
            v = view.setdefault(r["part_key"], {"on_order": 0})
            v.update(rejected_recently=True, rejection_reason=r["reason"])
        return {k: LedgerState(**v) for k, v in view.items()}

    # ---------------------------------------------------------------- planning

    def persist_plan(self, plan: ReorderPlan, now: datetime) -> PersistResult:
        """Record the run and turn its orders into intents.

        Pending intents from earlier runs are kept when the decision is unchanged (same decision_hash)
        and superseded otherwise, in one transaction. AUTO orders are recorded as approved by policy.
        BLOCK orders never become intents (they cannot be submitted)."""
        created: list[str] = []
        kept: list[str] = []
        superseded: list[str] = []
        skipped: list[str] = []
        orders = {
            r.decision.facts.part_key: r
            for r in plan.recommendations
            if r.decision.decision is Decision.REORDER and r.final_route in (Route.AUTO, Route.HUMAN)
        }
        with self.tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO runs (run_id, created_at, mode, llm, policy_version, policy_hash, "
                "snapshot_hash, plan_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.run_id,
                    _iso(plan.created_at),
                    plan.mode,
                    plan.llm,
                    plan.policy_version,
                    plan.policy_hash,
                    plan.snapshot_hash,
                    plan.model_dump_json(),
                ),
            )
            pending = {
                r["part_key"]: Intent.from_row(r)
                for r in conn.execute("SELECT * FROM po_intents WHERE status = 'PENDING_APPROVAL'")
            }
            for key, it in pending.items():
                rec = orders.get(key)
                if rec is not None and rec.decision.decision_hash == it.decision_hash:
                    kept.append(it.intent_id)
                    continue
                self._transition(
                    conn,
                    it.intent_id,
                    S.PENDING_APPROVAL,
                    S.SUPERSEDED,
                    now,
                    detail={"by_run": plan.run_id},
                )
                superseded.append(it.intent_id)
            kept_parts = {self._one(conn, i).part_key for i in kept}
            for key, rec in orders.items():
                if key in kept_parts:
                    continue
                d = rec.decision
                intent_id = uuid.uuid4().hex[:12]
                auto = rec.final_route is Route.AUTO
                bind = binding_hash(key, d.facts.item_name, d.quantity, d.decision_hash)
                try:
                    conn.execute(
                        "INSERT INTO po_intents (intent_id, run_id, part_key, item_name, supplier, quantity, "
                        "engine_quantity, route, decision_hash, status, created_at, updated_at, "
                        "on_hand_at_plan, "
                        "approved_expires_at, binding_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            intent_id,
                            plan.run_id,
                            key,
                            d.facts.item_name,
                            d.facts.supplier,
                            d.quantity,
                            d.quantity,
                            rec.final_route.value,
                            d.decision_hash,
                            (S.APPROVED if auto else S.PENDING_APPROVAL).value,
                            _iso(now),
                            _iso(now),
                            d.facts.on_hand,
                            _iso(now + APPROVAL_TTL) if auto else None,
                            bind if auto else None,
                        ),
                    )
                except sqlite3.IntegrityError:
                    # Another open intent exists for this part: the engine should have held it.
                    self._event(conn, now, "SKIPPED_OPEN_INTENT", run_id=plan.run_id, detail={"part": key})
                    skipped.append(key)
                    continue
                self._event(
                    conn,
                    now,
                    "CREATED",
                    intent_id=intent_id,
                    run_id=plan.run_id,
                    dst=S.APPROVED if auto else S.PENDING_APPROVAL,
                    detail={"quantity": d.quantity, "route": rec.final_route.value},
                )
                if auto:
                    conn.execute(
                        "INSERT INTO approvals (approval_id, intent_id, actor, action, quantity, "
                        "binding_hash, "
                        "created_at, expires_at) VALUES (?, ?, ?, 'APPROVE', ?, ?, ?, ?)",
                        (
                            uuid.uuid4().hex[:12],
                            intent_id,
                            f"policy:auto@{plan.policy_version}",
                            d.quantity,
                            bind,
                            _iso(now),
                            _iso(now + APPROVAL_TTL),
                        ),
                    )
                created.append(intent_id)
        return PersistResult(created, kept, superseded, skipped)

    # ---------------------------------------------------------------- human decisions

    def approve(
        self,
        intent_id: str,
        actor: str,
        now: datetime,
        *,
        quantity: int | None = None,
        reason: str | None = None,
    ) -> Intent:
        with self.tx() as conn:
            it = self._one(conn, intent_id)
            qty = quantity if quantity is not None else it.quantity
            if qty <= 0:
                raise LedgerError("quantity must be positive")
            edited = qty != it.quantity
            if edited and not (reason and reason.strip()):
                raise LedgerError("a reason is required when changing the quantity")
            bind = binding_hash(it.part_key, it.item_name, qty, it.decision_hash)
            expires = now + APPROVAL_TTL
            self._transition(
                conn,
                intent_id,
                S.PENDING_APPROVAL,
                S.APPROVED,
                now,
                detail={"actor": actor, "quantity": qty, "edited": edited},
                quantity=qty,
                binding_hash=bind,
                approved_expires_at=_iso(expires),
            )
            conn.execute(
                "INSERT INTO approvals (approval_id, intent_id, actor, action, quantity, reason, "
                "binding_hash, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex[:12],
                    intent_id,
                    actor,
                    "EDIT" if edited else "APPROVE",
                    qty,
                    reason,
                    bind,
                    _iso(now),
                    _iso(expires),
                ),
            )
            return self._one(conn, intent_id)

    def reject(
        self,
        intent_id: str,
        actor: str,
        reason: str,
        now: datetime,
    ) -> Intent:
        if not reason.strip():
            raise LedgerError("a reason is required to reject")
        with self.tx() as conn:
            self._transition(
                conn,
                intent_id,
                S.PENDING_APPROVAL,
                S.REJECTED,
                now,
                detail={"actor": actor, "reason": reason},
            )
            conn.execute(
                "INSERT INTO approvals (approval_id, intent_id, actor, action, reason, created_at) "
                "VALUES (?, ?, ?, 'REJECT', ?, ?)",
                (uuid.uuid4().hex[:12], intent_id, actor, reason, _iso(now)),
            )
            return self._one(conn, intent_id)

    # ---------------------------------------------------------------- submission (used by the submitter)

    def begin_submit(self, intent_id: str, now: datetime, on_hand: int | None) -> Intent:
        """APPROVED -> SUBMITTING, committed before the SAP call. Raises StateConflict if another
        submitter got there first, which is what makes the call exactly-once."""
        with self.tx() as conn:
            it = self._one(conn, intent_id)
            if it.binding_hash != binding_hash(it.part_key, it.item_name, it.quantity, it.decision_hash):
                raise LedgerError(f"intent {intent_id}: approval does not match the payload")
            if it.approved_expires_at and datetime.fromisoformat(it.approved_expires_at) < now:
                raise LedgerError(f"intent {intent_id}: approval expired")
            self._transition(conn, intent_id, S.APPROVED, S.SUBMITTING, now, on_hand_at_submit=on_hand)
            return self._one(conn, intent_id)

    def finish_submit(
        self,
        intent_id: str,
        status: IntentStatus,
        now: datetime,
        *,
        raw_response: str | None = None,
        sap_po_number: str | None = None,
        parsed_quantity: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> Intent:
        if status not in (S.SUBMITTED, S.SUBMITTED_MISMATCH, S.UNKNOWN, S.FAILED):
            raise LedgerError(f"not a submission outcome: {status}")
        with self.tx() as conn:
            self._transition(
                conn,
                intent_id,
                S.SUBMITTING,
                status,
                now,
                detail=detail,
                raw_response=raw_response,
                sap_po_number=sap_po_number,
                parsed_quantity=parsed_quantity,
                submitted_at=_iso(now),
            )
            return self._one(conn, intent_id)

    def release_unsent(self, intent_id: str, now: datetime, reason: str) -> Intent:
        """SUBMITTING -> APPROVED, only when the request provably never left this process."""
        with self.tx() as conn:
            self._transition(
                conn, intent_id, S.SUBMITTING, S.APPROVED, now, kind="RELEASED_UNSENT", detail=reason
            )
            return self._one(conn, intent_id)

    def expire_approval(self, intent_id: str, now: datetime) -> Intent:
        with self.tx() as conn:
            self._transition(conn, intent_id, S.APPROVED, S.EXPIRED, now)
            return self._one(conn, intent_id)

    def mark_stale(self, intent_id: str, now: datetime, detail: dict[str, Any]) -> Intent:
        with self.tx() as conn:
            self._transition(conn, intent_id, S.APPROVED, S.STALE, now, detail=detail)
            return self._one(conn, intent_id)

    # ---------------------------------------------------------------- reconciliation

    def resolve(self, intent_id: str, actor: str, now: datetime, sap_po_number: str | None) -> Intent:
        """Human resolves an UNKNOWN / MISMATCH outcome: a PO number means it exists, None means no PO."""
        with self.tx() as conn:
            it = self._one(conn, intent_id)
            if it.status not in UNRESOLVED:
                raise LedgerError(f"intent {intent_id} is {it.status}, nothing to resolve")
            dst = S.SUBMITTED if sap_po_number else S.FAILED
            fields = {"sap_po_number": sap_po_number} if sap_po_number else {}
            self._transition(
                conn,
                intent_id,
                it.status,
                dst,
                now,
                kind="RESOLVED",
                detail={"actor": actor, "po": sap_po_number},
                **fields,
            )
            return self._one(conn, intent_id)

    def mark_received(self, intent_id: str, actor: str, now: datetime) -> Intent:
        with self.tx() as conn:
            self._transition(conn, intent_id, S.SUBMITTED, S.RECEIVED, now, detail={"actor": actor})
            return self._one(conn, intent_id)

    def cancel(self, intent_id: str, actor: str, reason: str, now: datetime) -> Intent:
        with self.tx() as conn:
            it = self._one(conn, intent_id)
            self._transition(
                conn, intent_id, it.status, S.CANCELLED, now, detail={"actor": actor, "reason": reason}
            )
            return self._one(conn, intent_id)

    # ---------------------------------------------------------------- housekeeping

    def startup_sweep(self, now: datetime, *, recover_in_flight: bool = True) -> dict[str, list[str]]:
        """Crash recovery and ageing. A SUBMITTING intent found at startup may have reached SAP: it becomes
        UNKNOWN (never retried). Old approvals/pending intents expire. Old open POs are flagged but still
        counted. Pass recover_in_flight=False while another submitter may be running: its SUBMITTING
        intents are live calls, not crashes."""
        out: dict[str, list[str]] = {"unknown": [], "expired": [], "stale_open_po": []}
        with self.tx() as conn:
            for it in [
                Intent.from_row(r)
                for r in conn.execute(
                    "SELECT * FROM po_intents WHERE status IN ('SUBMITTING', 'APPROVED', 'PENDING_APPROVAL', "
                    "'SUBMITTED')"
                )
            ]:
                if it.status is S.SUBMITTING:
                    if not recover_in_flight:
                        continue
                    self._transition(conn, it.intent_id, S.SUBMITTING, S.UNKNOWN, now, kind="CRASH_RECOVERY")
                    out["unknown"].append(it.intent_id)
                elif (
                    it.status is S.APPROVED
                    and it.approved_expires_at
                    and (datetime.fromisoformat(it.approved_expires_at) < now)
                ):
                    self._transition(conn, it.intent_id, S.APPROVED, S.EXPIRED, now)
                    out["expired"].append(it.intent_id)
                elif (
                    it.status is S.PENDING_APPROVAL
                    and datetime.fromisoformat(it.created_at) < now - PENDING_TTL
                ):
                    self._transition(conn, it.intent_id, S.PENDING_APPROVAL, S.EXPIRED, now)
                    out["expired"].append(it.intent_id)
                elif (
                    it.status is S.SUBMITTED
                    and it.submitted_at
                    and (datetime.fromisoformat(it.submitted_at) < now - STALE_OPEN_PO_AGE)
                ):
                    out["stale_open_po"].append(it.intent_id)
        return out

    def apply_receipts(self, snapshot: Snapshot, now: datetime) -> list[str]:
        """Close SUBMITTED POs whose part's on-hand stock rose by at least 80% of the ordered quantity."""
        stock = {p.key: p.stock for p in snapshot.parts}
        received = []
        with self.tx() as conn:
            for it in [
                Intent.from_row(r)
                for r in conn.execute("SELECT * FROM po_intents WHERE status = 'SUBMITTED'")
            ]:
                base, current = it.on_hand_at_submit, stock.get(it.part_key)
                if (
                    base is not None
                    and current is not None
                    and current - base >= RECEIPT_FRACTION * it.quantity
                ):
                    self._transition(
                        conn,
                        it.intent_id,
                        S.SUBMITTED,
                        S.RECEIVED,
                        now,
                        kind="AUTO_RECEIVED",
                        detail={"on_hand_at_submit": base, "on_hand_now": current},
                    )
                    received.append(it.intent_id)
        return received
