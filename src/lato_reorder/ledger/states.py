"""PO intent state machine."""

from enum import StrEnum


class IntentStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"  # committed BEFORE the SAP call (write-ahead)
    SUBMITTED = "SUBMITTED"
    SUBMITTED_MISMATCH = "SUBMITTED_MISMATCH"  # SAP confirmed a different item/quantity
    UNKNOWN = "UNKNOWN"  # request may have reached SAP; outcome unknown; never auto-retried
    FAILED = "FAILED"  # SAP definitely created nothing
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"  # inventory changed after approval; re-proposed
    CANCELLED = "CANCELLED"
    RECEIVED = "RECEIVED"


S = IntentStatus

# Open intents occupy the one-per-part slot (mirrors the partial unique index).
OPEN = frozenset({S.PENDING_APPROVAL, S.APPROVED, S.SUBMITTING, S.SUBMITTED, S.SUBMITTED_MISMATCH, S.UNKNOWN})
# Quantity counted as "on order" in inventory position (pending approval is not yet committed).
ON_ORDER = frozenset({S.APPROVED, S.SUBMITTING, S.SUBMITTED, S.SUBMITTED_MISMATCH, S.UNKNOWN})
# An order is (or may be) with the supplier.
HAS_OPEN_PO = frozenset({S.APPROVED, S.SUBMITTING, S.SUBMITTED})
# Outcome needs a human before anything else happens for the part.
UNRESOLVED = frozenset({S.UNKNOWN, S.SUBMITTED_MISMATCH})

TRANSITIONS: dict[IntentStatus, frozenset[IntentStatus]] = {
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.SUPERSEDED, S.EXPIRED}),
    S.APPROVED: frozenset({S.SUBMITTING, S.STALE, S.EXPIRED, S.CANCELLED}),
    S.SUBMITTING: frozenset({S.SUBMITTED, S.SUBMITTED_MISMATCH, S.UNKNOWN, S.FAILED, S.APPROVED}),
    S.UNKNOWN: frozenset({S.SUBMITTED, S.FAILED}),
    S.SUBMITTED_MISMATCH: frozenset({S.SUBMITTED, S.FAILED}),
    S.SUBMITTED: frozenset({S.RECEIVED, S.CANCELLED}),
}


def can_transition(src: IntentStatus, dst: IntentStatus) -> bool:
    return dst in TRANSITIONS.get(src, frozenset())
