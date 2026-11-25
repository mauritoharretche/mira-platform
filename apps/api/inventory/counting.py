"""Bounded physical counts and losses, within the existing Inventory transaction gate."""
from uuid import UUID

from django.utils import timezone

from audit import application as audit_application
from identity import application as identity_application
from identity.contracts import AccessDenied, Capability

from .application import _append, _quantity
from .contracts import InventoryUnit, InventoryValidationError, InventoryVariantNotFoundError, StockConflictError, StockMovementKind
from .locking import inventory_transaction
from .models import PhysicalCount, PhysicalCountLine, PhysicalInventoryEvent, PhysicalLoss, StockLocation, StockLotBalance, StockPosition
from .quantities import format_quantity
from .reservations import _operation, _save_operation, _reserved, _text, _uuid

REASONS = frozenset({"EXPIRATION", "DAMAGE", "QUALITY_REJECTION", "MEASUREMENT_DISCREPANCY", "INTERNAL_CONSUMPTION", "OTHER"})


def _actor(principal, capability):
    return identity_application.require_capability(principal=principal, capability=capability)


def _event(actor, action, *, count=None, loss=None, details=None):
    event = PhysicalInventoryEvent.objects.using("default").create(count=count, loss_id=loss.pk if loss else None,
        actor_id=actor.actor_id, action=action, details=details or {})
    audit_application.record_physical_inventory_event(actor=actor, event_id=event.pk, count_id=count.pk if count else None,
        loss_id=event.loss_id, action=action)


def _find(count_id):
    count = PhysicalCount.objects.using("default").select_for_update().filter(pk=_uuid(count_id)).first()
    if count is None: raise InventoryVariantNotFoundError("Count not found")
    return count


def _reviewer(principal):
    try:
        _actor(principal, Capability.REVIEW_PHYSICAL_COUNT)
        return True
    except AccessDenied:
        return False


def _snapshot(count, *, reveal=False):
    rows = []
    for line in count.lines.select_related("balance__lot").order_by("id"):
        row = {"id": str(line.pk), "balance_id": str(line.balance_id), "lot_id": str(line.balance.lot_id),
               "lot_code": line.balance.lot.code, "variant_id": str(line.balance.lot.variant_id), "unit": line.unit,
               "observed": format_quantity(line.observed_quantity, line.unit) if line.observed_quantity is not None else None,
               "reason": line.reason, "evidence": line.evidence, "stock_movement_id": str(line.stock_movement_id) if line.stock_movement_id else None}
        if reveal or not count.blind:
            row["expected"] = format_quantity(line.expected_quantity, line.unit)
            row["discrepancy"] = format_quantity(line.observed_quantity-line.expected_quantity, line.unit) if line.observed_quantity is not None else None
        rows.append(row)
    return {"id": str(count.pk), "location_id": str(count.location_id), "state": count.state, "blind": count.blind,
            "responsible_actor_id": count.responsible_actor_id, "created_at": count.created_at.isoformat(), "lines": rows,
            "history": [{"action":e.action,"actor_id":e.actor_id,"at":e.recorded_at.isoformat()} for e in count.events.order_by("recorded_at","id")[:200]], "history_truncated":count.events.count()>200}


@inventory_transaction
def create_count(*, principal: object, operation_id: UUID, location_id: UUID, balance_ids: tuple[UUID, ...], blind: bool) -> dict:
    actor = _actor(principal, Capability.CREATE_PHYSICAL_COUNT)
    _uuid(location_id)
    if type(blind) is not bool or not isinstance(balance_ids, tuple) or not 1 <= len(balance_ids) <= 100 or len(set(balance_ids)) != len(balance_ids):
        raise InventoryValidationError("Explicit bounded unique count scope required")
    for identifier in balance_ids: _uuid(identifier)
    digest, prior = _operation(actor, operation_id, "count.create", {"location_id": str(location_id), "balance_ids": sorted(map(str,balance_ids)), "blind":blind})
    if prior: return prior.result
    if not StockLocation.objects.using("default").filter(pk=location_id,active=True).exists():
        raise InventoryVariantNotFoundError("Location not found")
    balances = list(StockLotBalance.objects.using("default").filter(pk__in=balance_ids,location_id=location_id).select_related("lot").order_by("id"))
    if len(balances) != len(balance_ids): raise InventoryValidationError("Count scope must belong to one explicit location")
    count = PhysicalCount.objects.using("default").create(operation_id=operation_id,location_id=location_id,responsible_actor_id=actor.actor_id,blind=blind)
    for balance in balances:
        position = StockPosition.objects.using("default").get(variant_id=balance.lot.variant_id)
        PhysicalCountLine.objects.using("default").create(count=count,balance=balance,unit=position.accounting_unit,
            expected_quantity=balance.quantity,expected_stock_version=position.version,expected_lot_version=balance.lot.version)
    _event(actor,"count.created",count=count,details={"scope_size":len(balances),"blind":blind})
    return _save_operation(actor,operation_id,"count.create",digest,_snapshot(count,reveal=False))


