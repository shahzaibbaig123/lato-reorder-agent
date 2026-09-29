import json
from pathlib import Path

import pytest

from lato_reorder.execution.confirmation import classify
from lato_reorder.ledger.states import IntentStatus as S

PROBE = {
    c["case"]: c for c in json.loads((Path(__file__).parents[1] / "fixtures/live/po_probe.json").read_text())
}


def live(case):
    c = PROBE[case]
    return c["text"], c["is_error"], c["arguments"]["ItemName"], "Selle Italia", c["arguments"]["Quantity"]


def test_live_success():
    out = classify(*live("valid"))
    assert (out.status, out.po_number, out.parsed_quantity) == (S.SUBMITTED, "Gel-905", 5)


@pytest.mark.parametrize("case", ["name_case_variant", "unknown_name", "qty_zero", "qty_negative"])
def test_live_refusals_are_failed_even_though_is_error_is_false(case):
    _, is_error, *_ = live(case)
    assert is_error is False
    assert classify(*live(case)).status is S.FAILED


def test_live_tool_error_is_failed():
    assert classify(*live("qty_decimal")).status is S.FAILED


def test_singular_item():
    text = "Purchase order Gel-100 for Gel Comfort Seat from Selle Italia created for 1 item."
    assert classify(text, False, "Gel Comfort Seat", "Selle Italia", 1).status is S.SUBMITTED


def test_name_containing_from():
    text = "Purchase order Fro-1 for Frame from Hell from Acme created for 3 items."
    assert classify(text, False, "Frame from Hell", "Acme", 3).status is S.SUBMITTED


def test_different_quantity_is_mismatch():
    text = "Purchase order Gel-1 for Gel Comfort Seat from Selle Italia created for 50 items."
    out = classify(text, False, "Gel Comfort Seat", "Selle Italia", 5)
    assert (out.status, out.parsed_quantity) == (S.SUBMITTED_MISMATCH, 50)


def test_different_item_is_mismatch():
    text = "Purchase order Rac-1 for Racing Alloy Wheels from Mavic created for 5 items."
    assert classify(text, False, "Lightweight Alloy Wheels", "Mavic", 5).status is S.SUBMITTED_MISMATCH


def test_supplier_only_difference_is_submitted_with_note():
    text = "Purchase order Gel-1 for Gel Comfort Seat from Brooks created for 5 items."
    out = classify(text, False, "Gel Comfort Seat", "Selle Italia", 5)
    assert out.status is S.SUBMITTED and "SUPPLIER_MISMATCH" in out.notes[0]


@pytest.mark.parametrize("text", ["", "OK", "Service temporarily busy", "<html>502</html>"])
def test_unrecognised_is_unknown(text):
    assert classify(text, False, "Gel Comfort Seat", "Selle Italia", 5).status is S.UNKNOWN
