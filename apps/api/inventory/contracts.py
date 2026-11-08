from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


MAX_STOCK_INTEGER = 2**63 - 1


class InventoryUnit(Enum):
    EACH = "EACH"


class StockMovementKind(Enum):
    RECEIPT = "RECEIPT"
    ADJUSTMENT = "ADJUSTMENT"


class InventoryValidationError(ValueError):
    """Invalid inventory command, including technical quantity/version overflow."""


class InventoryVariantNotFoundError(LookupError):
    """Catalog contains no such Variant; this is not uninitialized inventory."""


class StockNotInitializedError(LookupError):
    """A Variant exists but has not received its initial positive stock."""


class StockConflictError(RuntimeError):
    """The expectation is stale; the command must not be silently replayed."""


@dataclass(frozen=True)
class StockSnapshot:
    position_id: UUID
    variant_id: UUID
    unit: InventoryUnit
    on_hand: int
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StockMovementSnapshot:
    movement_id: UUID
    position_id: UUID
    variant_id: UUID
    sequence: int
    kind: StockMovementKind
    unit: InventoryUnit
    quantity_before: int
    delta: int
    quantity_after: int
    reason: str | None
    recorded_at: datetime
