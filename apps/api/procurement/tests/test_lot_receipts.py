from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase

from audit.models import InventoryLotAuditEntry, InventoryMovementAuditEntry, ProcurementAuditEntry
from catalog.models import Product, Variant
from inventory.lot_application import create_location, create_lot, list_lots
from inventory.models import StockLotBalance, StockLotEvent, StockMovement, StockPosition
from procurement import application
from inventory.contracts import InventoryValidationError
from procurement.models import GoodsReceipt, ReceiptAllocation, ReceiptLine


class ProcurementLotReceiptTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        password = "fictional-lot-receipt-only"
        self.user = get_user_model().objects.create_user(username="fictional-lot-receiver", password=password)
        for app in ("inventory", "procurement"):
            self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label=app))
        self.principal = authenticate(username=self.user.username, password=password)
        product = Product.objects.create(name="Fictional lot order")
        self.variant = Variant.objects.create(product=product, label="Fictional pack", sku="FICTIONAL-LOT-ORDER")
        self.location = create_location(principal=self.principal, code="FIC-RECEIPT", name="Fictional receiving shelf")
        self.lot = create_lot(principal=self.principal, variant_id=self.variant.pk,
                              code="FIC-RECEIPT-LOT", expires_on=None)
        supplier = application.create_supplier(principal=self.principal, name="Fictional Lot Supplier")
        self.order = application.create_order(principal=self.principal, supplier_id=supplier.pk,
            lines=[{"variant_id": self.variant.pk, "quantity": 5, "unit": "EACH", "unit_cost_ars": Decimal("2.50")}])
        self.order = application.confirm_order(principal=self.principal, order_id=self.order.pk, expected_version=1)
        self.order_line = self.order.lines.get()

    def payload(self, accepted=3):
        return [{"order_line_id": self.order_line.pk, "delivered_quantity": accepted,
                 "accepted_quantity": accepted, "difference_reason": None, "expected_stock_version": None,
                 "allocations": [{"lot_id": self.lot.pk, "location_id": self.location.pk, "quantity": accepted}]}]

    def test_allocated_receipt_is_atomic_idempotent_and_reconciled(self):
        operation = uuid4()
        order, receipt = application.receive_order(principal=self.principal, order_id=self.order.pk,
            operation_id=operation, expected_version=2, lines=self.payload())
        line = receipt.lines.get()
        self.assertEqual((order.status, order.version, line.accepted_quantity), ("PARTIAL", 3, 3))
        self.assertEqual(ReceiptAllocation.objects.get(receipt_line=line).quantity, 3)
        self.assertIsNone(application.receipt_snapshot(receipt)["lines"][0]["allocations"])
        self.assertEqual(application.receipt_snapshot(receipt, include_allocations=True)["lines"][0]["allocations"][0]["quantity"], "3")
        self.assertEqual(StockLotBalance.objects.get(lot=self.lot, location=self.location).quantity, 3)
        self.assertEqual((StockPosition.objects.get(variant_id=self.variant.pk).on_hand,
                          list_lots(principal=self.principal, variant_id=self.variant.pk)["unallocated"]), (3, "0"))
        replay_order, replay = application.receive_order(principal=self.principal, order_id=self.order.pk,
            operation_id=operation, expected_version=2, lines=self.payload())
        self.assertEqual((replay.pk, replay_order.version), (receipt.pk, 3))
        self.assertEqual((GoodsReceipt.objects.count(), StockMovement.objects.count(), ReceiptAllocation.objects.count()), (1, 1, 1))
        self.assertEqual((InventoryMovementAuditEntry.objects.count(), StockLotEvent.objects.filter(action="lot.received").count()), (1, 1))
        self.assertEqual(StockLotEvent.objects.count(), InventoryLotAuditEntry.objects.count())
        self.assertEqual(ProcurementAuditEntry.objects.count(), 4)

    def test_wrong_lot_rolls_back_procurement_inventory_and_audit(self):
        other = Variant.objects.create(product=self.variant.product, label="Other", sku="FICTIONAL-OTHER")
        wrong_lot = create_lot(principal=self.principal, variant_id=other.pk, code="FIC-OTHER", expires_on=None)
        bad = self.payload()
        bad[0]["allocations"][0]["lot_id"] = wrong_lot.pk
        before = (GoodsReceipt.objects.count(), StockMovement.objects.count(),
                  InventoryMovementAuditEntry.objects.count(), ProcurementAuditEntry.objects.count())
        with self.assertRaises(InventoryValidationError):
            application.receive_order(principal=self.principal, order_id=self.order.pk,
                                      operation_id=uuid4(), expected_version=2, lines=bad)
        self.assertEqual((GoodsReceipt.objects.count(), StockMovement.objects.count(),
                          InventoryMovementAuditEntry.objects.count(), ProcurementAuditEntry.objects.count()), before)
        self.assertFalse(StockPosition.objects.filter(variant_id=self.variant.pk).exists())
        self.assertEqual(ReceiptLine.objects.count(), 0)
