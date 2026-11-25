"""Narrow POS integration. No broad inventory permissions or borrowed principals.

The caller-owned outer transaction retains the pilot gate. Internal helpers
receive only the freshly authorized actor, never an HTTP-supplied ActorContext.
"""
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid5

from django.utils import timezone
from django.db import transaction
from django.db.transaction import TransactionManagementError
from identity.application import require_capability
from identity.contracts import Capability
from .pos_safety import isolated_sale
from .contracts import AvailabilitySnapshot, ReservationLine, StockConflictError
from .locking import inventory_transaction as sale_transaction, lock_inventory
from .reservations import _availability, _create_reservation, _transition_reservation


@dataclass(frozen=True)
class SaleConsumption:
    reservation_id: UUID
    trace: dict


@sale_transaction
def discover_stock_page(*, principal: object, variant_ids: tuple[UUID, ...]) -> dict[UUID, AvailabilitySnapshot]:
    require_capability(principal=principal, capability=Capability.POS_DISCOVER)
    if not isinstance(variant_ids,tuple) or len(variant_ids)>50 or len(set(variant_ids))!=len(variant_ids) or any(not isinstance(v,UUID) for v in variant_ids):
        raise ValueError("Bounded distinct variant page required")
    now=timezone.now()
    return {v:_availability(v,now) for v in variant_ids}


@sale_transaction
def quote_stock(*, principal: object, variant_id: UUID) -> AvailabilitySnapshot:
    require_capability(principal=principal, capability=Capability.POS_QUOTE)
    if not isinstance(variant_id, UUID): raise ValueError("Variant UUID required")
    return _availability(variant_id, timezone.now())


@isolated_sale
def consume_for_sale(*, principal: object, operation_id: UUID, sale_id: UUID, lines: tuple[ReservationLine, ...]) -> SaleConsumption:
    actor = require_capability(principal=principal, capability=Capability.POS_CONFIRM)
    if not isinstance(operation_id, UUID) or not isinstance(sale_id, UUID): raise ValueError("Typed sale operation required")
    if not transaction.get_connection("default").in_atomic_block:
        raise TransactionManagementError("POS consumption requires caller-owned transaction")
    lock_inventory()
    # Defense in depth: this capability never authorizes real trade in POS-01.
    from .pos_safety import require_test_sale_context
    require_test_sale_context()
    result = _create_reservation(actor=actor,operation_id=uuid5(operation_id,"pos.reserve"), source="POS-01",
        reference=str(sale_id), expires_at=timezone.now()+timedelta(minutes=5), lines=lines)
    consumed = _transition_reservation(actor=actor,operation_id=uuid5(operation_id,"pos.consume"),
        reservation_id=UUID(result["id"]),action="consume")
    if consumed["state"] != "CONSUMED": raise StockConflictError("Reservation did not commit consumption")
    return SaleConsumption(UUID(consumed["id"]), consumed)
