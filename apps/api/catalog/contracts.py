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
