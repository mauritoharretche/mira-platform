from decimal import Decimal
from queue import Queue
from threading import Event, Thread
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from audit import application as audit_application
from audit.models import CatalogPriceAuditEntry
from catalog.application import PriceConflictError, get_current_variant_price, set_variant_price
from catalog.contracts import PriceSnapshot
from catalog.models import Product, Variant, VariantPriceRevision


class PriceTransactionTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        password = "fictional-test-password-only"
        self.user = get_user_model().objects.create_user(username="fictional-price-writer", password=password)
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog"))
        self.principal = authenticate(username=self.user.username, password=password)
        self.assertIsNotNone(self.principal)
        product = Product.objects.create(name="Fictional product")
        self.variant = Variant.objects.create(product=product, label="Presentation", sku="FICT-001")

    def write(self, amount=Decimal("10"), expected=None):
        return set_variant_price(principal=self.principal, variant_id=self.variant.id,
                                 amount=amount, currency="ARS", expected_revision_id=expected)

    def assert_consistent_audit(self):
        revisions = list(VariantPriceRevision.objects.order_by("sequence"))
        self.assertEqual(CatalogPriceAuditEntry.objects.count(), len(revisions))
        previous = None
        for revision in revisions:
            entry = CatalogPriceAuditEntry.objects.get(new_revision_id=revision.pk)
            self.assertEqual(entry.actor_id, str(self.user.pk))
            self.assertEqual(entry.variant_id, revision.variant_id)
            self.assertEqual(entry.previous_revision_id, previous.pk if previous else None)
            self.assertEqual(entry.previous_amount, previous.amount if previous else None)
            self.assertEqual(entry.new_amount, revision.amount)
            self.assertEqual(entry.currency, revision.currency)
            self.assertEqual(entry.price_basis, revision.price_basis)
            self.assertEqual(entry.action, "catalog.variant_price_set")
            self.assertEqual(entry.result, "success")
            previous = revision

    def test_committed_success_preserves_revision_and_audit_correspondence(self):
        self.assertTrue(connection.get_autocommit())
        first = self.write()
        second = self.write(Decimal("20"), expected=first.revision_id)
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), second)
        self.assert_consistent_audit()

    def test_audit_failure_before_or_after_insert_rolls_back_initial_and_replacement_writes(self):
        original = audit_application.record_variant_price_set
        for replacement in (False, True):
            expected = self.write().revision_id if replacement else None
            before = list(VariantPriceRevision.objects.values())
            before_audit = list(CatalogPriceAuditEntry.objects.values())
            for after_insert in (False, True):
                with self.subTest(replacement=replacement, after_insert=after_insert):
                    def fail(**kwargs):
                        if after_insert:
                            entry_id = original(**kwargs)
                            self.assertTrue(CatalogPriceAuditEntry.objects.filter(pk=entry_id).exists())
                        raise DatabaseError("fictional audit failure")

                    with patch.object(audit_application, "record_variant_price_set", side_effect=fail):
                        with self.assertRaises(DatabaseError):
                            self.write(Decimal("20"), expected=expected)
                    self.assertEqual(list(VariantPriceRevision.objects.values()), before)
                    self.assertEqual(list(CatalogPriceAuditEntry.objects.values()), before_audit)
        self.assert_consistent_audit()

    def test_outer_rollback_discards_returned_revisions_and_audit(self):
        with self.assertRaisesRegex(RuntimeError, "rollback requested"):
            with transaction.atomic():
                first = self.write()
                self.write(Decimal("20"), expected=first.revision_id)
                self.assertEqual(VariantPriceRevision.objects.count(), 2)
                self.assertEqual(CatalogPriceAuditEntry.objects.count(), 2)
                raise RuntimeError("rollback requested")
        self.assertFalse(VariantPriceRevision.objects.exists())
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())
        first = self.write()
        with self.assertRaisesRegex(RuntimeError, "rollback requested"):
            with transaction.atomic():
                self.write(Decimal("20"), expected=first.revision_id)
                raise RuntimeError("rollback requested")
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), first)
        self.assert_consistent_audit()

    def test_conflict_requires_explicit_fresh_expectation_for_a_later_change(self):
        first = self.write()
        second = self.write(Decimal("20"), expected=first.revision_id)
        with self.assertRaises(PriceConflictError):
            self.write(Decimal("30"), expected=first.revision_id)
        self.assertEqual(VariantPriceRevision.objects.count(), 2)
        current = get_current_variant_price(principal=self.principal, variant_id=self.variant.id)
        self.assertEqual(current, second)
        third = self.write(Decimal("30"), expected=current.revision_id)
        self.assertEqual(third.sequence, 3)
        self.assert_consistent_audit()

    def _exercise_concurrent_writes(self, replacement):
        previous = self.write() if replacement else None
        expected = previous.revision_id if previous else None
        count = 1 if replacement else 0
        first_locked, second_lock_requested, release_first = Event(), Event(), Event()
        outcomes = Queue()
        original = audit_application.record_variant_price_set

        def pause_writer(**kwargs):
            first_locked.set()
            if not release_first.wait(10):
                raise RuntimeError("test synchronization timeout")
            return original(**kwargs)

        def worker(is_first):
            close_old_connections()
            db = connections["default"]
            try:
                if db.settings_dict["NAME"] != "test_mira":
                    raise RuntimeError("unexpected test database")

                def observe_lock(execute, sql, params, many, context):
                    if not is_first and "FOR UPDATE" in sql:
                        second_lock_requested.set()
                    return execute(sql, params, many, context)

                with db.execute_wrapper(observe_lock):
                    result = self.write(Decimal("20"), expected=expected)
                outcomes.put((is_first, result))
            except Exception as error:
                outcomes.put((is_first, error))
            finally:
                db.close()

        first = Thread(target=worker, args=(True,), daemon=True)
        second = Thread(target=worker, args=(False,), daemon=True)
        with patch.object(audit_application, "record_variant_price_set", side_effect=pause_writer):
            first.start()
            try:
                self.assertTrue(first_locked.wait(10), "first writer did not reach audit")
                second.start()
                self.assertTrue(second_lock_requested.wait(10), "second writer did not request its lock")
                # Readers see the last committed revision, never the pending new one.
                self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), previous)
                self.assertEqual(VariantPriceRevision.objects.count(), count)
                self.assertEqual(CatalogPriceAuditEntry.objects.count(), count)
                with self.assertRaises(DatabaseError) as failure, transaction.atomic():
                    Variant.objects.select_for_update(nowait=True).get(pk=self.variant.pk)
                self.assertEqual(failure.exception.__cause__.sqlstate, "55P03")
            finally:
                release_first.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(outcomes.qsize(), 2)
        results = dict(outcomes.get_nowait() for unused in range(2))
        self.assertIsInstance(results[True], PriceSnapshot)
        # Same submitted amount must still conflict after the first commit.
        self.assertIsInstance(results[False], PriceConflictError)
        self.assertEqual(VariantPriceRevision.objects.count(), count + 1)
        self.assertEqual(results[True].sequence, count + 1)
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), results[True])
        self.assert_consistent_audit()

    def test_concurrent_first_assignments_serialize_on_the_variant(self):
        self._exercise_concurrent_writes(replacement=False)

    def test_concurrent_replacements_do_not_overwrite_with_stale_expectations(self):
        self._exercise_concurrent_writes(replacement=True)
