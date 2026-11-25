"""POS-owned command contracts. Money and quantities never use binary floats."""
from dataclasses import dataclass
from uuid import UUID

from catalog.calculations import MassMeasurement
from catalog.contracts import MeasurementUnit

MAX_LINES = 30
MAX_MONEY = "999999999999.99"
QUOTE_SECONDS = 300


class PosValidationError(ValueError):
    pass


class PosConflictError(RuntimeError):
    pass


class PosNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CartLine:
    variant_id: UUID
    quantity: str
    unit: MeasurementUnit
    measurement: MassMeasurement | None = None
