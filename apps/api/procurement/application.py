"""Procurement commands. All writes use one default transaction and typed module contracts."""

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, localcontext
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from audit import application as audit_application
from audit.contracts import ProcurementAuditAction
from catalog import references as catalog_references
from catalog.calculations import calculate_amount
from catalog.contracts import PriceBasis, MeasurementUnit
from inventory.quantities import format_quantity
from identity import application as identity_application
from identity.contracts import Capability
from inventory import application as inventory_application
from inventory import lot_application as inventory_lots
from inventory.contracts import InventoryUnit, StockConflictError

from .contracts import OrderStatus, ProcurementConflictError, ProcurementNotFoundError, ProcurementValidationError
from .models import GoodsReceipt, ProcurementEvent, PurchaseOrder, PurchaseOrderLine, ReceiptLine, ReceiptAllocation, Supplier


_COST = re.compile(r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}\Z")
_MAX = 2**63 - 1
_ACTIONS = {
    "supplier.created": ProcurementAuditAction.SUPPLIER_CREATED,
    "supplier.updated": ProcurementAuditAction.SUPPLIER_UPDATED,
    "order.created": ProcurementAuditAction.ORDER_CREATED,
    "order.updated": ProcurementAuditAction.ORDER_UPDATED,
    "order.confirmed": ProcurementAuditAction.ORDER_CONFIRMED,
    "order.received": ProcurementAuditAction.ORDER_RECEIVED,
}


def _uuid(value):
    if not isinstance(value, UUID):
        raise ProcurementValidationError("UUID required")
    return value


def _version(value):
    if type(value) is not int or not 1 <= value <= _MAX:
        raise ProcurementValidationError("positive expected_version required")
    return value


def _count(value, *, allow_zero=False):
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= _MAX:
        raise ProcurementValidationError("EACH quantity outside technical bounds")
    return value


def _name(value):
    if not isinstance(value, str):
        raise ProcurementValidationError("supplier name required")
    name = unicodedata.normalize("NFKC", value).strip()
    key = name.casefold()
    if not name or len(name) > 120 or len(key) > 120 or not name.isprintable():
        raise ProcurementValidationError("invalid supplier name")
    return name, key


def _constraint(error, name):
    return getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) == name


def supplier_snapshot(supplier):
    return {"id": str(supplier.pk), "name": supplier.name, "active": supplier.active,
            "version": str(supplier.version), "created_at": supplier.created_at.isoformat(),
            "updated_at": supplier.updated_at.isoformat()}


def order_snapshot(order):
    lines = list(order.lines.order_by("id"))
    return {"id": str(order.pk), "supplier_id": str(order.supplier_id),
            "supplier_name": order.supplier.name, "status": order.status,
            "version": str(order.version), "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
            "confirmed_at": order.confirmed_at.isoformat() if order.confirmed_at else None,
    "lines": [{"id": str(line.pk), "variant_id": str(line.variant_id),
                       "variant_label": line.variant_label, "variant_sku": line.variant_sku,
                       "product_name": line.product_name,
                       "unit": line.unit, "cost_basis": line.cost_basis, "quantity": format_quantity(line.quantity, line.unit),
                       "received_quantity": format_quantity(line.received_quantity, line.unit),
                       "pending_quantity": format_quantity(line.quantity - line.received_quantity, line.unit),
                       "unit_cost_ars": format(line.unit_cost_ars, ".2f")}
                      for line in lines],
            "total_cost_ars": _order_total(lines)}


def _order_total(lines):
    with localcontext() as context:
        context.prec = 60
        total = sum((calculate_amount(amount=line.unit_cost_ars, price_basis=PriceBasis(line.cost_basis),
            quantity=Decimal(format_quantity(line.quantity, line.unit)), unit=MeasurementUnit(line.unit)).amount_ars
            for line in lines), Decimal("0.00"))
        return format(total, ".2f")


