from dataclasses import FrozenInstanceError
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase

from audit.models import InventoryMovementAuditEntry
from catalog.application import get_current_variant_price, set_variant_measurement_spec, set_variant_price
from catalog.contracts import MeasurementUnit
from catalog.models import Product, Variant
from inventory.application import adjust_stock, get_stock, list_stock_movements, receive_stock
from inventory.contracts import (
    MAX_STOCK_INTEGER, InventoryUnit, InventoryValidationError, InventoryVariantNotFoundError,
    StockConflictError, StockMovementKind, StockNotInitializedError,
)
from inventory.models import StockMovement, StockPosition


def assert_test_database(test):
    test.assertEqual(settings.SETTINGS_MODULE, "config.settings.test")
    test.assertEqual(connection.vendor, "postgresql")
    test.assertEqual(connection.settings_dict["NAME"], "test_mira")
    test.assertEqual(connection.settings_dict["TEST"]["NAME"], "test_mira")
    test.assertFalse(connection.settings_dict["TEST"].get("MIRROR"))
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        test.assertEqual(cursor.fetchone(), ("test_mira",))


def make_variant():
    product = Product.objects.create(name="Fictional inventory product")
    return Variant.objects.create(product=product, label="Fictional presentation", sku="FICTIONAL-" + uuid4().hex)


def make_principal(*, username="fictional-inventory-operator", grant=True):
    password = "fictional-test-password-only"
    user = get_user_model().objects.create_user(username=username, password=password)
    if grant:
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="inventory"))
    principal = authenticate(username=username, password=password)
    assert principal is not None
    return user, principal


def assert_chain(test, variant, actor_id):
    position = StockPosition.objects.get(variant_id=variant.pk)
    movements = list(StockMovement.objects.filter(position=position).order_by("sequence"))
    audits = list(InventoryMovementAuditEntry.objects.filter(position_id=position.pk).order_by("sequence"))
    test.assertEqual(len(movements), len(audits))
    test.assertTrue(movements)
    balance = 0
    for sequence, (movement, audit) in enumerate(zip(movements, audits), 1):
        test.assertEqual(movement.sequence, sequence)
        test.assertEqual(movement.quantity_before, balance)
        balance += movement.delta
        test.assertEqual(movement.quantity_after, balance)
        test.assertGreaterEqual(balance, 0)
        test.assertEqual((audit.actor_id, audit.variant_id, audit.movement_id, audit.sequence),
                         (str(actor_id), variant.pk, movement.pk, sequence))
        test.assertEqual((audit.quantity_before, audit.delta, audit.quantity_after, audit.unit),
                         (movement.quantity_before, movement.delta, movement.quantity_after, "EACH"))
        test.assertEqual(audit.action, "inventory.stock_received" if movement.kind == "RECEIPT" else "inventory.stock_adjusted")
        test.assertEqual(audit.result, "success")
    test.assertEqual(position.on_hand, sum(m.delta for m in movements))
    test.assertEqual((position.on_hand, position.version), (balance, movements[-1].sequence))


class InventoryApplicationTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user, self.principal = make_principal()
        self.variant = make_variant()

    def receive(self, quantity=10, expected=None, **overrides):
        values = dict(principal=self.principal, variant_id=self.variant.pk, unit=InventoryUnit.EACH,
                      quantity=quantity, expected_version=expected)
        values.update(overrides)
        return receive_stock(**values)

    def adjust(self, target=8, expected=1, **overrides):
        values = dict(principal=self.principal, variant_id=self.variant.pk, new_on_hand=target,
                      reason="  fictional recount  ", expected_version=expected)
        values.update(overrides)
        return adjust_stock(**values)

    def test_uninitialized_and_missing_variant_have_distinct_results(self):
        self.assertIsNone(get_stock(principal=self.principal, variant_id=self.variant.pk))
        self.assertEqual(list_stock_movements(principal=self.principal, variant_id=self.variant.pk), ())
        with self.assertRaises(StockNotInitializedError):
            self.adjust()
        for operation in (
            lambda: get_stock(principal=self.principal, variant_id=uuid4()),
            lambda: list_stock_movements(principal=self.principal, variant_id=uuid4()),
            lambda: self.receive(variant_id=uuid4()), lambda: self.adjust(variant_id=uuid4()),
        ):
            with self.assertRaises(InventoryVariantNotFoundError):
                operation()
        self.assertFalse(StockPosition.objects.exists())

    def test_receipts_and_adjustments_reconcile_and_zero_retains_position(self):
        first = self.receive()
        self.assertEqual((first.on_hand, first.version, first.unit), (10, 1, InventoryUnit.EACH))
        self.receive(3, 1)
        self.adjust(8, 2)
        zero = self.adjust(0, 3)
        self.assertEqual((zero.position_id, zero.on_hand, zero.version), (first.position_id, 0, 4))
        self.assertEqual(get_stock(principal=self.principal, variant_id=self.variant.pk), zero)
        history = list_stock_movements(principal=self.principal, variant_id=self.variant.pk)
        self.assertIsInstance(history, tuple)
        self.assertEqual([m.sequence for m in history], [4, 3, 2, 1])
        self.assertEqual([m.delta for m in history], [-8, -5, 3, 10])
        self.assertEqual(history[0].reason, "fictional recount")
        self.assertIsNone(history[-1].reason)
        self.assertEqual(history[-1].kind, StockMovementKind.RECEIPT)
        assert_chain(self, self.variant, self.user.pk)

    def test_snapshots_and_history_do_not_change_after_later_mutations_or_equal_clocks(self):
        first = self.receive()
        history = list_stock_movements(principal=self.principal, variant_id=self.variant.pk)
        original = history[0]
        with patch("django.utils.timezone.now", return_value=original.recorded_at):
            self.adjust(12)
            self.adjust(10, 2)
        self.assertEqual(first.on_hand, 10)
        self.assertEqual(history, (original,))
        self.assertEqual([m.sequence for m in list_stock_movements(principal=self.principal, variant_id=self.variant.pk)], [3, 2, 1])
        for value, attribute in ((first, "on_hand"), (original, "delta")):
            with self.assertRaises(FrozenInstanceError):
                setattr(value, attribute, 999)

    def test_noop_requires_current_version_and_does_not_write_or_audit(self):
        first = self.receive()
        self.assertEqual(self.adjust(10), first)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(InventoryMovementAuditEntry.objects.count(), 1)
        self.adjust(8)
        for operation in (lambda: self.adjust(8, 1), lambda: self.receive(10, None), lambda: self.receive(10, 1)):
            with self.assertRaises(StockConflictError):
                operation()
        self.assertEqual(StockMovement.objects.count(), 2)
        assert_chain(self, self.variant, self.user.pk)

    def test_invalid_types_bounds_units_versions_and_reasons_never_initialize_stock(self):
        for value in (True, False, 1.0, Decimal("1"), "1", None, 0, -1, MAX_STOCK_INTEGER + 1):
            with self.subTest(quantity=value), self.assertRaises(InventoryValidationError):
                self.receive(value)
        for value in (None, "EACH", MeasurementUnit.EACH, "KG"):
            with self.subTest(unit=value), self.assertRaises(InventoryValidationError):
                self.receive(unit=value)
        for value in (False, 0, -1, "1", MAX_STOCK_INTEGER + 1):
            with self.subTest(version=value), self.assertRaises(InventoryValidationError):
                self.receive(expected=value)
        with self.assertRaises(StockConflictError):
            self.receive(expected=1)
        with self.assertRaises(InventoryValidationError):
            self.receive(variant_id="invalid")
        self.assertFalse(StockPosition.objects.exists())
        self.receive()
        for value in (None, "", "   ", "\t\n", "x" * 256, 1):
            with self.subTest(reason=value), self.assertRaises(InventoryValidationError):
                self.adjust(reason=value)
        for value in (True, Decimal("2"), 2.0, "2", -1, MAX_STOCK_INTEGER + 1):
            with self.subTest(target=value), self.assertRaises(InventoryValidationError):
                self.adjust(value)
        for value in (None, True, 0, -1, MAX_STOCK_INTEGER + 1):
            with self.subTest(expected=value), self.assertRaises(InventoryValidationError):
                self.adjust(expected=value)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_quantity_delta_and_version_boundaries_fail_without_partial_mutations(self):
        self.receive(MAX_STOCK_INTEGER)
        with self.assertRaises(InventoryValidationError):
            self.receive(1, 1)
        self.adjust(0)
        self.adjust(MAX_STOCK_INTEGER, 2)
        history = list_stock_movements(principal=self.principal, variant_id=self.variant.pk)
        self.assertEqual([m.delta for m in history], [MAX_STOCK_INTEGER, -MAX_STOCK_INTEGER, MAX_STOCK_INTEGER])
        assert_chain(self, self.variant, self.user.pk)
        # Deliberately privileged test corruption isolates version overflow.
        StockPosition.objects.filter(variant_id=self.variant.pk).update(version=MAX_STOCK_INTEGER, on_hand=0)
        before = list(StockPosition.objects.values())
        for operation in (lambda: self.adjust(1, MAX_STOCK_INTEGER), lambda: self.receive(1, MAX_STOCK_INTEGER)):
            with self.assertRaises(InventoryValidationError):
                operation()
        self.assertEqual(list(StockPosition.objects.values()), before)
        self.assertEqual(StockMovement.objects.count(), 3)

    def test_inventory_is_independent_of_activation_price_and_measurement(self):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog"))
        set_variant_measurement_spec(principal=self.principal, variant_id=self.variant.pk,
                                    sale_unit=MeasurementUnit.KG, net_content_amount=None,
                                    net_content_unit=None, expected_version=None)
        price = set_variant_price(principal=self.principal, variant_id=self.variant.pk,
                                  amount=Decimal("12.34"), currency="ARS", expected_revision_id=None)
        self.receive()
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.pk), price)
        self.variant.product.refresh_from_db()
        self.assertFalse(self.variant.product.is_active)
        self.assertIsNone(self.variant.product.category_id)
        self.assertEqual(StockPosition.objects.get().accounting_unit, "EACH")
