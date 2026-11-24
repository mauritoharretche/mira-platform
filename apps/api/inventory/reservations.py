"""Commercial EACH reservations. All commands share the pilot Inventory gate."""
import hashlib
import json
from datetime import datetime, timezone as datetime_timezone
from uuid import UUID

from django.db.models import Sum
from django.utils import timezone

from audit import application as audit_application
from catalog import references as catalog_references
from identity import application as identity_application
from identity.contracts import Capability

from .contracts import (MAX_STOCK_INTEGER, AvailabilitySnapshot, PhysicalAvailability,
                        InventoryValidationError, InventoryVariantNotFoundError, ReservationLine, StockConflictError)
from .locking import inventory_transaction
from .models import (InventoryOperation, ReservationAllocation, ReservationEvent, StockReservation,
                     StockLocation, StockLot, StockLotBalance, StockPosition)


def _uuid(value):
    if not isinstance(value, UUID):
        raise InventoryValidationError("UUID required")
    return value


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or not value.strip().isprintable():
        raise InventoryValidationError("Invalid reference")
    return value.strip()


def _count(value):
    if type(value) is not int or not 1 <= value <= MAX_STOCK_INTEGER:
        raise InventoryValidationError("Positive EACH integer required")
    return value


def _actor(principal, capability):
    return identity_application.require_capability(principal=principal, capability=capability)


def _event(actor, action, *, reservation=None, operation_id=None, details=None):
    entry = ReservationEvent.objects.using("default").create(
        reservation=reservation, actor_id=actor.actor_id, action=action,
        operation_id=operation_id, details=details or {})
    audit_application.record_reservation_event(actor=actor, event_id=entry.pk,
        reservation_id=reservation.pk if reservation else None, action=action)
    return entry


