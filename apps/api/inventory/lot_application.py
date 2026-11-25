"""Physical lot accounting alongside the unchanged EACH stock ledger.

The aggregate balance remains authoritative. Existing stock without a physical
assignment is reported as unallocated; no provenance is invented or backfilled.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from audit import application as audit_application
from catalog import references as catalog_references
from identity import application as identity_application
from identity.contracts import Capability

from .application import adjust_stock, _quantity
from .contracts import InventoryUnit, InventoryValidationError, InventoryVariantNotFoundError, StockConflictError, StockNotInitializedError
from .models import StockLocation, StockLot, StockLotBalance, StockLotEvent, StockMovement, StockPosition
from .locking import inventory_transaction, lock_inventory


def _uuid(value):
    if not isinstance(value, UUID):
        raise InventoryValidationError("UUID required")
    return value


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit or not value.strip().isprintable():
        raise InventoryValidationError("Invalid text")
    return value.strip()


def _event(actor, action, *, lot=None, location=None, other_location=None, quantity=0, reason=None, movement_id=None):
    event = StockLotEvent.objects.using("default").create(
        lot=lot, location=location, other_location=other_location, quantity=quantity,
        reason=reason, stock_movement_id=movement_id, action=action, actor_id=actor.actor_id)
    audit_application.record_inventory_lot_event(actor=actor, event_id=event.pk, action=action)
    return event


def _lot(lot_id, *, lock=False):
    query = StockLot.objects.using("default")
    if lock:
        query = query.select_for_update()
    lot = query.filter(pk=_uuid(lot_id)).first()
    if lot is None:
        raise InventoryVariantNotFoundError("Lot not found")
    return lot


def _location(location_id):
    location = StockLocation.objects.using("default").filter(pk=_uuid(location_id), active=True).first()
    if location is None:
        raise InventoryVariantNotFoundError("Location not found")
    return location


def _balance(lot, location, *, create=False):
    query = StockLotBalance.objects.using("default").select_for_update()
    balance = query.filter(lot=lot, location=location).first()
    if balance is None and create:
        try:
            with transaction.atomic(using="default"):
                balance = StockLotBalance.objects.using("default").create(lot=lot, location=location, quantity=0)
        except IntegrityError as error:
            if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) == "inventory_lot_location_unique":
                raise StockConflictError("Lot balance changed concurrently") from error
            raise
    return balance


def allocated_total(variant_id):
    return (StockLotBalance.objects.using("default").filter(lot__variant_id=variant_id)
            .aggregate(total=Sum("quantity"))["total"] or 0)


@inventory_transaction
def create_location(*, principal, code, name):
    actor = identity_application.require_capability(principal=principal, capability=Capability.MANAGE_STOCK_LOCATIONS)
    code, name = _text(code, 40).upper(), _text(name, 120)
    try:
        with transaction.atomic(using="default"):
            location = StockLocation.objects.using("default").create(code=code, name=name)
            _event(actor, "location.created", location=location)
        return location
    except IntegrityError as error:
        constraint = getattr(getattr(error.__cause__, "diag", None), "constraint_name", "") or ""
        if constraint.startswith("inventory_stocklocation_code_"):
            raise StockConflictError("Location code already exists") from error
        raise


@inventory_transaction
def create_lot(*, principal, variant_id, code, expires_on):
    actor = identity_application.require_capability(principal=principal, capability=Capability.MANAGE_STOCK_LOTS)
    _uuid(variant_id)
    if not catalog_references.variant_exists(variant_id=variant_id):
        raise InventoryVariantNotFoundError("Variant not found")
    code = _text(code, 80).upper()
    if expires_on is not None and (not isinstance(expires_on, date) or isinstance(expires_on, datetime)):
        raise InventoryValidationError("Invalid expiry date")
    try:
        with transaction.atomic(using="default"):
            lot = StockLot.objects.using("default").create(variant_id=variant_id, code=code, expires_on=expires_on)
            _event(actor, "lot.created", lot=lot)
        return lot
    except IntegrityError as error:
        if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) == "inventory_lot_variant_code_unique":
            raise StockConflictError("Lot code already exists for this variant") from error
        raise


@inventory_transaction
def set_lot_status(*, principal, lot_id, status, expected_version, reason):
    actor = identity_application.require_capability(principal=principal, capability=Capability.MANAGE_STOCK_LOTS)
    if status not in ("ACTIVE", "BLOCKED", "QUARANTINE"):
        raise InventoryValidationError("Invalid lot status")
    reason = _text(reason, 255)
    if type(expected_version) is not int or not 1 <= expected_version <= 2**63 - 1:
        raise InventoryValidationError("Expected lot version required")
    with transaction.atomic(using="default"):
        lot = _lot(lot_id, lock=True)
        if lot.version != expected_version:
            raise StockConflictError("Lot changed")
        if lot.status != status:
            if lot.version == 2**63 - 1:
                raise InventoryValidationError("Lot version exhausted")
            if status != "ACTIVE":
                from .reservations import _invalidate_allocations
                _invalidate_allocations(actor=actor, lot_id=lot.pk)
            lot.status = status
            lot.version += 1
            lot.save(using="default", update_fields=["status", "version"])
            _event(actor, "lot.status", lot=lot, reason=reason)
    return lot


def allocate_purchase_receipt(*, principal, variant_id, movement_id, allocations):
    """Called only inside Procurement's receipt transaction after its stock movement.

    Every physical allocation is explicit. An absent allocation leaves the
    receipt in the visible unallocated remainder.
    """
    actor = identity_application.require_capability(principal=principal, capability=Capability.ALLOCATE_STOCK_LOTS)
    if not transaction.get_connection("default").in_atomic_block:
        raise RuntimeError("Allocation requires Procurement transaction")
    lock_inventory()
    movement = StockMovement.objects.using("default").select_related("position").get(pk=_uuid(movement_id))
    if movement.position.variant_id != _uuid(variant_id) or movement.kind != "RECEIPT":
        raise InventoryValidationError("Receipt movement mismatch")
    if movement.position.version != movement.sequence or StockLotEvent.objects.using("default").filter(
            stock_movement_id=movement_id, action="lot.received").exists():
        raise StockConflictError("Receipt already allocated or no longer current")
    if not isinstance(allocations, (list, tuple)) or not 1 <= len(allocations) <= 10:
        raise InventoryValidationError("Receipt allocations required")
    seen, total, normalized = set(), 0, []
    for item in allocations:
        if not isinstance(item, dict) or set(item) != {"lot_id", "location_id", "quantity"}:
            raise InventoryValidationError("Invalid allocation")
        lot = _lot(item["lot_id"], lock=True)
        location = _location(item["location_id"])
        quantity = _quantity(item["quantity"], positive=True)
        if lot.variant_id != variant_id or (lot.pk, location.pk) in seen:
            raise InventoryValidationError("Wrong or duplicate allocation")
        seen.add((lot.pk, location.pk)); total += quantity
        normalized.append((lot, location, quantity))
    if total != movement.delta:
        raise InventoryValidationError("Allocations must cover accepted quantity")
    for lot, location, quantity in normalized:
        balance = _balance(lot, location, create=True)
        balance.quantity = _quantity(balance.quantity + quantity)
        balance.save(using="default", update_fields=["quantity"])
        _event(actor, "lot.received", lot=lot, location=location, quantity=quantity, movement_id=movement_id)
    if allocated_total(variant_id) > movement.position.on_hand:
        raise InventoryValidationError("Physical allocation exceeds stock")
    return tuple((lot.pk, location.pk, quantity) for lot, location, quantity in normalized)


@inventory_transaction
def transfer_lot(*, principal, lot_id, source_id, destination_id, quantity, expected_version, reason, unit=InventoryUnit.EACH):
    actor = identity_application.require_capability(principal=principal, capability=Capability.TRANSFER_STOCK_LOTS)
    quantity = _quantity(quantity, positive=True)
    reason = _text(reason, 255)
    if type(expected_version) is not int or not 1 <= expected_version <= 2**63 - 1 or source_id == destination_id:
        raise InventoryValidationError("Invalid transfer")
    with transaction.atomic(using="default"):
        lot = _lot(lot_id, lock=True)
        if lot.version != expected_version:
            raise StockConflictError("Lot changed")
        if lot.version == 2**63 - 1:
            raise InventoryValidationError("Lot version exhausted")
        position = StockPosition.objects.using("default").filter(variant_id=lot.variant_id).first()
        if position is None or not isinstance(unit, InventoryUnit) or position.accounting_unit != unit.value:
            raise InventoryValidationError("Matching accounting unit required")
        source, destination = _location(source_id), _location(destination_id)
        prior = _balance(lot, source)
        if prior is None or prior.quantity < quantity:
            raise StockConflictError("Insufficient lot quantity at source")
        from .reservations import _reserved
        if prior.quantity - quantity < _reserved(prior.pk, timezone.now()):
            raise StockConflictError("Transfer would move reserved stock")
        target = _balance(lot, destination, create=True)
        prior.quantity -= quantity
        target.quantity = _quantity(target.quantity + quantity)
        prior.save(using="default", update_fields=["quantity"])
        target.save(using="default", update_fields=["quantity"])
        lot.version += 1
        lot.save(using="default", update_fields=["version"])
        _event(actor, "lot.transferred", lot=lot, location=source, other_location=destination,
               quantity=quantity, reason=reason)
    return lot


@inventory_transaction
def correct_lot(*, principal, lot_id, location_id, delta, expected_lot_version, expected_stock_version, reason, unit=InventoryUnit.EACH):
    actor = identity_application.require_capability(principal=principal, capability=Capability.CORRECT_STOCK_LOTS)
    if type(delta) is not int or delta == 0 or abs(delta) > 2**63 - 1:
        raise InventoryValidationError("Nonzero exact base-unit correction required")
    reason = _text(reason, 255)
    if (type(expected_lot_version) is not int or not 1 <= expected_lot_version <= 2**63 - 1
            or type(expected_stock_version) is not int or not 1 <= expected_stock_version <= 2**63 - 1):
        raise InventoryValidationError("Expected versions required")
    with transaction.atomic(using="default"):
        variant_id = _lot(lot_id).variant_id
        position = StockPosition.objects.using("default").select_for_update().filter(variant_id=variant_id).first()
        if position is None:
            raise StockNotInitializedError("Stock not initialized")
        lot = _lot(lot_id, lock=True)
        if not isinstance(unit, InventoryUnit) or position.accounting_unit != unit.value:
            raise InventoryValidationError("Matching accounting unit required")
        if lot.version != expected_lot_version or position.version != expected_stock_version:
            raise StockConflictError("Lot or stock changed")
        if lot.version == 2**63 - 1:
            raise InventoryValidationError("Lot version exhausted")
        location = _location(location_id)
        balance = _balance(lot, location, create=delta > 0)
        if balance is None or balance.quantity + delta < 0:
            raise StockConflictError("Insufficient lot quantity")
        from .reservations import _reserved
        if balance.quantity + delta < _reserved(balance.pk, timezone.now()):
            raise StockConflictError("Correction would remove reserved stock")
        new_stock = position.on_hand + delta
        _quantity(new_stock)
        if delta < 0:
            balance.quantity += delta
            balance.save(using="default", update_fields=["quantity"])
        snapshot = adjust_stock(principal=principal, variant_id=lot.variant_id, new_on_hand=new_stock,
                                reason=reason, expected_version=expected_stock_version, unit=unit)
        if delta > 0:
            balance.quantity = _quantity(balance.quantity + delta)
            balance.save(using="default", update_fields=["quantity"])
        lot.version += 1
        lot.save(using="default", update_fields=["version"])
        movement = StockMovement.objects.using("default").get(position_id=position.pk, sequence=snapshot.version)
        _event(actor, "lot.corrected", lot=lot, location=location, quantity=delta,
               reason=reason, movement_id=movement.pk)
    return lot


def list_locations(*, principal):
    identity_application.require_capability(principal=principal, capability=Capability.READ_STOCK_LOTS)
    return tuple(StockLocation.objects.using("default").order_by("code")[:101])


@inventory_transaction
def list_lots(*, principal, variant_id):
    identity_application.require_capability(principal=principal, capability=Capability.READ_STOCK_LOTS)
    _uuid(variant_id)
    if not catalog_references.variant_exists(variant_id=variant_id):
        raise InventoryVariantNotFoundError("Variant not found")
    today = timezone.localdate()
    lots = StockLot.objects.using("default").filter(variant_id=variant_id).order_by("expires_on", "code", "id")[:101]
    position = StockPosition.objects.using("default").filter(variant_id=variant_id).first()
    accounting = InventoryUnit(position.accounting_unit) if position else InventoryUnit.EACH
    from .quantities import format_quantity
    rows = []
    for lot in lots:
        balances = tuple(lot.balances.select_related("location").order_by("location__code"))
        rows.append({"id": str(lot.pk), "code": lot.code, "expires_on": lot.expires_on.isoformat() if lot.expires_on else None,
                     "status": lot.status, "expired": lot.expires_on is not None and lot.expires_on < today,
                     "version": str(lot.version), "quantity": format_quantity(sum(item.quantity for item in balances), accounting),
                     "unit": accounting.value if position else None,
                     "locations": [{"id": str(item.location_id), "balance_id": str(item.pk), "code": item.location.code,
                                    "quantity": format_quantity(item.quantity, accounting)} for item in balances if item.quantity]})
    position = StockPosition.objects.using("default").filter(variant_id=variant_id).first()
    on_hand = position.on_hand if position else 0
    unallocated = on_hand - allocated_total(variant_id)
    if unallocated < 0:
        raise StockConflictError("Physical allocation exceeds stock")
    eligible = sorted((row for row in rows if row["status"] == "ACTIVE" and not row["expired"] and Decimal(row["quantity"]) > 0),
                      key=lambda row: (row["expires_on"] is None, row["expires_on"] or "", row["code"], row["id"]))
    return {"on_hand": format_quantity(on_hand, accounting), "unallocated": format_quantity(unallocated, accounting), "unit": accounting.value if position else None, "lots": rows,
            "fefo": [row["id"] for row in eligible], "truncated": len(rows) > 100}