def receipt_snapshot(receipt, *, include_allocations=False):
    return {"id": str(receipt.pk), "order_id": str(receipt.order_id),
            "operation_id": str(receipt.operation_id), "actor_id": receipt.actor_id,
            "recorded_at": receipt.recorded_at.isoformat(),
            "lines": [{"order_line_id": str(line.order_line_id),
                       "unit": line.order_line.unit, "delivered_quantity": format_quantity(line.delivered_quantity, line.order_line.unit),
                       "accepted_quantity": format_quantity(line.accepted_quantity, line.order_line.unit),
                       "difference_reason": line.difference_reason,
                       "stock_movement_id": str(line.stock_movement_id) if line.stock_movement_id else None,
                       "allocations": ([{"lot_id": str(item.lot_id), "location_id": str(item.location_id),
                                        "quantity": format_quantity(item.quantity, line.order_line.unit)} for item in line.allocations.order_by("id")]
                                       if include_allocations else None)}
                      for line in receipt.lines.order_by("id")]}


def _event(*, actor, action, supplier=None, order=None, receipt=None):
    snapshot = order_snapshot(order) if order is not None else supplier_snapshot(supplier)
    event = ProcurementEvent.objects.using("default").create(
        supplier=supplier or order.supplier, order=order,
        receipt_id=receipt.pk if receipt else None, action=action,
        version=order.version if order else supplier.version,
        actor_id=actor.actor_id, snapshot=snapshot,
    )
    audit_application.record_procurement_event(
        actor=actor, action=_ACTIONS[action], event_id=event.pk,
        supplier_id=event.supplier_id, order_id=event.order_id,
        receipt_id=event.receipt_id, version=event.version,
    )


def create_supplier(*, principal, name):
    actor = identity_application.require_capability(principal=principal, capability=Capability.MANAGE_SUPPLIER)
    name, key = _name(name)
    try:
        with transaction.atomic(using="default"):
            supplier = Supplier.objects.using("default").create(name=name, name_key=key)
            _event(actor=actor, action="supplier.created", supplier=supplier)
    except IntegrityError as error:
        if _constraint(error, "proc_supplier_name_unique"):
            raise ProcurementConflictError("supplier name already exists") from error
        raise
    return supplier


def update_supplier(*, principal, supplier_id, name, active, expected_version):
    actor = identity_application.require_capability(principal=principal, capability=Capability.MANAGE_SUPPLIER)
    _uuid(supplier_id)
    name, key = _name(name)
    _version(expected_version)
    if type(active) is not bool:
        raise ProcurementValidationError("active must be boolean")
    try:
        with transaction.atomic(using="default"):
            supplier = Supplier.objects.using("default").select_for_update().filter(pk=supplier_id).first()
            if supplier is None:
                raise ProcurementNotFoundError("supplier not found")
            if supplier.version != expected_version:
                raise ProcurementConflictError("supplier version changed")
            if (supplier.name, supplier.active) == (name, active):
                return supplier
            if supplier.version == _MAX:
                raise ProcurementValidationError("supplier version exhausted")
            supplier.name, supplier.name_key, supplier.active = name, key, active
            supplier.version += 1
            supplier.save(using="default", update_fields=["name", "name_key", "active", "version", "updated_at"])
            _event(actor=actor, action="supplier.updated", supplier=supplier)
    except IntegrityError as error:
        if _constraint(error, "proc_supplier_name_unique"):
            raise ProcurementConflictError("supplier name already exists") from error
        raise
    return supplier


def get_supplier(*, principal, supplier_id):
    identity_application.require_capability(principal=principal, capability=Capability.READ_SUPPLIER)
    _uuid(supplier_id)
    supplier = Supplier.objects.using("default").filter(pk=supplier_id).first()
    if supplier is None:
        raise ProcurementNotFoundError("supplier not found")
    return supplier


def list_suppliers(*, principal, search=""):
    identity_application.require_capability(principal=principal, capability=Capability.READ_SUPPLIER)
    if not isinstance(search, str) or len(search) > 120 or not search.isprintable():
        raise ProcurementValidationError("invalid supplier search")
    query = Supplier.objects.using("default").order_by("name_key", "id")
    if search.strip():
        query = query.filter(name_key__icontains=unicodedata.normalize("NFKC", search.strip()).casefold())
    return tuple(query[:51])


