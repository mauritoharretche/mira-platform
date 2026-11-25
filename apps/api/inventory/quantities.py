"""Exact quantity boundary. EACH stores whole items; G stores integer milligrams."""
import re
from decimal import Decimal, localcontext

from .contracts import MAX_STOCK_INTEGER, InventoryUnit, InventoryValidationError


def unit(value):
    try:
        return value if isinstance(value, InventoryUnit) else InventoryUnit(value)
    except (ValueError, TypeError):
        raise InventoryValidationError("Explicit EACH or G accounting unit required") from None


def parse_quantity(value, accounting_unit, *, signed=False, positive=False):
    accounting_unit = unit(accounting_unit)
    if not isinstance(value, str) or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) or len(value) > 30:
        raise InventoryValidationError("Canonical exact quantity string required")
    if not signed and value.startswith("-"):
        raise InventoryValidationError("Negative quantity not allowed")
    scale = 0 if accounting_unit is InventoryUnit.EACH else 3
    if "." in value and (scale == 0 or len(value.split(".")[1]) > scale):
        raise InventoryValidationError("Quantity precision exceeds accounting scale")
    with localcontext() as context:
        context.prec = 50
        amount = Decimal(value) * (1 if scale == 0 else 1000)
        if abs(amount) > MAX_STOCK_INTEGER or (positive and amount <= 0):
            raise InventoryValidationError("Quantity outside bounds")
        return int(amount)


def format_quantity(value, accounting_unit):
    accounting_unit = unit(accounting_unit)
    if type(value) is not int or abs(value) > MAX_STOCK_INTEGER:
        raise InventoryValidationError("Invalid base-unit quantity")
    if accounting_unit is InventoryUnit.EACH:
        return str(value)
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), 1000)
    return f"{sign}{whole}.{fraction:03}"


def convert_mass(value: str, *, from_unit: str, to_unit: str) -> str:
    if from_unit not in ("G", "KG") or to_unit not in ("G", "KG"):
        raise InventoryValidationError("Explicit G/KG conversion required")
    if not isinstance(value, str) or len(value) > 30 or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise InventoryValidationError("Exact mass string required")
    if "." in value and len(value.split(".")[1]) > (3 if from_unit == "G" else 6):
        raise InventoryValidationError("Sub-milligram precision is unsupported")
    with localcontext() as context:
        context.prec = 50
        milligrams = Decimal(value) * (1000 if from_unit == "G" else 1_000_000)
        if milligrams > MAX_STOCK_INTEGER:
            raise InventoryValidationError("Mass overflow")
        return format(Decimal(milligrams) / (1000 if to_unit == "G" else 1_000_000), ".3f" if to_unit == "G" else ".6f")
