from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase
from django.utils.timezone import now

from catalog.models import Variant
from inventory.contracts import MAX_STOCK_INTEGER
from inventory.models import StockMovement, StockPosition
from inventory.tests.test_application import assert_test_database, make_variant


class InventoryDatabaseTests(TransactionTestCase):
    def setUp(self):
        assert_test_database(self)
        self.variant = make_variant()
        self.position = StockPosition.objects.create(variant_id=self.variant.pk, accounting_unit="EACH", on_hand=5, version=1)

    def insert(self, *, raw=False, **changes):
        values = dict(id=uuid4(), position_id=self.position.pk, sequence=1, kind="RECEIPT",
                      unit="EACH", quantity_before=0, delta=5, quantity_after=5, reason=None, recorded_at=now())
        values.update(changes)
        if raw:
            columns = ", ".join(connection.ops.quote_name(k) for k in values)
            with connection.cursor() as cursor:
                cursor.execute(f'INSERT INTO inventory_stockmovement ({columns}) VALUES ({", ".join(["%s"] * len(values))})', list(values.values()))
        else:
            StockMovement.objects.create(**values)
        return values["id"]

    def reject(self, constraint=None, **changes):
        count = StockMovement.objects.count()
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            self.insert(**changes)
        if constraint:
            self.assertEqual(failure.exception.__cause__.diag.constraint_name, constraint)
        self.assertEqual(StockMovement.objects.count(), count)

    def test_position_reference_uniqueness_units_nonnegative_and_version(self):
        for changes, constraint in (
            ({"variant_id": self.variant.pk}, "inventory_position_variant_unique"),
            ({"accounting_unit": "each"}, "inventory_position_unit_valid"),
            ({"accounting_unit": "KG"}, "inventory_position_unit_valid"),
            ({"version": 0}, "inventory_position_version_valid"),
            ({"on_hand": -1}, "inventory_position_quantity_valid"),
        ):
            values = dict(variant_id=make_variant().pk, accounting_unit="EACH", on_hand=0, version=1)
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(IntegrityError) as failure, transaction.atomic():
                StockPosition.objects.create(**values)
            self.assertEqual(failure.exception.__cause__.diag.constraint_name, constraint)
        for variant_id in (None, uuid4()):
            with self.subTest(variant=variant_id), self.assertRaises(IntegrityError), transaction.atomic():
                StockPosition.objects.create(variant_id=variant_id, accounting_unit="EACH", on_hand=0, version=1)

    def test_database_equation_delta_codes_and_initial_shape(self):
        cases = [
            ("inventory_movement_equation_valid", {"quantity_after": 6}),
            ("inventory_movement_delta_valid", {"delta": 0, "quantity_after": 0}),
            ("inventory_movement_unit_valid", {"unit": "each"}),
            ("inventory_movement_shape_valid", {"kind": "unknown", "sequence": 2}),
            ("inventory_movement_initial_valid", {"quantity_before": 1, "quantity_after": 6}),
            # Sequence zero also violates the stronger initial-state predicate;
            # PostgreSQL reports that constraint first. Do not change the rule.
            ("inventory_movement_initial_valid", {"sequence": 0}),
            ("inventory_movement_shape_valid", {"sequence": 2, "quantity_before": 5, "delta": -1, "quantity_after": 4}),
            ("inventory_movement_initial_valid", {"kind": "ADJUSTMENT", "reason": "fictional correction"}),
        ]
        for raw in (False, True):
            for constraint, changes in cases:
                with self.subTest(raw=raw, constraint=constraint):
                    self.reject(constraint, raw=raw, **changes)

    def test_nullable_reason_checks_do_not_accept_unknown_and_allow_valid_adjustments(self):
        for raw in (False, True):
            for reason in (None, "", "   "):
                with self.subTest(raw=raw, reason=reason):
                    self.reject("inventory_movement_shape_valid", raw=raw, sequence=2,
                                kind="ADJUSTMENT", quantity_before=5, delta=-2, quantity_after=3, reason=reason)
            self.reject("inventory_movement_shape_valid", raw=raw, reason="not allowed for receipt")
        self.insert()
        self.insert(raw=True, sequence=2, kind="ADJUSTMENT", quantity_before=5, delta=-5, quantity_after=0, reason="fictional recount")
        self.insert(raw=True, sequence=3, kind="ADJUSTMENT", quantity_before=0, delta=3, quantity_after=3, reason="fictional recount")
        self.assertEqual(StockMovement.objects.count(), 3)

    def test_not_null_and_references_are_effectively_enforced(self):
        for field in ("position_id", "sequence", "kind", "unit", "quantity_before", "delta", "quantity_after", "recorded_at"):
            with self.subTest(field=field):
                self.reject(**{field: None}, raw=True)
        self.reject(position_id=uuid4(), raw=True)

    def test_movement_sequence_and_protected_history_references(self):
        self.insert()
        self.reject("inventory_movement_sequence_unique")
        with self.assertRaises(ProtectedError):
            self.position.delete()
        with self.assertRaises(ProtectedError):
            self.variant.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM catalog_variant WHERE id = %s", [self.variant.pk])
        self.assertTrue(Variant.objects.filter(pk=self.variant.pk).exists())

    def test_bigint_boundaries_and_overflow_reject_bypassed_writes_without_persistence(self):
        self.insert(delta=MAX_STOCK_INTEGER, quantity_after=MAX_STOCK_INTEGER)
        self.insert(sequence=2, kind="ADJUSTMENT", quantity_before=MAX_STOCK_INTEGER,
                    delta=-MAX_STOCK_INTEGER, quantity_after=0, reason="fictional correction")
        for changes in ({"delta": MAX_STOCK_INTEGER + 1}, {"sequence": MAX_STOCK_INTEGER + 1},
                        {"quantity_before": MAX_STOCK_INTEGER + 1}, {"quantity_after": MAX_STOCK_INTEGER + 1}):
            with self.subTest(changes=changes), self.assertRaises(DatabaseError), transaction.atomic():
                self.insert(raw=True, **changes)
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_malformed_extreme_arithmetic_is_a_check_violation_not_bigint_subtraction_overflow(self):
        self.reject("inventory_movement_equation_valid", raw=True,
                    quantity_before=-(2**63), quantity_after=MAX_STOCK_INTEGER, delta=1)