def _fingerprint(action, payload):
    return hashlib.sha256(json.dumps({"action": action, "payload": payload}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _operation(actor, operation_id, action, payload):
    _uuid(operation_id)
    digest = _fingerprint(action, payload)
    prior = InventoryOperation.objects.using("default").filter(pk=operation_id).first()
    if prior is not None and (prior.action != action or prior.request_sha256 != digest or prior.actor_id != actor.actor_id):
        raise StockConflictError("Operation identity reused with different request or actor")
    return digest, prior


def _save_operation(actor, operation_id, action, digest, result, reservation=None):
    InventoryOperation.objects.using("default").create(operation_id=operation_id, action=action,
        request_sha256=digest, actor_id=actor.actor_id, reservation=reservation, result=result)
    return result


def _eligible(balance, now):
    today = timezone.localdate(now)
    return (balance.location.active and balance.location.commercial_eligible and balance.lot.status == "ACTIVE"
            and (balance.lot.expires_on is None or balance.lot.expires_on >= today))


def _reserved(balance_id, now):
    return ReservationAllocation.objects.using("default").filter(balance_id=balance_id,
        reservation__state="ACTIVE", reservation__expires_at__gt=now).aggregate(total=Sum("quantity"))["total"] or 0


def _balances(variant_id):
    # No UI limits here. All balances participate in availability and FEFO.
    return list(StockLotBalance.objects.using("default").filter(lot__variant_id=variant_id)
                .select_related("lot", "location").order_by("lot__expires_on", "lot__code", "lot_id", "location__code", "location_id"))


def _availability(variant_id, now):
    position = StockPosition.objects.using("default").filter(variant_id=variant_id).first()
    physical = position.on_hand if position else 0
    balances = _balances(variant_id)
    rows = []
    for balance in balances:
        eligible = _eligible(balance, now)
        reserved = _reserved(balance.pk, now)
        if reserved > balance.quantity:
            raise StockConflictError("Reservation/physical balance drift")
        rows.append(PhysicalAvailability(balance.pk, balance.lot_id, balance.lot.code,
            balance.location_id, balance.location.code, balance.lot.expires_on.isoformat() if balance.lot.expires_on else None,
            balance.quantity, eligible, reserved, balance.quantity - reserved if eligible else 0))
    allocated = sum(row.physical for row in rows)
    eligible = sum(row.physical for row in rows if row.eligible)
    reserved = sum(row.reserved for row in rows)
    available = sum(row.available for row in rows)
    if allocated > physical:
        raise StockConflictError("Physical allocation exceeds aggregate stock")
    # Unavailable includes unallocated and the union of ineligibility/reservation;
    # overlapping reasons are never subtracted twice. Safety stock is explicitly zero.
    return AvailabilitySnapshot(variant_id, now, physical, allocated, physical - allocated,
        eligible, reserved, physical - available, available, 0, tuple(rows))


def _find(reservation_id):
    reservation = StockReservation.objects.using("default").select_for_update().filter(pk=_uuid(reservation_id)).first()
    if reservation is None:
        raise InventoryVariantNotFoundError("Reservation not found")
    return reservation


def _snapshot(reservation, now):
    effective = "EXPIRED" if reservation.state == "ACTIVE" and reservation.expires_at <= now else reservation.state
    return {"id": str(reservation.pk), "operation_id": str(reservation.operation_id), "source": reservation.source,
        "reference": reservation.reference, "state": reservation.state, "effective_state": effective,
        "created_at": reservation.created_at.isoformat(), "expires_at": reservation.expires_at.isoformat(),
        "allocations": [{"id": str(a.pk), "variant_id": str(a.balance.lot.variant_id),
            "lot_id": str(a.balance.lot_id), "lot_code": a.balance.lot.code,
            "location_id": str(a.balance.location_id), "location_code": a.balance.location.code,
            "quantity": str(a.quantity), "stock_movement_id": str(a.stock_movement_id) if a.stock_movement_id else None}
            for a in reservation.allocations.select_related("balance__lot", "balance__location").order_by("id")],
        "history": [{"action": e.action, "at": e.recorded_at.isoformat(), "actor_id": e.actor_id}
                    for e in reservation.events.order_by("recorded_at", "id")]}


def _expire_one(actor, reservation, now, operation_id=None):
    if reservation.state == "ACTIVE" and reservation.expires_at <= now:
        reservation.state = "EXPIRED"
        reservation.save(using="default", update_fields=["state"])
        _event(actor, "reservation.expired", reservation=reservation, operation_id=operation_id)


def _invalidate_allocations(*, actor, lot_id=None, location_id=None):
    """Safety changes release the entire impacted multi-variant reservation."""
    query = ReservationAllocation.objects.using("default").filter(reservation__state="ACTIVE")
    if lot_id is not None:
        query = query.filter(balance__lot_id=lot_id)
    if location_id is not None:
        query = query.filter(balance__location_id=location_id)
    for reservation_id in sorted(set(query.values_list("reservation_id", flat=True)), key=str):
        reservation = _find(reservation_id)
        now = timezone.now()
        _expire_one(actor, reservation, now)
        if reservation.state == "ACTIVE":
            reservation.state = "RELEASED"
            reservation.save(using="default", update_fields=["state"])
            _event(actor, "reservation.invalidated", reservation=reservation,
                   details={"lot_id": str(lot_id) if lot_id else None, "location_id": str(location_id) if location_id else None})


@inventory_transaction
def query_availability(*, principal: object, variant_id: UUID) -> AvailabilitySnapshot:
    _actor(principal, Capability.READ_AVAILABILITY)
    _uuid(variant_id)
    if not catalog_references.variant_exists(variant_id=variant_id):
        raise InventoryVariantNotFoundError("Variant not found")
    return _availability(variant_id, timezone.now())


@inventory_transaction
def create_reservation(*, principal: object, operation_id: UUID, source: str, reference: str,
                       expires_at: datetime, lines: tuple[ReservationLine, ...]) -> dict:
    actor = _actor(principal, Capability.RESERVE_STOCK)
    source, reference = _text(source, 40), _text(reference, 120)
    if not isinstance(expires_at, datetime) or not timezone.is_aware(expires_at):
        raise InventoryValidationError("Explicit timezone-aware expiry required")
    if not isinstance(lines, tuple) or not 1 <= len(lines) <= 30 or any(not isinstance(line, ReservationLine) for line in lines):
        raise InventoryValidationError("Between 1 and 30 typed lines required")
    normalized = sorted(lines, key=lambda line: str(_uuid(line.variant_id)))
    if len({line.variant_id for line in normalized}) != len(normalized):
        raise InventoryValidationError("Duplicate variant")
    for line in normalized:
        _count(line.quantity)
    payload = {"source": source, "reference": reference,
               "expires_at": expires_at.astimezone(datetime_timezone.utc).isoformat(),
               "lines": [[str(line.variant_id), line.quantity] for line in normalized]}
    digest, prior = _operation(actor, operation_id, "reserve", payload)
    if prior:
        return prior.result
    now = timezone.now()
    if expires_at <= now:
        raise InventoryValidationError("Reservation expiry must be in the future")
    selected = []
    for line in normalized:
        if not catalog_references.variant_exists(variant_id=line.variant_id):
            raise InventoryVariantNotFoundError("Variant not found")
        snapshot = _availability(line.variant_id, now)
        if snapshot.available < line.quantity:
            raise StockConflictError("Insufficient eligible unreserved stock")
        remaining = line.quantity
        ordered = sorted(snapshot.balances, key=lambda row: (row.expires_on is None,
            row.expires_on or "", row.lot_code, str(row.lot_id), row.location_code, str(row.location_id)))
        for row in ordered:
            quantity = min(remaining, row.available)
            if quantity:
                selected.append((row.balance_id, quantity))
                remaining -= quantity
            if remaining == 0:
                break
    reservation = StockReservation.objects.using("default").create(operation_id=operation_id,
        source=source, reference=reference, actor_id=actor.actor_id, created_at=now, expires_at=expires_at)
    _event(actor, "reservation.created", reservation=reservation, operation_id=operation_id)
    for balance_id, quantity in selected:
        allocation = ReservationAllocation.objects.using("default").create(
            reservation=reservation, balance_id=balance_id, quantity=quantity)
        _event(actor, "reservation.allocated", reservation=reservation, operation_id=operation_id,
               details={"allocation_id": str(allocation.pk), "balance_id": str(balance_id), "quantity": str(quantity)})
    return _save_operation(actor, operation_id, "reserve", digest, _snapshot(reservation, now), reservation)


@inventory_transaction
def read_reservation(*, principal: object, reservation_id: UUID) -> dict:
    _actor(principal, Capability.READ_RESERVATION)
    return _snapshot(_find(reservation_id), timezone.now())


@inventory_transaction
def list_reservations(*, principal: object, variant_id: UUID | None = None) -> tuple[dict, ...]:
    _actor(principal, Capability.READ_RESERVATION)
    query = StockReservation.objects.using("default").all()
    if variant_id is not None:
        _uuid(variant_id)
        query = query.filter(allocations__balance__lot__variant_id=variant_id).distinct()
    now = timezone.now()
    return tuple(_snapshot(r, now) for r in query.order_by("-created_at", "id")[:101])


@inventory_transaction
def query_operation(*, principal: object, operation_id: UUID) -> dict:
    _actor(principal, Capability.READ_RESERVATION)
    operation = InventoryOperation.objects.using("default").filter(pk=_uuid(operation_id)).first()
    if operation is None:
        raise InventoryVariantNotFoundError("Operation not found; absence is not proof of failure")
    return {"operation_id": str(operation.pk), "action": operation.action, "result": operation.result}


@inventory_transaction
def transition_reservation(*, principal: object, operation_id: UUID, reservation_id: UUID, action: str) -> dict:
    capabilities = {"release": Capability.RELEASE_RESERVATION, "expire": Capability.EXPIRE_RESERVATION,
                    "consume": Capability.CONSUME_RESERVATION}
    if action not in capabilities:
        raise InventoryValidationError("Unknown transition")
    actor = _actor(principal, capabilities[action])
    _uuid(reservation_id)
    digest, prior = _operation(actor, operation_id, action, {"reservation_id": str(reservation_id)})
    if prior:
        return prior.result
    reservation = _find(reservation_id)
    now = timezone.now()
    _expire_one(actor, reservation, now, operation_id)
    target = {"release": "RELEASED", "expire": "EXPIRED", "consume": "CONSUMED"}[action]
    if reservation.state == target:
        return _save_operation(actor, operation_id, action, digest, _snapshot(reservation, now), reservation)
    if reservation.state != "ACTIVE":
        # Expiry is an observable result, never interpreted as a consumption.
        if reservation.state == "EXPIRED":
            return _save_operation(actor, operation_id, action, digest, _snapshot(reservation, now), reservation)
        raise StockConflictError("Reservation is terminal")
    if action == "expire":
        raise StockConflictError("Reservation has not expired")
    if action == "consume":
        allocations = list(reservation.allocations.select_related("balance__lot", "balance__location").order_by("balance_id"))
        if any(not _eligible(a.balance, now) for a in allocations):
            reservation.state = "RELEASED"
            reservation.save(using="default", update_fields=["state"])
            _event(actor, "reservation.invalidated", reservation=reservation, operation_id=operation_id)
            return _save_operation(actor, operation_id, action, digest, _snapshot(reservation, now), reservation)
        from .application import _append
        from .contracts import StockMovementKind
        by_variant = {}
        for allocation in allocations:
            by_variant.setdefault(allocation.balance.lot.variant_id, []).append(allocation)
        for variant_id in sorted(by_variant, key=str):
            position = StockPosition.objects.using("default").select_for_update().get(variant_id=variant_id)
            group = by_variant[variant_id]
            quantity = sum(a.quantity for a in group)
            if position.on_hand < quantity or position.version == MAX_STOCK_INTEGER:
                raise StockConflictError("Physical stock drift or exhausted version")
            before = position.on_hand
            for allocation in group:
                balance = allocation.balance
                if balance.quantity < allocation.quantity:
                    raise StockConflictError("Physical balance drift")
                balance.quantity -= allocation.quantity
                balance.save(using="default", update_fields=["quantity"])
            position.on_hand -= quantity
            position.version += 1
            position.save(using="default", update_fields=["on_hand", "version", "updated_at"])
            movement = _append(actor=actor, position=position, before=before, delta=-quantity,
                               kind=StockMovementKind.CONSUMPTION, reason=None)
            for allocation in group:
                allocation.stock_movement_id = movement.pk
                allocation.save(using="default", update_fields=["stock_movement_id"])
    reservation.state = target
    reservation.save(using="default", update_fields=["state"])
    _event(actor, "reservation.consumed" if action == "consume" else "reservation.released",
           reservation=reservation, operation_id=operation_id)
    return _save_operation(actor, operation_id, action, digest, _snapshot(reservation, now), reservation)


def release_reservation(*, principal: object, operation_id: UUID, reservation_id: UUID) -> dict:
    return transition_reservation(principal=principal, operation_id=operation_id, reservation_id=reservation_id, action="release")


def expire_reservation(*, principal: object, operation_id: UUID, reservation_id: UUID) -> dict:
    return transition_reservation(principal=principal, operation_id=operation_id, reservation_id=reservation_id, action="expire")


def consume_reservation(*, principal: object, operation_id: UUID, reservation_id: UUID) -> dict:
    return transition_reservation(principal=principal, operation_id=operation_id, reservation_id=reservation_id, action="consume")


@inventory_transaction
def expire_reservations(*, principal: object, limit: int = 100) -> int:
    """Bounded idempotent cleanup; correctness never depends on its scheduling."""
    actor = _actor(principal, Capability.EXPIRE_RESERVATION)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InventoryValidationError("Cleanup limit outside bounds")
    now = timezone.now()
    rows = list(StockReservation.objects.using("default").filter(state="ACTIVE", expires_at__lte=now)
                .order_by("expires_at", "id")[:limit])
    for reservation in rows:
        _expire_one(actor, reservation, now)
    return len(rows)


@inventory_transaction
def classify_stock(*, principal: object, operation_id: UUID, lot_id: UUID, location_id: UUID,
                   quantity: int, expected_stock_version: int, evidence: str) -> dict:
    actor = _actor(principal, Capability.CLASSIFY_STOCK)
    _uuid(lot_id); _uuid(location_id); _count(quantity); _count(expected_stock_version)
    evidence = _text(evidence, 255)
    digest, prior = _operation(actor, operation_id, "classify", {"lot_id": str(lot_id),
        "location_id": str(location_id), "quantity": quantity, "expected_stock_version": expected_stock_version, "evidence": evidence})
    if prior:
        return prior.result
    lot = StockLot.objects.using("default").filter(pk=lot_id).first()
    location = StockLocation.objects.using("default").filter(pk=location_id, active=True).first()
    if lot is None or location is None:
        raise InventoryVariantNotFoundError("Lot or location not found")
    position = StockPosition.objects.using("default").select_for_update().filter(variant_id=lot.variant_id).first()
    snapshot = _availability(lot.variant_id, timezone.now())
    if position is None or position.version != expected_stock_version or quantity > snapshot.unallocated:
        raise StockConflictError("Verified count exceeds unallocated stock or stock changed")
    balance, unused = StockLotBalance.objects.using("default").get_or_create(lot=lot, location=location, defaults={"quantity": 0})
    balance.quantity += quantity
    balance.save(using="default", update_fields=["quantity"])
    _event(actor, "stock.classified", operation_id=operation_id, details={"lot_id": str(lot.pk),
        "location_id": str(location.pk), "quantity": str(quantity), "evidence": evidence,
        "stock_version": str(position.version), "provenance": "verified_present_count; historical_origin_not_inferred"})
    return _save_operation(actor, operation_id, "classify", digest, {"classified": str(quantity), "lot_id": str(lot.pk)})


@inventory_transaction
def set_location_eligibility(*, principal: object, location_id: UUID, eligible: bool,
                             expected_version: int, reason: str) -> dict:
    actor = _actor(principal, Capability.SET_LOCATION_ELIGIBILITY)
    _uuid(location_id); _count(expected_version); reason = _text(reason, 255)
    if type(eligible) is not bool:
        raise InventoryValidationError("Explicit eligibility required")
    location = StockLocation.objects.using("default").select_for_update().filter(pk=location_id).first()
    if location is None:
        raise InventoryVariantNotFoundError("Location not found")
    if location.version != expected_version or location.version == MAX_STOCK_INTEGER:
        raise StockConflictError("Location version changed")
    if location.commercial_eligible != eligible:
        if not eligible:
            _invalidate_allocations(actor=actor, location_id=location_id)
        location.commercial_eligible = eligible
        location.version += 1
        location.save(using="default", update_fields=["commercial_eligible", "version"])
        _event(actor, "location.eligibility", details={"location_id": str(location.pk), "eligible": eligible, "reason": reason})
    return {"location_id": str(location.pk), "eligible": location.commercial_eligible, "version": str(location.version)}