def _lines(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 30:
        raise ProcurementValidationError("between 1 and 30 order lines required")
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, dict) or not {"variant_id", "quantity", "unit", "unit_cost_ars"} <= set(value) or set(value)-{"variant_id", "quantity", "unit", "unit_cost_ars", "cost_basis"}:
            raise ProcurementValidationError("invalid order line")
        variant = _uuid(value["variant_id"])
        if variant in seen or value["unit"] not in ("EACH", "G"):
            raise ProcurementValidationError("duplicate Variant or non-EACH unit")
        seen.add(variant)
        quantity = _count(value["quantity"])
        cost = value["unit_cost_ars"]
        if not isinstance(cost, Decimal) or not _COST.fullmatch(format(cost, "f")):
            raise ProcurementValidationError("canonical ARS cost with two decimal places required")
        basis = value.get("cost_basis", "PER_VARIANT")
        if (value["unit"], basis) not in (("EACH", "PER_VARIANT"), ("G", "PER_KG")):
            raise ProcurementValidationError("Explicit compatible unit cost basis required")
        result.append((variant, quantity, cost, value["unit"], basis))
    return result


def _insert_lines(order, values):
    for variant_id, quantity, cost, unit, basis in values:
        variant = catalog_references.variant_reference(variant_id=variant_id)
        if variant is None:
            raise ProcurementNotFoundError("Variant not found")
        PurchaseOrderLine.objects.using("default").create(
            order=order, variant_id=variant_id, variant_label=variant["label"],
            variant_sku=variant["sku"], product_name=variant["product_name"],
            quantity=quantity, unit_cost_ars=cost, unit=unit, cost_basis=basis,
        )


def create_order(*, principal, supplier_id, lines):
    actor = identity_application.require_capability(principal=principal, capability=Capability.CREATE_PURCHASE_ORDER)
    _uuid(supplier_id)
    values = _lines(lines)
    with transaction.atomic(using="default"):
        supplier = Supplier.objects.using("default").select_for_update().filter(pk=supplier_id).first()
        if supplier is None:
            raise ProcurementNotFoundError("supplier not found")
        if not supplier.active:
            raise ProcurementConflictError("supplier inactive")
        order = PurchaseOrder.objects.using("default").create(supplier=supplier)
        _insert_lines(order, values)
        _event(actor=actor, action="order.created", order=order)
    return order


def update_order(*, principal, order_id, supplier_id, lines, expected_version):
    actor = identity_application.require_capability(principal=principal, capability=Capability.EDIT_PURCHASE_ORDER)
    _uuid(order_id); _uuid(supplier_id); _version(expected_version)
    values = _lines(lines)
    with transaction.atomic(using="default"):
        order = PurchaseOrder.objects.using("default").select_for_update().select_related("supplier").filter(pk=order_id).first()
        if order is None:
            raise ProcurementNotFoundError("order not found")
        if order.version != expected_version or order.status != OrderStatus.DRAFT.value:
            raise ProcurementConflictError("order is not the expected draft")
        if order.version == _MAX:
            raise ProcurementValidationError("order version exhausted")
        supplier = Supplier.objects.using("default").select_for_update().filter(pk=supplier_id).first()
        if supplier is None:
            raise ProcurementNotFoundError("supplier not found")
        if not supplier.active:
            raise ProcurementConflictError("supplier inactive")
        order.lines.all().delete()
        order.supplier = supplier
        order.version += 1
        order.save(using="default", update_fields=["supplier", "version", "updated_at"])
        _insert_lines(order, values)
        _event(actor=actor, action="order.updated", order=order)
    return order


