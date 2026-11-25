"""Exact commercial arithmetic, independent of Inventory and future POS adapters."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from .contracts import MeasurementUnit, PriceBasis, PriceSnapshot


@dataclass(frozen=True)
class PriceCalculation:
    unrounded: Decimal
    amount_ars: Decimal
    quantity: Decimal
    unit: MeasurementUnit
    price_basis: PriceBasis


def calculate_amount(*, amount: Decimal, price_basis: PriceBasis, quantity: Decimal, unit: MeasurementUnit) -> PriceCalculation:
    if not isinstance(amount, Decimal) or not amount.is_finite() or not 0 <= amount <= Decimal("999999999999.99"):
        raise ValueError("Bounded exact ARS price required")
    if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity < 0:
        raise ValueError("Exact nonnegative transaction quantity required")
    with localcontext() as context:
        context.prec = 60
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("ARS price scale exceeds cents")
        if price_basis is PriceBasis.PER_VARIANT and unit is MeasurementUnit.EACH:
            if quantity != quantity.to_integral_value() or quantity > 2**63 - 1:
                raise ValueError("Whole EACH quantity required")
            factor = quantity
        elif price_basis is PriceBasis.PER_KG and unit in (MeasurementUnit.G, MeasurementUnit.KG):
            milligrams = quantity * (1000 if unit is MeasurementUnit.G else 1_000_000)
            if milligrams != milligrams.to_integral_value() or milligrams > 2**63 - 1:
                raise ValueError("Mass precision or bounds exceeded")
            factor = milligrams / Decimal(1_000_000)
        else:
            raise ValueError("Price basis and transaction unit are incompatible")
        raw = amount * factor
        return PriceCalculation(raw, raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN), quantity, unit, price_basis)


def calculate_price(*, price: PriceSnapshot, quantity: Decimal, unit: MeasurementUnit) -> PriceCalculation:
    if not isinstance(price, PriceSnapshot) or price.currency != "ARS":
        raise ValueError("Explicit ARS price revision required")
    return calculate_amount(amount=price.amount, price_basis=price.price_basis, quantity=quantity, unit=unit)


@dataclass(frozen=True)
class MassMeasurement:
    net: Decimal
    unit: MeasurementUnit
    source: str
    measured_at: datetime
    resolution: Decimal
    gross: Decimal | None = None
    tare: Decimal | None = None

    def validate(self) -> "MassMeasurement":
        if self.unit not in (MeasurementUnit.G, MeasurementUnit.KG):
            raise ValueError("Explicit mass unit required")
        if not isinstance(self.source, str) or not self.source.strip() or len(self.source) > 80 or not self.source.isprintable():
            raise ValueError("Measurement source required")
        if not isinstance(self.measured_at, datetime) or self.measured_at.utcoffset() is None:
            raise ValueError("Aware measurement timestamp required")
        floor = Decimal("0.001") if self.unit is MeasurementUnit.G else Decimal("0.000001")
        if not isinstance(self.resolution, Decimal) or not self.resolution.is_finite() or self.resolution < floor:
            raise ValueError("Explicit supported resolution required")
        with localcontext() as context:
            context.prec = 60
            if self.resolution % floor != 0 or self.resolution / floor > 2**63 - 1:
                raise ValueError("Resolution incompatible with base precision")
            for value in (self.net, self.gross, self.tare):
                if value is None: continue
                if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or value / floor > 2**63 - 1 or value % self.resolution != 0:
                    raise ValueError("Measurement violates precision or bounds")
            if self.net is None or (self.gross is None) != (self.tare is None):
                raise ValueError("Net and paired optional gross/tare required")
            if self.gross is not None and self.gross - self.tare != self.net:
                raise ValueError("Gross minus tare must equal net")
        return self
