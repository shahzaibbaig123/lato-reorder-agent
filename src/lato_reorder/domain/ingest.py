"""Parse and strictly validate raw tool payloads into a Snapshot.

Invalid data is never "fixed" (a missing stock is not treated as 0, which would trigger a large
order): the affected part is BLOCKED and reported. Soft anomalies become WARN issues that route
the part to a human.
"""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from lato_reorder.domain.models import DataIssue, Part, Product, Snapshot
from lato_reorder.domain.names import has_control_chars, normalise, similarity

NEAR_DUPLICATE_THRESHOLD = 0.9


def _strict_int(value: Any) -> int | None:
    """Accept ints (and integral floats); reject bools, strings, None, negatives are caught later."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _product_titles(raw_products: Any) -> list[str] | None:
    if not isinstance(raw_products, list):
        return None
    titles = []
    for entry in raw_products:
        title = entry.get("Title") if isinstance(entry, dict) else entry
        if not isinstance(title, str) or not title.strip():
            return None
        titles.append(title)
    return titles


def build_snapshot(
    raw_products: list[dict[str, Any]],
    raw_parts: list[dict[str, Any]],
    fetched_at: datetime | None = None,
) -> Snapshot:
    issues: list[DataIssue] = []

    products: list[Product] = []
    for rp in raw_products:
        title = rp.get("Title")
        stock = _strict_int(rp.get("StockQuantity"))
        if not isinstance(title, str) or not title.strip():
            issues.append(
                DataIssue(severity="WARN", code="PRODUCT_NO_TITLE", subject="run", detail=str(rp)[:120])
            )
            continue
        if stock is None or stock < 0:
            # Finished-bike stock only informs routing; keep the product so fan-out stays correct.
            issues.append(
                DataIssue(
                    severity="WARN",
                    code="PRODUCT_BAD_STOCK",
                    subject=title,
                    detail=f"StockQuantity={rp.get('StockQuantity')!r}",
                )
            )
            stock = 0
        brand = rp.get("Brand")
        products.append(
            Product(
                title=title,
                stock=stock,
                brand=brand.get("BrandName") if isinstance(brand, dict) else brand,
                sku=rp.get("SKU"),
                stock_per_crate=_strict_int(rp.get("StockAmountPerCrate")),
                number_of_parts=_strict_int(rp.get("NumberOfParts")),
            )
        )
    product_titles = {p.title for p in products}

    parts: list[Part] = []
    for rp in raw_parts:
        name = rp.get("Title")
        if not isinstance(name, str) or not name.strip():
            issues.append(
                DataIssue(severity="WARN", code="PART_NO_TITLE", subject="run", detail=str(rp)[:120])
            )
            continue
        problems: list[tuple[str, str]] = []
        stock = _strict_int(rp.get("StockQuantity"))
        if stock is None:
            problems.append(("BAD_STOCK", f"StockQuantity={rp.get('StockQuantity')!r} is not an integer"))
        elif stock < 0:
            problems.append(("NEGATIVE_STOCK", f"StockQuantity={stock}"))
        supplier = rp.get("Supplier")
        if not isinstance(supplier, str) or not supplier.strip():
            problems.append(("NO_SUPPLIER", f"Supplier={supplier!r}"))
        used_by = _product_titles(rp.get("Products"))
        if used_by is None:
            problems.append(("BAD_PRODUCTS", f"Products={rp.get('Products')!r}"[:120]))
        if has_control_chars(name):
            problems.append(("NAME_CONTROL_CHARS", repr(name)))
        for code, detail in problems:
            issues.append(DataIssue(severity="BLOCK", code=code, subject=name, detail=detail))
        if problems:
            continue  # cannot build a trustworthy Part; it stays out of the plan and is reported

        assert stock is not None and used_by is not None and isinstance(supplier, str)
        for title, count in Counter(used_by).items():
            if count > 1:
                issues.append(
                    DataIssue(
                        severity="WARN",
                        code="DUPLICATE_PRODUCT_REF",
                        subject=name,
                        detail=f"{title!r} listed {count} times",
                    )
                )
        for title in sorted(set(used_by) - product_titles):
            issues.append(
                DataIssue(
                    severity="WARN",
                    code="UNMATCHED_MODEL",
                    subject=name,
                    detail=f"uses {title!r}, which is not in GetProductDetails",
                )
            )
        parts.append(
            Part(
                key=normalise(name),
                item_name=name,
                description=str(rp.get("Description") or ""),
                supplier=supplier,
                stock=stock,
                per_crate=_strict_int(rp.get("PartAmountPerCrate")),
                used_by=tuple(dict.fromkeys(used_by)),  # dedupe, keep order
                is_fragile=bool(rp.get("IsFragile")),
            )
        )

    # Identity checks across parts.
    for key, count in Counter(p.key for p in parts).items():
        if count > 1:
            for p in parts:
                if p.key == key:
                    issues.append(
                        DataIssue(
                            severity="BLOCK",
                            code="NAME_COLLISION",
                            subject=p.item_name,
                            detail=f"{count} parts normalise to {key!r}",
                        )
                    )
    for i, a in enumerate(parts):
        for b in parts[i + 1 :]:
            if a.key != b.key and similarity(a.item_name, b.item_name) >= NEAR_DUPLICATE_THRESHOLD:
                for p, other in ((a, b), (b, a)):
                    issues.append(
                        DataIssue(
                            severity="WARN",
                            code="NEAR_DUPLICATE_NAME",
                            subject=p.item_name,
                            detail=f"very similar to {other.item_name!r}",
                        )
                    )

    payload = json.dumps({"products": raw_products, "parts": raw_parts}, sort_keys=True).encode()
    return Snapshot(
        fetched_at=fetched_at or datetime.now(UTC),
        products=tuple(products),
        parts=tuple(parts),
        issues=tuple(issues),
        content_hash=hashlib.sha256(payload).hexdigest(),
    )
