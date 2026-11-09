from datetime import timedelta
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Barrier, Thread
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.conf import settings

from inventory.tests.test_application import assert_test_database
from identity.login_throttle import LoginThrottled, reserve_login_attempt
from identity.models import LoginAttemptWindow


class LoginThrottleTests(TestCase):
    def setUp(self):
        assert_test_database(self)

    def test_identifier_limit_hmac_normalization_and_expiration(self):
        now = timezone.now()
        with patch("identity.login_throttle.timezone.now", return_value=now):
            for _ in range(5):
                reserve_login_attempt("Ｆictional")
            with self.assertRaises(LoginThrottled) as caught:
                reserve_login_attempt("Fictional")
        self.assertGreaterEqual(caught.exception.retry_after, 900)
        self.assertEqual(LoginAttemptWindow.objects.count(), 2)
        self.assertTrue(all(len(key) == 64 and "fictional" not in key.lower() for key in LoginAttemptWindow.objects.values_list("key", flat=True)))
        with patch("identity.login_throttle.timezone.now", return_value=now + timedelta(minutes=16)):
            reserve_login_attempt("Fictional")
        self.assertEqual(list(LoginAttemptWindow.objects.values_list("attempts", flat=True)), [1, 1])

    def test_global_limit_is_shared_and_does_not_create_unbounded_rows_when_full(self):
        for index in range(30):
            reserve_login_attempt(f"fictional-{index}")
        before = LoginAttemptWindow.objects.count()
        with self.assertRaises(LoginThrottled):
            reserve_login_attempt("fictional-another")
        self.assertEqual(LoginAttemptWindow.objects.count(), before)

    def test_only_long_expired_budgets_are_cleaned_in_a_bounded_batch(self):
        now = timezone.now()
        for index in range(105):
            LoginAttemptWindow.objects.create(key=f"{index:064x}", attempts=1, expires_at=now - timedelta(days=2))
        reserve_login_attempt("fictional-cleanup")
        self.assertEqual(LoginAttemptWindow.objects.count(), 7)

    def test_database_rejects_invalid_attempt_counts_without_model_validation(self):
        for count in (0, -1, 31):
            with self.subTest(count=count), self.assertRaises(IntegrityError), transaction.atomic():
                LoginAttemptWindow.objects.create(key="a" * 64, attempts=count, expires_at=timezone.now())
        self.assertFalse(LoginAttemptWindow.objects.exists())


class ConcurrentLoginThrottleTests(TransactionTestCase):
    def setUp(self):
        assert_test_database(self)

    def test_concurrent_initial_reservations_allow_exactly_five_attempts(self):
        barrier = Barrier(8)
        results = Queue()
        def worker():
            close_old_connections()
            db = connections["default"]
            try:
                if db.settings_dict["NAME"] != "test_mira":
                    raise AssertionError("unsafe database")
                with db.cursor() as cursor:
                    cursor.execute("SELECT current_database()")
                    if cursor.fetchone() != ("test_mira",):
                        raise AssertionError("unsafe effective database")
                barrier.wait(timeout=15)
                reserve_login_attempt("fictional-concurrent")
                results.put("allowed")
            except LoginThrottled:
                results.put("throttled")
            except Exception as exc:
                results.put(type(exc).__name__)
            finally:
                db.close()
        workers = [Thread(target=worker, daemon=True) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=20)
            self.assertFalse(worker.is_alive())
        observed = [results.get_nowait() for _ in workers]
        self.assertEqual(observed.count("allowed"), 5, observed)
        self.assertEqual(observed.count("throttled"), 3, observed)
        self.assertCountEqual(LoginAttemptWindow.objects.values_list("attempts", flat=True), [8, 5])

    def test_separate_processes_share_the_same_postgresql_budget(self):
        database = connections["default"].settings_dict
        self.assertEqual(database["NAME"], "test_mira")
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
        env.update(DJANGO_SETTINGS_MODULE="config.settings.test", DJANGO_SECRET_KEY="test-only-process-fixture",
                   POSTGRES_DB="test_mira", POSTGRES_USER=database["USER"], POSTGRES_PASSWORD=database["PASSWORD"],
                   POSTGRES_HOST=database["HOST"], POSTGRES_PORT=str(database["PORT"]))
        worker = '''
import sys, django
django.setup()
from django.db import connection
from identity.login_throttle import LoginThrottled, reserve_login_attempt
if connection.settings_dict["NAME"] != "test_mira":
    sys.exit(2)
with connection.cursor() as cursor:
    cursor.execute("SELECT current_database()")
    if cursor.fetchone() != ("test_mira",):
        sys.exit(2)
sys.stdin.readline()
try:
    reserve_login_attempt("fictional-multiprocess-operator")
    print("allowed")
except LoginThrottled:
    print("throttled")
finally:
    connection.close()
'''
        # Children only consume the already-created test DB. No child creates,
        # migrates, resets, or connects to any non-test database.
        workers = []
        try:
            workers = [subprocess.Popen([sys.executable, "-B", "-c", worker], cwd=Path(settings.BASE_DIR), env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                       for _ in range(8)]
            for process in workers:
                process.stdin.write("go\n")
                process.stdin.flush()
            observed = []
            for process in workers:
                stdout, _ = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, "Isolated test worker failed; credentials are not logged")
                observed.append(stdout.strip())
            self.assertEqual(observed.count("allowed"), 5, observed)
            self.assertEqual(observed.count("throttled"), 3, observed)
            self.assertCountEqual(LoginAttemptWindow.objects.values_list("attempts", flat=True), [8, 5])
        finally:
            for process in workers:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
