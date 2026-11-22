from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase

from audit.models import InventoryMovementAuditEntry, ProcurementAuditEntry
from catalog.models import Product, Variant
from identity.contracts import AccessDenied
from inventory.models import StockMovement, StockPosition
from procurement import application
from procurement.contracts import ProcurementConflictError
from procurement.models import GoodsReceipt, ProcurementEvent, ReceiptLine, Supplier


class ProcurementApplicationTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        self.assertEqual(connection.settings_dict["TEST"]["NAME"], "test_mira")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        password = "fictional-procurement-test-only"
        self.user = get_user_model().objects.create_user(username="fictional-procurement", password=password)
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="procurement"))
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label="inventory", codename="receive_stock"))
        self.principal = authenticate(username=self.user.username, password=password)
        self.assertIsNotNone(self.principal)
        product = Product.objects.create(name="Fictional purchase test product")
        self.variants = [Variant.objects.create(product=product, label=f"Fictional item {i}",
                                                sku=f"FICTIONAL-PROC-{i}-{uuid4().hex}") for i in (1, 2)]

    def make_order(self):
        supplier = application.create_supplier(principal=self.principal, name="  Fictional Supply Co.  ")
        lines = [{"variant_id": variant.pk, "quantity": count, "unit": "EACH", "unit_cost_ars": cost}
                 for variant, count, cost in zip(self.variants, (10, 4), (Decimal("12.50"), Decimal("3.25")))]
        order = application.create_order(principal=self.principal, supplier_id=supplier.pk, lines=lines)
        return supplier, order, lines

    def receipt_lines(self, order, amounts, versions):
        return [{"order_line_id": line.pk, "delivered_quantity": delivered, "accepted_quantity": accepted,
                 "difference_reason": reason, "expected_stock_version": version}
                for line, (delivered, accepted, reason), version in zip(order.lines.order_by("-quantity"), amounts, versions)]

    def test_supplier_duplicate_search_edit_and_deactivation(self):
        supplier = application.create_supplier(principal=self.principal, name="Fictional Vendor")
        with self.assertRaises(ProcurementConflictError):
            application.create_supplier(principal=self.principal, name="  fictional vendor  ")
        self.assertEqual([s.pk for s in application.list_suppliers(principal=self.principal, search="VENDOR")], [supplier.pk])
        changed = application.update_supplier(principal=self.principal, supplier_id=supplier.pk,
                                              name="Fictional Vendor", active=False, expected_version=1)
        self.assertEqual((changed.active, changed.version), (False, 2))
        with self.assertRaises(ProcurementConflictError):
            application.update_supplier(principal=self.principal, supplier_id=supplier.pk,
                                        name="Other", active=True, expected_version=1)
        self.assertEqual(ProcurementEvent.objects.filter(supplier=supplier).count(), 2)
        self.assertEqual(ProcurementAuditEntry.objects.filter(supplier_id=supplier.pk).count(), 2)

    def test_draft_edit_confirm_and_order_history(self):
        supplier, order, lines = self.make_order()
        self.assertEqual(application.order_snapshot(order)["total_cost_ars"], "138.00")
        lines[0]["quantity"] = 12
        edited = application.update_order(principal=self.principal, order_id=order.pk,
                                          supplier_id=supplier.pk, lines=lines, expected_version=1)
        self.assertEqual((edited.status, edited.version), ("DRAFT", 2))
        confirmed = application.confirm_order(principal=self.principal, order_id=order.pk, expected_version=2)
        self.assertEqual((confirmed.status, confirmed.version), ("CONFIRMED", 3))
        with self.assertRaises(ProcurementConflictError):
            application.update_order(principal=self.principal, order_id=order.pk,
                                     supplier_id=supplier.pk, lines=lines, expected_version=3)
        self.assertEqual([event.action for event in order.events.order_by("version")],
                         ["order.created", "order.updated", "order.confirmed"])
        self.assertEqual(ProcurementAuditEntry.objects.filter(order_id=order.pk).count(), 3)

    def test_partial_complete_receipts_are_idempotent_and_reconcile_inventory_audit(self):
        unused, order, unused_lines = self.make_order()
        application.confirm_order(principal=self.principal, order_id=order.pk, expected_version=1)
        first_lines = self.receipt_lines(order, [(4, 3, "fictional damaged item"), (2, 2, None)], (None, None))
        operation = uuid4()
        part, first = application.receive_order(principal=self.principal, order_id=order.pk,
                                                operation_id=operation, expected_version=2, lines=first_lines)
        self.assertEqual((part.status, part.version), ("PARTIAL", 3))
        self.assertEqual(sorted(line["pending_quantity"] for line in application.order_snapshot(part)["lines"]), ["2", "7"])
        self.assertEqual(application.get_receipt_by_operation(principal=self.principal, operation_id=operation).pk, first.pk)
        replay_order, replay = application.receive_order(principal=self.principal, order_id=order.pk,
                                                         operation_id=operation, expected_version=2, lines=first_lines)
        self.assertEqual((replay.pk, replay_order.version), (first.pk, 3))
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        with self.assertRaises(ProcurementConflictError):
            changed = [dict(first_lines[0], accepted_quantity=4, difference_reason=None), first_lines[1]]
            application.receive_order(principal=self.principal, order_id=order.pk,
                                      operation_id=operation, expected_version=2, lines=changed)
        final_lines = self.receipt_lines(order, [(7, 7, None), (2, 2, None)], (1, 1))
        completed, final = application.receive_order(principal=self.principal, order_id=order.pk,
                                                     operation_id=uuid4(), expected_version=3, lines=final_lines)
        self.assertEqual((completed.status, completed.version), ("COMPLETE", 4))
        self.assertEqual([line["pending_quantity"] for line in application.order_snapshot(completed)["lines"]], ["0", "0"])
        self.assertEqual((GoodsReceipt.objects.count(), ReceiptLine.objects.count()), (2, 4))
        self.assertEqual((StockPosition.objects.count(), StockMovement.objects.count(),
                          InventoryMovementAuditEntry.objects.count()), (2, 4, 4))
        self.assertEqual(ProcurementAuditEntry.objects.filter(order_id=order.pk).count(), 4)
        self.assertEqual(set(ReceiptLine.objects.values_list("stock_movement_id", flat=True)),
                         set(StockMovement.objects.values_list("id", flat=True)))
        self.assertEqual(set(StockMovement.objects.values_list("id", flat=True)),
                         set(InventoryMovementAuditEntry.objects.values_list("movement_id", flat=True)))
        self.assertEqual(final.order_id, order.pk)

    def test_excess_receipt_and_audit_failure_leave_no_stock_or_receipt(self):
        unused, order, unused_lines = self.make_order()
        application.confirm_order(principal=self.principal, order_id=order.pk, expected_version=1)
        lines = self.receipt_lines(order, [(11, 11, None)], (None,))[:1]
        with self.assertRaises(ProcurementConflictError):
            application.receive_order(principal=self.principal, order_id=order.pk,
                                      operation_id=uuid4(), expected_version=2, lines=lines)
        self.assertFalse(GoodsReceipt.objects.exists())
        valid = self.receipt_lines(order, [(3, 3, None)], (None,))[:1]
        with patch("procurement.application.audit_application.record_procurement_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                application.receive_order(principal=self.principal, order_id=order.pk,
                                          operation_id=uuid4(), expected_version=2, lines=valid)
        self.assertFalse(GoodsReceipt.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(InventoryMovementAuditEntry.objects.exists())
        order.refresh_from_db()
        self.assertEqual((order.status, order.version), ("CONFIRMED", 2))

    def test_receipt_capability_requires_procurement_and_inventory_permissions(self):
        unused, order, unused_lines = self.make_order()
        application.confirm_order(principal=self.principal, order_id=order.pk, expected_version=1)
        values = self.receipt_lines(order, [(1, 1, None)], (None,))[:1]
        for app, codename in (("inventory", "receive_stock"), ("procurement", "receive_purchase_order")):
            permission = Permission.objects.get(content_type__app_label=app, codename=codename)
            self.user.user_permissions.remove(permission)
            with self.assertRaises(AccessDenied):
                application.receive_order(principal=self.principal, order_id=order.pk,
                                          operation_id=uuid4(), expected_version=2, lines=values)
            self.user.user_permissions.add(permission)
        self.assertFalse(GoodsReceipt.objects.exists())
        group = Group.objects.create(name="fictional procurement receiving")
        group.permissions.add(Permission.objects.get(content_type__app_label="procurement", codename="receive_purchase_order"),
                              Permission.objects.get(content_type__app_label="inventory", codename="receive_stock"))
        self.user.user_permissions.clear()
        self.user.groups.add(group)
        self.assertIsNotNone(application.receive_order(principal=self.principal, order_id=order.pk,
                                                        operation_id=uuid4(), expected_version=2, lines=values))