def confirm_order(*, principal, order_id, expected_version):
    actor = identity_application.require_capability(principal=principal, capability=Capability.CONFIRM_PURCHASE_ORDER)
    _uuid(order_id); _version(expected_version)
    with transaction.atomic(using="default"):
        order = PurchaseOrder.objects.using("default").select_for_update().select_related("supplier").filter(pk=order_id).first()
        if order is None:
            raise ProcurementNotFoundError("order not found")
        if order.version != expected_version or order.status != OrderStatus.DRAFT.value or not order.supplier.active:
            raise ProcurementConflictError("order cannot be confirmed from this state")
        if order.version == _MAX:
            raise ProcurementValidationError("order version exhausted")
        order.status = OrderStatus.CONFIRMED.value
        order.version += 1
        order.confirmed_at = timezone.now()
        order.save(using="default", update_fields=["status", "version", "confirmed_at", "updated_at"])
        _event(actor=actor, action="order.confirmed", order=order)
    return order


def get_order(*, principal, order_id):
    identity_application.require_capability(principal=principal, capability=Capability.READ_PURCHASE_ORDER)
    _uuid(order_id)
    order = PurchaseOrder.objects.using("default").select_related("supplier").filter(pk=order_id).first()
    if order is None:
        raise ProcurementNotFoundError("order not found")
    return order


def list_orders(*, principal):
    identity_application.require_capability(principal=principal, capability=Capability.READ_PURCHASE_ORDER)
    return tuple(PurchaseOrder.objects.using("default").select_related("supplier").order_by("-created_at", "-id")[:51])


