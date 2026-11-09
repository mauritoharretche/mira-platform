from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class PriceBasis(Enum):
    PER_VARIANT = "PER_VARIANT"


@dataclass(frozen=True)
class PriceSnapshot:
    """An administrative revision, not a quote or a sellability decision."""

    revision_id: UUID
    variant_id: UUID
    sequence: int
    amount: Decimal
    currency: str
    price_basis: PriceBasis
    recorded_at: datetime


class MeasurementUnit(Enum):
    EACH = "EACH"
    G = "G"
    KG = "KG"


@dataclass(frozen=True)
class VariantMeasurementSpecSnapshot:
    """A declaration of metadata, not transaction quantities or a pricing basis."""

    spec_id: UUID
    variant_id: UUID
    version: int
    sale_unit: MeasurementUnit | None
    net_content_amount: Decimal | None
    net_content_unit: MeasurementUnit | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProductSummary:
    id: UUID
    name: str
    category_id: UUID | None
    is_active: bool


@dataclass(frozen=True)
class ProductPage:
    items: tuple[ProductSummary, ...]
    next_after_id: UUID | None


@dataclass(frozen=True)
class CategorySummary:
    id: UUID
    name: str


@dataclass(frozen=True)
class CategoryCursor:
    name_key: str
    id: UUID


@dataclass(frozen=True)
class CategoryPage:
    items: tuple[CategorySummary, ...]
    next_cursor: CategoryCursor | None
