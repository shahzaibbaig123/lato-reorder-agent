import json
from pathlib import Path

from lato_reorder.domain.ingest import build_snapshot
from lato_reorder.domain.names import normalise, similarity

LIVE = Path(__file__).parents[1] / "fixtures" / "live"


def live():
    products = json.loads((LIVE / "03_GetProductDetails_parsed.json").read_text())
    parts = json.loads((LIVE / "04_GetPartDetails_parsed.json").read_text())
    return products, parts


def part(title="Test Part", stock=10, supplier="Acme", products=("Bike A",)):
    return {
        "Title": title,
        "StockQuantity": stock,
        "Supplier": supplier,
        "Products": [{"Title": p} for p in products],
        "PartAmountPerCrate": 97,
    }


BIKES = [{"Title": "Bike A", "StockQuantity": 5}, {"Title": "Bike B", "StockQuantity": 7}]


def test_live_snapshot_is_clean():
    snap = build_snapshot(*live())
    assert snap.catalogue_size == 9
    assert len(snap.parts) == 23
    assert snap.issues == ()


def test_live_fan_out_distribution():
    snap = build_snapshot(*live())
    universal = sorted(p.item_name for p in snap.parts if len(p.used_by) == 9)
    assert universal == [
        "Gel Comfort Seat",
        "Hydraulic Suspension Fork",
        "Precision Gear Set",
        "Rear Derailleur Mechanism",
    ]


def test_content_hash_is_stable():
    assert build_snapshot(*live()).content_hash == build_snapshot(*live()).content_hash


def test_missing_stock_blocks_part_instead_of_zeroing_it():
    snap = build_snapshot(BIKES, [part(stock=None)])
    assert snap.parts == ()
    assert [(i.severity, i.code) for i in snap.issues] == [("BLOCK", "BAD_STOCK")]


def test_negative_stock_and_missing_supplier_block():
    snap = build_snapshot(BIKES, [part("A", stock=-1), part("B", supplier="")])
    assert {i.code for i in snap.issues} == {"NEGATIVE_STOCK", "NO_SUPPLIER"}
    assert snap.blocked_part_names() == {"A", "B"}


def test_string_stock_is_not_coerced():
    snap = build_snapshot(BIKES, [part(stock="12 units")])
    assert snap.issues[0].code == "BAD_STOCK"


def test_unmatched_model_warns_but_keeps_part():
    snap = build_snapshot(BIKES, [part(products=("Bike A", "Ghost Bike"))])
    assert len(snap.parts) == 1
    assert snap.parts[0].used_by == ("Bike A", "Ghost Bike")  # still counts toward fan-out
    assert snap.issues[0].code == "UNMATCHED_MODEL"


def test_name_collision_blocks_both():
    snap = build_snapshot(BIKES, [part("Gel Seat"), part("gel  seat")])
    assert {i.code for i in snap.issues} == {"NAME_COLLISION"}
    assert snap.blocked_part_names() == {"Gel Seat", "gel  seat"}


def test_near_duplicate_names_warn():
    snap = build_snapshot(BIKES, [part("Lightweight Alloy Wheels"), part("Lightweight Alloy Wheel")])
    assert {i.code for i in snap.issues} == {"NEAR_DUPLICATE_NAME"}


def test_distinct_live_names_are_not_near_duplicates():
    assert similarity("Lightweight Alloy Wheels", "Reinforced Alloy Wheels") < 0.9


def test_normalise():
    assert normalise("  EveryDay Vibin'  ") == "everyday vibin"