@inventory_transaction
def record_observation(*, principal: object, operation_id: UUID, count_id: UUID, line_id: UUID,
                       observed: int, unit: InventoryUnit, reason: str, evidence: str) -> dict:
    actor = _actor(principal,Capability.RECORD_PHYSICAL_COUNT)
    _uuid(count_id);_uuid(line_id);_quantity(observed)
    if not isinstance(unit,InventoryUnit): raise InventoryValidationError("Explicit unit required")
    reason,evidence = _text(reason,255),_text(evidence,255)
    digest,prior = _operation(actor,operation_id,"count.observe",{"count_id":str(count_id),"line_id":str(line_id),"observed":observed,"unit":unit.value,"reason":reason,"evidence":evidence})
    if prior: return prior.result
    count = _find(count_id)
    line = count.lines.filter(pk=line_id).first()
    if count.state != "OPEN" or line is None or line.unit != unit.value: raise StockConflictError("Count is not open or unit differs")
    if count.events.count() >= 198: raise InventoryValidationError("Count observation history limit reached; open a new explicit scope")
    previous = line.observed_quantity
    line.observed_quantity,line.reason,line.evidence = observed,reason,evidence
    line.save(using="default",update_fields=["observed_quantity","reason","evidence"])
    _event(actor,"count.observed",count=count,details={"line_id":str(line_id),"previous":format_quantity(previous,unit) if previous is not None else None,"observed":format_quantity(observed,unit),"unit":unit.value,"reason":reason,"evidence":evidence})
    return _save_operation(actor,operation_id,"count.observe",digest,_snapshot(count,reveal=False))


def _correct(actor, balance, position, delta, kind, reason):
    if not delta: return None
    if balance.quantity+delta < _reserved(balance.pk,timezone.now()): raise StockConflictError("Physical correction would remove reserved stock")
    after = _quantity(position.on_hand+delta)
    if position.version == 2**63-1 or balance.lot.version == 2**63-1: raise InventoryValidationError("Version exhausted")
    before = position.on_hand
    balance.quantity = _quantity(balance.quantity+delta)
    balance.save(using="default",update_fields=["quantity"])
    balance.lot.version += 1
    balance.lot.save(using="default",update_fields=["version"])
    position.on_hand,position.version = after,position.version+1
    position.save(using="default",update_fields=["on_hand","version","updated_at"])
    return _append(actor=actor,position=position,before=before,delta=delta,kind=kind,reason=reason)


@inventory_transaction
def transition_count(*, principal: object, operation_id: UUID, count_id: UUID, action: str) -> dict:
    actor = _actor(principal,Capability.REVIEW_PHYSICAL_COUNT if action == "review" else Capability.COMPLETE_PHYSICAL_COUNT)
    if action not in ("review","complete"): raise InventoryValidationError("Invalid count transition")
    _uuid(count_id)
    digest,prior = _operation(actor,operation_id,"count."+action,{"count_id":str(count_id)})
    if prior: return prior.result
    count = _find(count_id)
    if count.state != ("OPEN" if action == "review" else "REVIEWED"): raise StockConflictError("Count state changed")
    lines = list(count.lines.select_related("balance__lot").order_by("id"))
    if any(line.observed_quantity is None for line in lines): raise InventoryValidationError("All observations required")
    if action == "complete":
        positions = {p.variant_id:p for p in StockPosition.objects.using("default").select_for_update().filter(variant_id__in={l.balance.lot.variant_id for l in lines})}
        for line in lines:
            position=positions[line.balance.lot.variant_id]
            if (position.version != line.expected_stock_version or position.accounting_unit != line.unit or
                line.balance.lot.version != line.expected_lot_version or line.balance.quantity != line.expected_quantity):
                raise StockConflictError("Physical count snapshot is stale")
        for line in lines:
            movement=_correct(actor,line.balance,positions[line.balance.lot.variant_id],line.observed_quantity-line.expected_quantity,StockMovementKind.COUNT,line.reason)
            if movement:
                line.stock_movement_id=movement.pk;line.save(using="default",update_fields=["stock_movement_id"])
    count.state="REVIEWED" if action == "review" else "COMPLETED"
    count.save(using="default",update_fields=["state"])
    _event(actor,"count.reviewed" if action == "review" else "count.completed",count=count)
    # Saved operation result stays blind; explicit review read controls disclosure.
    return _save_operation(actor,operation_id,"count."+action,digest,_snapshot(count,reveal=False))


