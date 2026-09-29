"""Typed view of the inventory as returned by the Lato MCP server."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Product(BaseModel):
    """A finished bike (GetProductDetails)."""

    model_config = ConfigDict(frozen=True)

    title: str
    stock: int = Field(ge=0)
    brand: str | None = None
    sku: str | None = None
    stock_per_crate: int | None = None
    number_of_parts: int | None = None  # whole-bike component count; most are not in this inventory


class Part(BaseModel):
    """An inventory part (GetPartDetails)."""

    model_config = ConfigDict(frozen=True)

    key: str  # normalised title; stable identity for the ledger
    item_name: str  # exact title, used verbatim as ItemName for SAP
    description: str = ""
    supplier: str
    stock: int = Field(ge=0)
    per_crate: int | None = None
    used_by: tuple[str, ...]  # product titles, as listed by the server
    is_fragile: bool = False


class DataIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: str  # "BLOCK" (part cannot be ordered) | "WARN" (escalate to a human)
    code: str
    subject: str  # part item_name, product title, or "run"
    detail: str


class Snapshot(BaseModel):
    """One consistent read of the inventory, plus anything wrong with it."""

    model_config = ConfigDict(frozen=True)

    fetched_at: datetime
    products: tuple[Product, ...]
    parts: tuple[Part, ...]
    issues: tuple[DataIssue, ...]
    content_hash: str  # sha256 of the raw tool payloads; pins decisions to the data they saw

    @property
    def catalogue_size(self) -> int:
        return len(self.products)

    def product(self, title: str) -> Product | None:
        return next((p for p in self.products if p.title == title), None)

    def blocked_part_names(self) -> set[str]:
        return {i.subject for i in self.issues if i.severity == "BLOCK"}
