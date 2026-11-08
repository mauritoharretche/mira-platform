from uuid import UUID

from django.db import IntegrityError, transaction

from audit import application as audit_application
from audit.contracts import InventoryMovementAuditAction
from catalog import references as catalog_references
from identity import application as identity_application
from identity.contracts import Capability

from .contracts import (
    MAX_STOCK_INTEGER, InventoryUnit, InventoryValidationError,
    InventoryVariantNotFoundError, StockConflictError, StockMovementKind,
    StockMovementSnapshot, StockNotInitializedError, StockSnapshot,
)
from .models import StockMovement, StockPosition


def _quantity(value: int, *, positive: bool = False) -> int:
    if type(value) is not int or not (1 if positive else 0) <= value <= MAX_STOCK_INTEGER:
        raise InventoryValidationError("quantity must be an integer within technical bounds")
    return value


def _version(value: int | None, *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    if type(value) is not int or not 1 <= value <= MAX_STOCK_INTEGER:
        raise InventoryValidationError("a positive expected version is required")


def _variant(variant_id: UUID) -> None:
    if not isinstance(variant_id, UUID):
        raise InventoryValidationError("variant_id must be a UUID")
    if not catalog_references.variant_exists(variant_id=variant_id):
        raise InventoryVariantNotFoundError("Variant does not exist")


def _snapshot(position: StockPosition) -> StockSnapshot:
    return StockSnapshot(
        position_id=position.pk, variant_id=position.variant_id,
        unit=InventoryUnit(position.accounting_unit), on_hand=position.on_hand,
        version=position.version, created_at=position.created_at, updated_at=position.updated_at,
    )


def _append(*, actor, position, before, delta, kind, reason):
    movement = StockMovement.objects.using("default").create(
        position=position, sequence=position.version, kind=kind.value,
        unit=position.accounting_unit, quantity_before=before, delta=delta,
        quantity_after=position.on_hand, reason=reason,
    )
    audit_application.record_inventory_movement(
        actor=actor,
        action=(InventoryMovementAuditAction.STOCK_RECEIVED if kind is StockMovementKind.RECEIPT
                else InventoryMovementAuditAction.STOCK_ADJUSTED),
        variant_id=position.variant_id, position_id=position.pk, movement_id=movement.pk,
        sequence=movement.sequence, unit=movement.unit,
        quantity_before=before, delta=delta, quantity_after=position.on_hand,
    )


def receive_stock(
    *, principal: object, variant_id: UUID, unit: InventoryUnit,
    quantity: int, expected_version: int | None,
) -> StockSnapshot:
    """Receive EACH explicitly; trusted principal provenance belongs to the caller.

    None expects no position, never an unconditional increment. Stale committed
    retries conflict, without replay or business retries. An outer transaction
    can still roll back the returned snapshot and its movement/attribution.
    """
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(principal=principal, capability=Capability.RECEIVE_STOCK)
        _quantity(quantity, positive=True)
        _version(expected_version, allow_none=True)
        if unit is not InventoryUnit.EACH:
            raise InventoryValidationError("explicit InventoryUnit.EACH required")
        _variant(variant_id)
        position = StockPosition.objects.using("default").select_for_update().filter(variant_id=variant_id).first()
        if position is None:
            if expected_version is not None:
                raise StockConflictError("stock position has changed")
            try:
                with transaction.atomic(using="default"):
                    position = StockPosition.objects.using("default").create(
                        variant_id=variant_id, accounting_unit=unit.value, on_hand=quantity, version=1,
                    )
            except IntegrityError as error:
                if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) == "inventory_position_variant_unique":
                    raise StockConflictError("stock position was initialized concurrently") from error
                raise
            before = 0
        else:
            if expected_version != position.version:
                raise StockConflictError("stock position has changed")
            before = position.on_hand
            _quantity(before + quantity)
            if position.version == MAX_STOCK_INTEGER:
                raise InventoryValidationError("stock version exhausted")
            position.on_hand += quantity
            position.version += 1
            position.save(using="default", update_fields=["on_hand", "version", "updated_at"])
        _append(actor=actor, position=position, before=before, delta=quantity,
                kind=StockMovementKind.RECEIPT, reason=None)
    return _snapshot(position)


def adjust_stock(
    *, principal: object, variant_id: UUID, new_on_hand: int,
    reason: str, expected_version: int,
) -> StockSnapshot:
    """Set a desired balance with an explanation; this is not a sales operation."""
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(principal=principal, capability=Capability.ADJUST_STOCK)
        _quantity(new_on_hand)
        _version(expected_version, allow_none=False)
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
            raise InventoryValidationError("a nonempty reason of at most 255 characters is required")
        reason = reason.strip()
        _variant(variant_id)
        position = StockPosition.objects.using("default").select_for_update().filter(variant_id=variant_id).first()
        if position is None:
            raise StockNotInitializedError("stock position is not initialized")
        if expected_version != position.version:
            raise StockConflictError("stock position has changed")
        if position.on_hand == new_on_hand:
            return _snapshot(position)
        if position.version == MAX_STOCK_INTEGER:
            raise InventoryValidationError("stock version exhausted")
        before = position.on_hand
        position.on_hand = new_on_hand
        position.version += 1
        position.save(using="default", update_fields=["on_hand", "version", "updated_at"])
        _append(actor=actor, position=position, before=before, delta=new_on_hand - before,
                kind=StockMovementKind.ADJUSTMENT, reason=reason)
    return _snapshot(position)


def get_stock(*, principal: object, variant_id: UUID) -> StockSnapshot | None:
    """Return administrative on-hand, not reserved/available/sellable quantity."""
    identity_application.require_capability(principal=principal, capability=Capability.READ_STOCK)
    _variant(variant_id)
    position = StockPosition.objects.using("default").filter(variant_id=variant_id).first()
    return _snapshot(position) if position is not None else None


def list_stock_movements(*, principal: object, variant_id: UUID) -> tuple[StockMovementSnapshot, ...]:
    """Evaluate immutable history in sequence order; clocks do not order stock."""
    identity_application.require_capability(principal=principal, capability=Capability.READ_STOCK_MOVEMENTS)
    _variant(variant_id)
    position = StockPosition.objects.using("default").filter(variant_id=variant_id).first()
    if position is None:
        return ()
    return tuple(StockMovementSnapshot(
        movement_id=entry.pk, position_id=position.pk, variant_id=variant_id,
        sequence=entry.sequence, kind=StockMovementKind(entry.kind), unit=InventoryUnit(entry.unit),
        quantity_before=entry.quantity_before, delta=entry.delta, quantity_after=entry.quantity_after,
        reason=entry.reason, recorded_at=entry.recorded_at,
    ) for entry in StockMovement.objects.using("default").filter(position_id=position.pk).order_by("-sequence"))