@inventory_transaction
def read_count(*, principal: object, count_id: UUID) -> dict:
    _actor(principal,Capability.READ_PHYSICAL_INVENTORY)
    return _snapshot(_find(count_id),reveal=_reviewer(principal))


@inventory_transaction
def list_counts(*, principal: object) -> tuple[dict,...]:
    _actor(principal,Capability.READ_PHYSICAL_INVENTORY)
    reveal=_reviewer(principal)
    return tuple(_snapshot(c,reveal=reveal) for c in PhysicalCount.objects.using("default").order_by("-created_at","id")[:51])


@inventory_transaction
def write_off(*, principal: object, operation_id: UUID, balance_id: UUID, quantity: int, unit: InventoryUnit,
              expected_stock_version: int, reason_code: str, evidence: str) -> dict:
    actor=_actor(principal,Capability.WRITE_OFF_STOCK)
    _uuid(balance_id);_quantity(quantity,positive=True);_quantity(expected_stock_version,positive=True)
    if not isinstance(unit,InventoryUnit) or reason_code not in REASONS: raise InventoryValidationError("Explicit unit and loss reason code required")
    evidence=_text(evidence,255)
    digest,prior=_operation(actor,operation_id,"loss",{"balance_id":str(balance_id),"quantity":quantity,"unit":unit.value,"expected_stock_version":expected_stock_version,"reason_code":reason_code,"evidence":evidence})
    if prior:return prior.result
    balance=StockLotBalance.objects.using("default").select_related("lot").filter(pk=balance_id).first()
    if balance is None:raise InventoryVariantNotFoundError("Balance not found")
    position=StockPosition.objects.using("default").select_for_update().get(variant_id=balance.lot.variant_id)
    if position.accounting_unit != unit.value or position.version != expected_stock_version:raise StockConflictError("Stock version or unit changed")
    if quantity>balance.quantity:raise StockConflictError("Loss exceeds physical balance")
    movement=_correct(actor,balance,position,-quantity,StockMovementKind.WRITE_OFF,reason_code+": "+evidence[:220])
    loss=PhysicalLoss.objects.using("default").create(operation_id=operation_id,balance=balance,quantity=quantity,
        unit=unit.value,reason_code=reason_code,evidence=evidence,actor_id=actor.actor_id,stock_movement_id=movement.pk)
    _event(actor,"loss.recorded",loss=loss,details={"quantity":format_quantity(quantity,unit),"unit":unit.value,"stock_movement_id":str(movement.pk)})
    return _save_operation(actor,operation_id,"loss",digest,{"loss_id":str(loss.pk),"stock_movement_id":str(movement.pk),"quantity":format_quantity(quantity,unit),"unit":unit.value})


@inventory_transaction
def query_physical_operation(*, principal: object, operation_id: UUID) -> dict:
    _actor(principal,Capability.READ_PHYSICAL_INVENTORY)
    from .models import InventoryOperation
    row=InventoryOperation.objects.using("default").filter(pk=_uuid(operation_id),action__in=["count.create","count.observe","count.review","count.complete","loss"]).first()
    if row is None:raise InventoryVariantNotFoundError("Operation not found; absence is not proof of failure")
    return {"operation_id":str(row.pk),"action":row.action,"result":row.result}
