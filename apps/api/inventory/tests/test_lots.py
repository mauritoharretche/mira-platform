from datetime import timedelta
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from audit.models import InventoryLotAuditEntry, InventoryMovementAuditEntry
from inventory.application import adjust_stock, receive_stock
from inventory.contracts import InventoryUnit, StockConflictError
from inventory.lot_application import (allocate_purchase_receipt, correct_lot, create_location, create_lot,
                                       list_lots, set_lot_status, transfer_lot)
from inventory.models import StockLotBalance, StockLotEvent, StockMovement, StockPosition
from inventory.tests.test_application import assert_test_database, make_principal, make_variant


class LotAccountingTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user, self.principal = make_principal()
        self.variant = make_variant()
        self.first = create_location(principal=self.principal, code="FIC-A", name="Fictional shelf A")
        self.second = create_location(principal=self.principal, code="FIC-B", name="Fictional shelf B")
        self.lot = create_lot(principal=self.principal, variant_id=self.variant.pk, code="FIC-LOT-A",
                              expires_on=timezone.localdate() + timedelta(days=5))

    def receipt(self, amount, version):
        return receive_stock(principal=self.principal, variant_id=self.variant.pk, unit=InventoryUnit.EACH,
                             quantity=amount, expected_version=version)

    def test_legacy_stock_remains_explicitly_unallocated_and_fefo_is_stable(self):
        self.receipt(9, None)
        state = list_lots(principal=self.principal, variant_id=self.variant.pk)
        self.assertEqual((state["on_hand"], state["unallocated"], state["fefo"]), ("9", "9", []))
        later = create_lot(principal=self.principal, variant_id=self.variant.pk, code="FIC-LOT-B",
                           expires_on=None)
        with transaction.atomic():
            result = self.receipt(5, 1)
            allocate_purchase_receipt(principal=self.principal, variant_id=self.variant.pk,
                                      movement_id=StockMovement.objects.get(position_id=result.position_id,
                                                                            sequence=result.version).pk,
                                      allocations=[{"lot_id": self.lot.pk, "location_id": self.first.pk, "quantity": 3},
                                                   {"lot_id": later.pk, "location_id": self.second.pk, "quantity": 2}])
        state = list_lots(principal=self.principal, variant_id=self.variant.pk)
        self.assertEqual((state["on_hand"], state["unallocated"], state["fefo"]),
                         ("14", "9", [str(self.lot.pk), str(later.pk)]))
        with self.assertRaises(StockConflictError):
            adjust_stock(principal=self.principal, variant_id=self.variant.pk, new_on_hand=4,
                         reason="fictional count", expected_version=2)
        self.assertEqual(StockPosition.objects.get(variant_id=self.variant.pk).on_hand, 14)

    def test_transfer_status_and_correction_are_attributed_without_rewriting_history(self):
        result = self.receipt(4, None)
        movement = StockMovement.objects.get(position_id=result.position_id)
        with transaction.atomic():
            allocate_purchase_receipt(principal=self.principal, variant_id=self.variant.pk,
                                      movement_id=movement.pk, allocations=[{"lot_id": self.lot.pk,
                                      "location_id": self.first.pk, "quantity": 4}])
        transfer_lot(principal=self.principal, lot_id=self.lot.pk, source_id=self.first.pk,
                     destination_id=self.second.pk, quantity=2, expected_version=1, reason="fictional transfer")
        set_lot_status(principal=self.principal, lot_id=self.lot.pk, status="QUARANTINE",
                       expected_version=2, reason="fictional inspection")
        self.assertEqual(list_lots(principal=self.principal, variant_id=self.variant.pk)["fefo"], [])
        with self.assertRaises(StockConflictError):
            transfer_lot(principal=self.principal, lot_id=self.lot.pk, source_id=self.first.pk,
                         destination_id=self.second.pk, quantity=1, expected_version=1, reason="stale")
        correct_lot(principal=self.principal, lot_id=self.lot.pk, location_id=self.second.pk,
                    delta=-1, expected_lot_version=3, expected_stock_version=1, reason="fictional count")
        self.assertEqual(StockPosition.objects.get(variant_id=self.variant.pk).on_hand, 3)
        self.assertEqual(sum(StockLotBalance.objects.values_list("quantity", flat=True)), 3)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertEqual(InventoryMovementAuditEntry.objects.count(), 2)
        self.assertEqual(StockLotEvent.objects.count(), InventoryLotAuditEntry.objects.count())
        self.assertEqual(StockLotEvent.objects.filter(action="lot.corrected").count(), 1)

    def test_expired_and_blocked_lots_are_excluded_and_audit_failure_rolls_back(self):
        expired = create_lot(principal=self.principal, variant_id=self.variant.pk, code="FIC-EXPIRED",
                             expires_on=timezone.localdate() - timedelta(days=1))
        self.receipt(2, None)
        with transaction.atomic():
            movement = StockMovement.objects.get(position__variant_id=self.variant.pk)
            allocate_purchase_receipt(principal=self.principal, variant_id=self.variant.pk,
                                      movement_id=movement.pk, allocations=[{"lot_id": expired.pk,
                                      "location_id": self.first.pk, "quantity": 2}])
        self.assertEqual(list_lots(principal=self.principal, variant_id=self.variant.pk)["fefo"], [])
        before = StockLotEvent.objects.count()
        from unittest.mock import patch
        with patch("audit.application.record_inventory_lot_event", side_effect=RuntimeError("fictional Audit failure")):
            with self.assertRaises(RuntimeError):
                set_lot_status(principal=self.principal, lot_id=expired.pk, status="BLOCKED",
                               expected_version=1, reason="fictional hold")
        expired.refresh_from_db()
        self.assertEqual((expired.status, expired.version, StockLotEvent.objects.count()), ("ACTIVE", 1, before))