def _receipt_values(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 30:
        raise ProcurementValidationError("between 1 and 30 receipt lines required")
    normalized, seen = [], set()
    for value in values:
        required = {"order_line_id", "delivered_quantity", "accepted_quantity", "difference_reason", "expected_stock_version"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"allocations", "unit"}:
            raise ProcurementValidationError("invalid receipt line")
        line_id = _uuid(value["order_line_id"])
        if line_id in seen:
            raise ProcurementValidationError("duplicate receipt line")
        seen.add(line_id)
        delivered = _count(value["delivered_quantity"])
        accepted = _count(value["accepted_quantity"], allow_zero=True)
        if accepted > delivered:
            raise ProcurementValidationError("accepted quantity exceeds delivered")
        reason = value["difference_reason"]
        if accepted < delivered:
            if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
                raise ProcurementValidationError("difference reason required")
            reason = reason.strip()
        elif reason is not None:
            raise ProcurementValidationError("difference reason requires a difference")
        stock_version = value["expected_stock_version"]
        if stock_version is not None:
            _version(stock_version)
        if accepted == 0 and stock_version is not None:
            raise ProcurementValidationError("no stock version for a rejected line")
        accounting_unit = value.get("unit", "EACH")
        if accounting_unit not in ("EACH", "G"):
            raise ProcurementValidationError("Explicit accounting unit required")
        allocations = value.get("allocations")
        if allocations is not None:
            if not isinstance(allocations, list) or not 1 <= len(allocations) <= 10 or accepted == 0:
                raise ProcurementValidationError("invalid physical allocations")
            seen_allocations, normalized_allocations = set(), []
            for item in allocations:
                if not isinstance(item, dict) or set(item) != {"lot_id", "location_id", "quantity"}:
                    raise ProcurementValidationError("invalid physical allocation")
                lot_id, location_id, quantity = _uuid(item["lot_id"]), _uuid(item["location_id"]), _count(item["quantity"])
                if (lot_id, location_id) in seen_allocations:
                    raise ProcurementValidationError("duplicate physical allocation")
                seen_allocations.add((lot_id, location_id))
                normalized_allocations.append((lot_id, location_id, quantity))
            if sum(item[2] for item in normalized_allocations) != accepted:
                raise ProcurementValidationError("physical allocations must cover accepted quantity")
            allocations = sorted(normalized_allocations, key=lambda item: (str(item[0]), str(item[1])))
        normalized.append((line_id, delivered, accepted, reason, stock_version, allocations, accounting_unit))
    return sorted(normalized, key=lambda item: str(item[0]))


def receive_order(*, principal, order_id, operation_id, expected_version, lines):
    actor = identity_application.require_capability(principal=principal, capability=Capability.RECEIVE_PURCHASE_ORDER)
    _uuid(order_id); _uuid(operation_id); _version(expected_version)
    values = _receipt_values(lines)
    identity = {"order_id": str(order_id), "expected_version": expected_version,
                "lines": [[str(line_id), delivered, accepted, reason, stock_version]
                          + ([ [[str(lot), str(location), quantity] for lot, location, quantity in allocations] ]
                             if allocations is not None else []) + ([accounting_unit] if accounting_unit != "EACH" else [])
                          for line_id, delivered, accepted, reason, stock_version, allocations, accounting_unit in values]}
    request_sha = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    try:
        with transaction.atomic(using="default"):
            order = PurchaseOrder.objects.using("default").select_for_update().select_related("supplier").filter(pk=order_id).first()
            if order is None:
                raise ProcurementNotFoundError("order not found")
            existing = GoodsReceipt.objects.using("default").filter(operation_id=operation_id).first()
            if existing is not None:
                if existing.order_id != order_id or existing.request_sha256 != request_sha:
                    raise ProcurementConflictError("operation identity reused with different content")
                return order, existing
            if order.version != expected_version or order.status not in (OrderStatus.CONFIRMED.value, OrderStatus.PARTIAL.value):
                raise ProcurementConflictError("order changed or is not receivable")
            if order.version == _MAX:
                raise ProcurementValidationError("order version exhausted")
            available = {line.pk: line for line in order.lines.select_for_update()}
            if any(line_id not in available or delivered > available[line_id].quantity - available[line_id].received_quantity
                   for line_id, delivered, unused, unused_reason, unused_version, unused_allocations, unused_unit in values):
                raise ProcurementConflictError("receipt exceeds pending order quantity")
            receipt = GoodsReceipt.objects.using("default").create(
                order=order, operation_id=operation_id, request_sha256=request_sha, actor_id=actor.actor_id)
            for line_id, delivered, accepted, reason, stock_version, allocations, accounting_unit in values:
                line = available[line_id]
                if line.unit != accounting_unit:
                    raise ProcurementValidationError("Receipt unit differs from order")
                movement_id = None
                if accepted:
                    result = inventory_application.receive_stock_for_purchase(
                        principal=principal, variant_id=line.variant_id, unit=InventoryUnit(accounting_unit),
                        quantity=accepted, expected_version=stock_version)
                    movement_id = result.movement_id
                    if allocations is not None:
                        inventory_lots.allocate_purchase_receipt(
                            principal=principal, variant_id=line.variant_id, movement_id=movement_id,
                            allocations=[{"lot_id": lot_id, "location_id": location_id, "quantity": quantity}
                                         for lot_id, location_id, quantity in allocations])
                receipt_line = ReceiptLine.objects.using("default").create(
                    receipt=receipt, order_line=line, delivered_quantity=delivered,
                    accepted_quantity=accepted, difference_reason=reason, stock_movement_id=movement_id)
                if allocations is not None:
                    ReceiptAllocation.objects.using("default").bulk_create([
                        ReceiptAllocation(receipt_line=receipt_line, lot_id=lot_id, location_id=location_id,
                                          quantity=quantity) for lot_id, location_id, quantity in allocations])
                line.received_quantity += accepted
                line.save(using="default", update_fields=["received_quantity"])
            order.status = (OrderStatus.COMPLETE.value if all(line.received_quantity == line.quantity for line in available.values())
                            else OrderStatus.PARTIAL.value)
            order.version += 1
            order.save(using="default", update_fields=["status", "version", "updated_at"])
            _event(actor=actor, action="order.received", order=order, receipt=receipt)
    except StockConflictError as error:
        raise ProcurementConflictError("stock version changed") from error
    except IntegrityError as error:
        if _constraint(error, "proc_receipt_operation_unique"):
            raise ProcurementConflictError("operation identity already recorded") from error
        raise
    return order, receipt


def get_receipt_by_operation(*, principal, operation_id):
    identity_application.require_capability(principal=principal, capability=Capability.READ_PURCHASE_ORDER)
    _uuid(operation_id)
    receipt = GoodsReceipt.objects.using("default").filter(operation_id=operation_id).first()
    if receipt is None:
        raise ProcurementNotFoundError("receipt not found")
    return receipt
