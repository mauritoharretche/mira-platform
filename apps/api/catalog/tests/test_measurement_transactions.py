from decimal import Decimal
from queue import Queue
from threading import Event, Thread
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from audit import application as audit_application
from audit.models import CatalogMeasurementAuditEntry, CatalogPriceAuditEntry
from catalog.application import (
    MeasurementSpecConflictError, get_variant_measurement_spec,
    set_variant_measurement_spec, set_variant_price,
)
from catalog.contracts import MeasurementUnit, PriceBasis, PriceSnapshot, VariantMeasurementSpecSnapshot
from catalog.models import Product, Variant, VariantMeasurementSpec, VariantPriceRevision


class MeasurementTransactionTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        password = "fictional-test-password-only"
        self.user = get_user_model().objects.create_user(username="fictional-measurement-writer", password=password)
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog"))
        self.principal = authenticate(username=self.user.username, password=password)
        self.assertIsNotNone(self.principal)
        product = Product.objects.create(name="Fictional transaction product")
        self.variant = Variant.objects.create(product=product, label="Presentation", sku="TX-MEASURE-001")

    def write(self, *, expected=None, sale=MeasurementUnit.EACH, amount=None, unit=None):
        return set_variant_measurement_spec(
            principal=self.principal, variant_id=self.variant.id, expected_version=expected,
            sale_unit=sale, net_content_amount=amount, net_content_unit=unit,
        )

    def read(self):
        return get_variant_measurement_spec(principal=self.principal, variant_id=self.variant.id)

    def assert_audit_chain(self):
        entries = list(CatalogMeasurementAuditEntry.objects.order_by("new_version"))
        previous = (None, None, None)
        for version, entry in enumerate(entries, 1):
            self.assertEqual(entry.actor_id, str(self.user.pk))
            self.assertEqual(entry.variant_id, self.variant.id)
            self.assertEqual(entry.previous_version, version - 1 if version > 1 else None)
            self.assertEqual(entry.new_version, version)
            self.assertEqual((entry.previous_sale_unit, entry.previous_net_content_amount, entry.previous_net_content_unit), previous)
            previous = (entry.new_sale_unit, entry.new_net_content_amount, entry.new_net_content_unit)
            self.assertEqual(entry.action, "catalog.variant_measurement_spec_changed")
            self.assertEqual(entry.result, "success")
        spec = VariantMeasurementSpec.objects.get(variant=self.variant)
        self.assertEqual(spec.version, len(entries))
        self.assertEqual((spec.sale_unit, spec.net_content_amount, spec.net_content_unit), previous)
        self.assertTrue(all(entry.spec_id == spec.pk for entry in entries))

    def test_committed_create_update_and_clear_preserve_exact_before_after_attribution(self):
        self.assertTrue(connection.get_autocommit())
        self.write(amount=Decimal("250"), unit=MeasurementUnit.G)
        self.write(expected=1, sale=MeasurementUnit.KG)
        cleared = self.write(expected=2, sale=None)
        self.assertEqual(self.read(), cleared)
        self.assertEqual(cleared.version, 3)
        self.assert_audit_chain()

    def test_audit_failure_before_and_after_insert_rolls_back_creation_and_clearing(self):
        original = audit_application.record_variant_measurement_spec_changed
        for replacement in (False, True):
            if replacement:
                self.write(amount=Decimal("250"), unit=MeasurementUnit.G)
            before = list(VariantMeasurementSpec.objects.values())
            before_audit = list(CatalogMeasurementAuditEntry.objects.values())
            for after_insert in (False, True):
                with self.subTest(replacement=replacement, after_insert=after_insert):
                    def fail(**kwargs):
                        if after_insert:
                            identifier = original(**kwargs)
                            self.assertTrue(CatalogMeasurementAuditEntry.objects.filter(pk=identifier).exists())
                        raise DatabaseError("fictional audit failure")

                    with patch.object(audit_application, "record_variant_measurement_spec_changed", side_effect=fail):
                        with self.assertRaises(DatabaseError):
                            self.write(expected=1 if replacement else None, sale=None if replacement else MeasurementUnit.EACH)
                    self.assertEqual(list(VariantMeasurementSpec.objects.values()), before)
                    self.assertEqual(list(CatalogMeasurementAuditEntry.objects.values()), before_audit)
        self.assert_audit_chain()

    def test_outer_rollback_reverts_returned_snapshot_and_audit_for_creation_and_update(self):
        with self.assertRaisesRegex(RuntimeError, "rollback requested"):
            with transaction.atomic():
                result = self.write()
                self.assertEqual(result.version, 1)
                self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), 1)
                raise RuntimeError("rollback requested")
        self.assertFalse(VariantMeasurementSpec.objects.exists())
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())
        first = self.write()
        with self.assertRaisesRegex(RuntimeError, "rollback requested"):
            with transaction.atomic():
                self.write(expected=1, sale=None)
                raise RuntimeError("rollback requested")
        self.assertEqual(self.read(), first)
        self.assert_audit_chain()

    def _run_serialized(self, first_call, second_call, audit_name, inspect_pending):
        first_locked, second_requested, release = Event(), Event(), Event()
        outcomes = Queue()
        original = getattr(audit_application, audit_name)

        def pause(**kwargs):
            first_locked.set()
            if not release.wait(15):
                raise RuntimeError("test synchronization timeout")
            return original(**kwargs)

        def worker(first):
            close_old_connections()
            db = connections["default"]
            try:
                if db.settings_dict["NAME"] != "test_mira":
                    raise RuntimeError("unexpected test database")
                with db.cursor() as cursor:
                    cursor.execute("SELECT current_database()")
                    if cursor.fetchone() != ("test_mira",):
                        raise RuntimeError("unexpected live database")

                def observe(execute, sql, params, many, context):
                    if not first and "FOR UPDATE" in sql:
                        second_requested.set()
                    return execute(sql, params, many, context)

                with db.execute_wrapper(observe):
                    outcomes.put((first, first_call() if first else second_call()))
            except Exception as error:
                outcomes.put((first, error))
            finally:
                db.close()

        first = Thread(target=worker, args=(True,), daemon=True)
        second = Thread(target=worker, args=(False,), daemon=True)
        with patch.object(audit_application, audit_name, side_effect=pause):
            first.start()
            try:
                self.assertTrue(first_locked.wait(15), "first writer did not reach audit")
                second.start()
                self.assertTrue(second_requested.wait(15), "second writer did not request the Variant lock")
                inspect_pending()
                with self.assertRaises(DatabaseError) as failure, transaction.atomic():
                    Variant.objects.select_for_update(nowait=True).get(pk=self.variant.pk)
                self.assertEqual(failure.exception.__cause__.sqlstate, "55P03")
            finally:
                release.set()
                first.join(15)
                if second.ident is not None:
                    second.join(15)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(outcomes.qsize(), 2)
        return dict(outcomes.get_nowait() for unused in range(2))

    def _concurrent_measurements(self, replacement):
        previous = self.write() if replacement else None
        count = 1 if replacement else 0

        def write():
            return self.write(expected=count or None, sale=MeasurementUnit.KG)

        def pending():
            self.assertEqual(self.read(), previous)
            self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), count)

        results = self._run_serialized(write, write, "record_variant_measurement_spec_changed", pending)
        self.assertIsInstance(results[True], VariantMeasurementSpecSnapshot)
        self.assertIsInstance(results[False], MeasurementSpecConflictError)
        self.assertEqual(results[True].version, count + 1)
        self.assertEqual(self.read(), results[True])
        self.assert_audit_chain()

    def test_concurrent_initial_assignments_serialize_and_identical_retry_conflicts(self):
        self._concurrent_measurements(replacement=False)

    def test_concurrent_updates_serialize_and_identical_stale_state_still_conflicts(self):
        self._concurrent_measurements(replacement=True)

    def _price_and_measurement(self, price_first):
        def price():
            return set_variant_price(principal=self.principal, variant_id=self.variant.id,
                                     amount=Decimal("12.34"), currency="ARS", expected_revision_id=None)

        def measurement():
            return self.write(sale=MeasurementUnit.KG)

        def pending():
            self.assertIsNone(self.read())
            self.assertFalse(VariantPriceRevision.objects.exists())
            self.assertFalse(CatalogPriceAuditEntry.objects.exists())
            self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

        results = self._run_serialized(
            price if price_first else measurement, measurement if price_first else price,
            "record_variant_price_set" if price_first else "record_variant_measurement_spec_changed", pending,
        )
        self.assertIsInstance(results[price_first], PriceSnapshot)
        self.assertEqual(results[price_first].price_basis, PriceBasis.PER_VARIANT)
        self.assertIsInstance(results[not price_first], VariantMeasurementSpecSnapshot)
        self.assertEqual(CatalogPriceAuditEntry.objects.count(), 1)
        self.assertEqual(VariantPriceRevision.objects.count(), 1)
        self.assert_audit_chain()

    def test_measurement_writer_and_price_writer_share_variant_lock_without_reinterpreting_price(self):
        self._price_and_measurement(price_first=False)

    def test_price_writer_and_measurement_writer_also_serialize_in_reverse_order(self):
        self._price_and_measurement(price_first=True)
