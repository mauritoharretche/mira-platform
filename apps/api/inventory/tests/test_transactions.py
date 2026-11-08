from queue import Queue
from threading import Event, Thread, current_thread
from time import monotonic, sleep
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from audit import application as audit_application
from audit.models import InventoryMovementAuditEntry
from inventory.application import adjust_stock, get_stock, receive_stock
from inventory.contracts import InventoryUnit, StockConflictError, StockSnapshot
from inventory.models import StockMovement, StockPosition
from inventory.tests.test_application import assert_chain, assert_test_database, make_principal, make_variant


class InventoryTransactionTests(TransactionTestCase):
    def setUp(self):
        assert_test_database(self)
        self.user, self.principal = make_principal()
        self.variant = make_variant()

    def receive(self, expected=None):
        return receive_stock(principal=self.principal, variant_id=self.variant.pk,
                             unit=InventoryUnit.EACH, quantity=10, expected_version=expected)

    def adjust(self, target=7, expected=1):
        return adjust_stock(principal=self.principal, variant_id=self.variant.pk,
                            new_on_hand=target, reason="fictional count", expected_version=expected)

    def state(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in
                     (StockPosition, StockMovement, InventoryMovementAuditEntry))

    def test_audit_failure_before_and_after_insert_rolls_back_initial_receipt_and_later_mutations(self):
        original = audit_application.record_inventory_movement
        for phase in ("initial", "receipt", "adjustment"):
            if phase == "receipt":
                self.receive()
            baseline = self.state()
            for after_insert in (False, True):
                with self.subTest(phase=phase, after_insert=after_insert):
                    def fail(**kwargs):
                        if after_insert:
                            identifier = original(**kwargs)
                            self.assertTrue(InventoryMovementAuditEntry.objects.filter(pk=identifier).exists())
                        raise DatabaseError("fictional attribution failure")
                    with patch.object(audit_application, "record_inventory_movement", side_effect=fail):
                        with self.assertRaises(DatabaseError):
                            self.adjust() if phase == "adjustment" else self.receive(None if phase == "initial" else 1)
                    self.assertEqual(self.state(), baseline)

    def test_outer_rollback_reverts_returned_initial_and_adjustment_snapshots(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.assertEqual(self.receive().on_hand, 10)
                raise RuntimeError("fictional requested rollback")
        self.assertEqual(self.state(), ([], [], []))
        self.receive()
        baseline = self.state()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.adjust()
                raise RuntimeError("fictional requested rollback")
        self.assertEqual(self.state(), baseline)
        assert_chain(self, self.variant, self.user.pk)

    def _concurrent(self, first_call, second_call, *, initial=False, fail_first=False):
        locked, second_requested, release = Event(), Event(), Event()
        outcomes = Queue()
        pids, inserts = {}, {}
        original = audit_application.record_inventory_movement

        def pause(**kwargs):
            if current_thread().name == "fictional-stock-first":
                locked.set()
                if not release.wait(15):
                    raise RuntimeError("inventory test synchronization timeout")
                if fail_first:
                    raise DatabaseError("fictional first-writer rollback")
            return original(**kwargs)

        def worker(first, call):
            close_old_connections()
            db = connections["default"]
            count = 0
            try:
                if db.settings_dict["NAME"] != "test_mira":
                    raise RuntimeError("unsafe test database")
                with db.cursor() as cursor:
                    cursor.execute("SELECT current_database(), pg_backend_pid()")
                    name, pids[first] = cursor.fetchone()
                    if name != "test_mira":
                        raise RuntimeError("unsafe live database")

                def observe(execute, sql, params, many, context):
                    nonlocal count
                    if 'INSERT INTO "inventory_stockposition"' in sql:
                        count += 1
                    if not first and ((initial and 'INSERT INTO "inventory_stockposition"' in sql)
                                      or (not initial and "FOR UPDATE" in sql)):
                        second_requested.set()
                    return execute(sql, params, many, context)

                with db.execute_wrapper(observe):
                    result = call()
            except Exception as error:
                result = error
            finally:
                inserts[first] = count
                db.close()
            outcomes.put((first, result))

        first = Thread(target=worker, args=(True, first_call), name="fictional-stock-first", daemon=True)
        second = Thread(target=worker, args=(False, second_call), name="fictional-stock-second", daemon=True)
        with patch.object(audit_application, "record_inventory_movement", side_effect=pause):
            first.start()
            try:
                self.assertTrue(locked.wait(15))
                second.start()
                self.assertTrue(second_requested.wait(15))
                deadline = monotonic() + 5
                blocked = []
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pids[False]])
                        blocked = cursor.fetchone()[0]
                    if pids[True] in blocked:
                        break
                    sleep(0.01)
                self.assertIn(pids[True], blocked, "second writer was not blocked by the first transaction")
                current = get_stock(principal=self.principal, variant_id=self.variant.pk)
                self.assertEqual(current.on_hand if current else None, None if initial else 10)
                self.assertEqual(StockMovement.objects.filter(position__variant_id=self.variant.pk).count(), 0 if initial else 1)
                self.assertEqual(InventoryMovementAuditEntry.objects.filter(variant_id=self.variant.pk).count(), 0 if initial else 1)
            finally:
                release.set()
                first.join(15)
                if second.ident is not None:
                    second.join(15)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(outcomes.qsize(), 2)
        self.assertEqual(inserts, {True: 1, False: 1} if initial else {True: 0, False: 0})
        return dict(outcomes.get_nowait() for unused in range(2))

    def test_concurrent_initial_receipts_have_one_winner_and_no_business_retries(self):
        results = self._concurrent(self.receive, self.receive, initial=True)
        self.assertIsInstance(results[True], StockSnapshot)
        self.assertIsInstance(results[False], StockConflictError)
        self.assertEqual(StockPosition.objects.count(), 1)
        assert_chain(self, self.variant, self.user.pk)

    def test_concurrent_receipts_serialize_and_reject_stale_increment(self):
        self.receive()
        results = self._concurrent(lambda: self.receive(1), lambda: self.receive(1))
        self.assertIsInstance(results[True], StockSnapshot)
        self.assertIsInstance(results[False], StockConflictError)
        self.assertEqual(StockPosition.objects.get().on_hand, 20)
        assert_chain(self, self.variant, self.user.pk)

    def test_concurrent_equal_adjustments_conflict_before_noop(self):
        self.receive()
        results = self._concurrent(self.adjust, self.adjust)
        self.assertIsInstance(results[True], StockSnapshot)
        self.assertIsInstance(results[False], StockConflictError)
        assert_chain(self, self.variant, self.user.pk)

    def test_receipt_and_adjustment_are_serialized_in_both_orders(self):
        for adjust_first in (False, True):
            # Separate Variants keep each concurrency scenario at version 1.
            self.variant = make_variant()
            self.receive()
            results = self._concurrent(self.adjust if adjust_first else lambda: self.receive(1),
                                       (lambda: self.receive(1)) if adjust_first else self.adjust)
            self.assertIsInstance(results[True], StockSnapshot)
            self.assertIsInstance(results[False], StockConflictError)
            assert_chain(self, self.variant, self.user.pk)

    def test_competing_first_receipt_can_commit_when_first_transaction_rolls_back(self):
        results = self._concurrent(self.receive, self.receive, initial=True, fail_first=True)
        self.assertIsInstance(results[True], DatabaseError)
        self.assertIsInstance(results[False], StockSnapshot)
        self.assertEqual(StockPosition.objects.count(), 1)
        assert_chain(self, self.variant, self.user.pk)
