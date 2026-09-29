"""Classify RaiseSAPPurchaseOrder responses. Code only; the LLM never parses confirmations.

Observed in the sandbox (tests/fixtures/live/po_probe.json):
  * success:  "Purchase order Gel-905 for Gel Comfort Seat from Selle Italia created for 5 items."
  * refusals come back as normal text with isError=false, e.g. "No part named 'x' found. ..." and
    "Quantity is required and must be a positive integer." -> nothing was created (FAILED)
  * a malformed quantity returns isError=true "Error occurred during tool call: ..." -> FAILED
  * PO numbers are 3 letters + 3 random digits and can repeat, so they are not an idempotency key.
Anything we cannot classify is UNKNOWN: a PO may exist, so a human must reconcile it.
"""

import re
from dataclasses import dataclass, field

from lato_reorder.ledger.states import IntentStatus

PO_PREFIX = re.compile(r"^Purchase order (?P<po>\S+) for (?P<rest>.+)$", re.DOTALL)
QTY_TAIL = re.compile(r" created for (?P<qty>\d+) items?\.?$")
KNOWN_REFUSALS = (
    re.compile(r"^No part named '.*' found", re.DOTALL),
    re.compile(r"Quantity is required and must be a positive integer"),
)
TOOL_ERROR = re.compile(r"^Error occurred during tool call:")


@dataclass(frozen=True)
class Outcome:
    status: IntentStatus
    po_number: str | None = None
    parsed_quantity: int | None = None
    notes: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return " ".join(text.split()).rstrip(".")


def classify(text: str, is_error: bool, item_name: str, supplier: str | None, quantity: int) -> Outcome:
    text = text.strip()
    match = PO_PREFIX.match(text)
    if match is None:
        if any(p.search(text) for p in KNOWN_REFUSALS):
            return Outcome(IntentStatus.FAILED, notes=["SAP refused the order; nothing was created"])
        if is_error and TOOL_ERROR.match(text):
            return Outcome(IntentStatus.FAILED, notes=["tool error; nothing was created"])
        return Outcome(IntentStatus.UNKNOWN, notes=["unrecognised response; a PO may exist"])

    po, rest = match["po"], match["rest"]
    unit = "item" if quantity == 1 else "items"
    expected = f"{item_name} from {supplier} created for {quantity} {unit}"
    if _norm(rest) == _norm(expected):
        return Outcome(IntentStatus.SUBMITTED, po_number=po, parsed_quantity=quantity)

    # Fall back to anchored pieces (robust to names containing " from ").
    qty_match = QTY_TAIL.search(rest)
    parsed_qty = int(qty_match["qty"]) if qty_match else None
    item_ok = rest.startswith(f"{item_name} from ")
    if item_ok and parsed_qty == quantity:
        return Outcome(
            IntentStatus.SUBMITTED,
            po_number=po,
            parsed_quantity=parsed_qty,
            notes=["SUPPLIER_MISMATCH: confirmation names a different supplier"],
        )
    notes = []
    if not item_ok:
        notes.append("confirmation names a different item")
    if parsed_qty != quantity:
        notes.append(f"confirmation quantity {parsed_qty} != requested {quantity}")
    return Outcome(IntentStatus.SUBMITTED_MISMATCH, po_number=po, parsed_quantity=parsed_qty, notes=notes)
