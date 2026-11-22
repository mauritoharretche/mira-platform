from enum import Enum


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"


class ProcurementValidationError(ValueError):
    pass


class ProcurementConflictError(RuntimeError):
    pass


class ProcurementNotFoundError(LookupError):
    pass
