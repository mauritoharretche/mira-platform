"""Browser lifecycle belongs exclusively to the native isolated Django runner."""
import json
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import shutil
import socket
import subprocess
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from django.utils.crypto import salted_hmac

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry, CatalogMeasurementAuditEntry, CatalogPriceAuditEntry, InventoryMovementAuditEntry
from catalog.models import Category, Product, Variant, VariantPriceRevision
from identity.models import LoginAttemptWindow
from identity.tests.test_http_authentication import PERMISSIONS
from inventory.models import StockMovement, StockPosition
from procurement.models import GoodsReceipt, ProcurementEvent, PurchaseOrder, ReceiptLine, Supplier
from audit.models import ProcurementAuditEntry, InventoryLotAuditEntry
from inventory.models import StockLocation, StockLot, StockLotBalance, StockLotEvent
from procurement.models import ReceiptAllocation
from inventory.models import StockReservation, ReservationAllocation, ReservationEvent
from audit.models import ReservationAuditEntry


_REPORT_MAX_BYTES = 4 * 1024 * 1024
_DIAGNOSTIC_MAX_BYTES = 512
_DIAGNOSTIC_MAX_CASES = 4
_REPORT_MAX_SPECS = 64
_REPORT_MAX_TESTS = 64
_REPORT_MAX_RESULTS = 2
_REPORT_MAX_ERRORS = 4
_LOCATION_MAX_PATH = 1024
_LOCATION_MAX_COLUMN = 10000
_BROWSER_CASES = {
    # Playwright 1.65 JSON spec.file is relative to config.rootDir (testDir).
    "catalog.spec.ts": frozenset({
        "catalog-journey", "catalog-readonly", "catalog-revoked", "catalog-stale",
        "catalog-lost-category", "catalog-lost-change", "catalog-variants", "catalog-variants-lost",
        "catalog-price", "catalog-price-lost",
    }),
    "operations.spec.ts": frozenset({
        "journey", "csrf", "denied", "revoked", "duplicate", "lost-creation",
        "lost-receipt", "expired",
    }),
    "procurement.spec.ts": frozenset({"procurement-journey"}),
    "measured.spec.ts": frozenset({"measured-journey"}),
    "pos.spec.ts": frozenset({"pos-journey", "pos-lost"}),
}
_FAILED_RESULT_STATES = frozenset({"failed", "timedOut", "interrupted"})
_TEST_OUTCOMES = frozenset({"expected", "unexpected", "flaky", "skipped"})
_GLOBAL_LOGIN_LIMIT = 30
_GLOBAL_LOGIN_WINDOW = timedelta(seconds=60)
_LOGIN_RESERVATIONS_PER_PROJECT = 22


def _login_capacity(window, now):
    """Interpret only the real global budget; never create or reset its row."""
    if (not isinstance(window, tuple) or len(window) != 2
            or type(window[0]) is not int or not 1 <= window[0] <= _GLOBAL_LOGIN_LIMIT
            or not isinstance(window[1], datetime) or not timezone.is_aware(window[1])
            or not isinstance(now, datetime) or not timezone.is_aware(now)):
        raise AssertionError("Invalid global login budget")
    attempts, expires_at = window
    remaining = (expires_at - now).total_seconds()
    if remaining > _GLOBAL_LOGIN_WINDOW.total_seconds():
        raise AssertionError("Contradictory global login expiry")
    return (_GLOBAL_LOGIN_LIMIT if remaining <= 0 else _GLOBAL_LOGIN_LIMIT - attempts,
            max(0.0, remaining))


def _wait_for_login_capacity(read_window, *, required, deadline,
                             now=timezone.now, monotonic=time.monotonic, wait=time.sleep):
    if type(required) is not int or not 1 <= required <= _GLOBAL_LOGIN_LIMIT:
        raise AssertionError("Invalid browser login reservation count")
    started = monotonic()
    barrier_deadline = min(deadline, started + _GLOBAL_LOGIN_WINDOW.total_seconds() + 5)
    original = read_window()
    before, remaining = _login_capacity(original, now())
    if before >= required:
        return {"before_available": before, "after_available": before, "waited_seconds": 0.0}
    while True:
        if monotonic() + remaining > barrier_deadline:
            raise AssertionError("Global login budget deadline exceeded")
        wait(remaining)  # The stored expiry, never an arbitrary delay or retry.
        current = read_window()
        if current != original:
            raise AssertionError("Global login budget changed during barrier")
        after, remaining = _login_capacity(current, now())
        if after >= required:
            return {"before_available": before, "after_available": after,
                    "waited_seconds": monotonic() - started}
        if monotonic() >= barrier_deadline:
            raise AssertionError("Global login budget deadline exceeded")


def _read_global_login_window(test):
    assert_browser_database(test)
    key = salted_hmac("mira.operator.login.global", "global", algorithm="sha256").hexdigest()
    try:
        window = LoginAttemptWindow.objects.only("attempts", "expires_at").get(pk=key)
    except LoginAttemptWindow.DoesNotExist:
        return None
    return window.attempts, window.expires_at


def _stop_owned_process(process):
    if process is None:
        return
    import signal
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _run_project_sequence(run_project, between_projects):
    run_project("desktop")
    between_projects()
    run_project("mobile")


def _validated_failure_line(result: dict, source_path: Path, source_line_lengths: list[int]) -> int | None:
    """Use only Playwright 1.65's structured error locations in the known E2E file."""
    errors = result.get("errors", [])
    if not isinstance(errors, list) or len(errors) > _REPORT_MAX_ERRORS:
        raise ValueError("Invalid browser error collection")
    candidates = []
    for error in errors:
        if not isinstance(error, dict):
            raise ValueError("Invalid browser error")
        if "location" in error:
            candidates.append(error["location"])
    if "errorLocation" in result:
        candidates.append(result["errorLocation"])
    if not candidates:
        return None
    locations = []
    for location in candidates:
        if not isinstance(location, dict) or set(location) != {"file", "line", "column"}:
            raise ValueError("Invalid browser error location")
        file, line, column = location["file"], location["line"], location["column"]
        if (not isinstance(file, str) or len(file) > _LOCATION_MAX_PATH
                or file != str(source_path) or type(line) is not int
                or not 1 <= line <= len(source_line_lengths) or type(column) is not int
                or not 0 <= column <= min(_LOCATION_MAX_COLUMN, source_line_lengths[line - 1] + 1)):
            raise ValueError("Untrusted browser error location")
        locations.append((file, line, column))
    if len(set(locations)) != 1:
        raise ValueError("Contradictory browser error locations")
    return locations[0][1]


def _safe_browser_failure_summary(report_path: Path) -> str:
    """Return only reviewed test identifiers, never Playwright's raw error data."""
    try:
        with report_path.open("rb") as report_file:
            content = report_file.read(_REPORT_MAX_BYTES + 1)
        if len(content) > _REPORT_MAX_BYTES:
            return "diagnostic_unavailable"
        report = json.loads(content)
        if not isinstance(report, dict) or not isinstance(report.get("suites"), list):
            return "diagnostic_unavailable"
        pending = list(report["suites"])
        inspected = failed_count = spec_count = test_count = 0
        cases = []
        while pending:
            suite = pending.pop()
            inspected += 1
            if inspected > 128 or not isinstance(suite, dict):
                return "diagnostic_unavailable"
            nested = suite.get("suites", [])
            specs = suite.get("specs", [])
            if (not isinstance(nested, list) or not isinstance(specs, list)
                    or len(nested) + len(pending) > 128
                    or len(specs) > _REPORT_MAX_SPECS):
                return "diagnostic_unavailable"
            spec_count += len(specs)
            if spec_count > _REPORT_MAX_SPECS:
                return "diagnostic_unavailable"
            pending.extend(nested)
            for spec in specs:
                if not isinstance(spec, dict) or not isinstance(spec.get("tests"), list):
                    return "diagnostic_unavailable"
                test_count += len(spec["tests"])
                if test_count > _REPORT_MAX_TESTS:
                    return "diagnostic_unavailable"
                for test in spec["tests"]:
                    if not isinstance(test, dict) or test.get("status") not in _TEST_OUTCOMES:
                        return "diagnostic_unavailable"
                    results = test.get("results")
                    if not isinstance(results, list) or len(results) > _REPORT_MAX_RESULTS:
                        return "diagnostic_unavailable"
                    if test["status"] != "unexpected":
                        continue
                    file = spec.get("file")
                    title = spec.get("title")
                    line = spec.get("line")
                    project = test.get("projectName")
                    if (not isinstance(file, str) or file not in _BROWSER_CASES
                            or not isinstance(title, str) or title not in _BROWSER_CASES[file]
                            or type(line) is not int or not 1 <= line <= 10000
                            or project not in ("desktop", "mobile")
                            or len(results) != 1 or not isinstance(results[0], dict)):
                        return "diagnostic_unavailable"
                    result = results[0]
                    state = result.get("status")
                    if state not in _FAILED_RESULT_STATES:
                        return "diagnostic_unavailable"
                    source_path = (Path(__file__).resolve().parents[4] / "apps" / "web" / "e2e" / file).resolve(strict=True)
                    with source_path.open(encoding="utf-8") as source:
                        source_line_lengths = [len(text.rstrip("\r\n").encode("utf-16-le")) // 2
                                               for text in source]
                    if line > len(source_line_lengths):
                        return "diagnostic_unavailable"
                    failure_line = _validated_failure_line(result, source_path, source_line_lengths)
                    failed_count += 1
                    if len(cases) < _DIAGNOSTIC_MAX_CASES:
                        case = {"project": project, "file": file, "case": title,
                                "line": line, "status": state}
                        if failure_line is not None:
                            case["failure_line"] = failure_line
                        cases.append(case)
        if not failed_count:
            return "diagnostic_unavailable"
        summary = json.dumps({"failed_count": failed_count, "cases": cases,
                              "truncated": failed_count > len(cases)}, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True)
        return summary if len(summary.encode("ascii")) <= _DIAGNOSTIC_MAX_BYTES else "diagnostic_unavailable"
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return "diagnostic_unavailable"


def _assert_browser_exit(test: unittest.TestCase, browser, report_path: Path, artifacts: Path):
    try:
        exit_code = browser.wait(timeout=5)
        if exit_code != 0:
            print("MIRA_BROWSER_FAILURE=" + _safe_browser_failure_summary(report_path), flush=True)
        test.assertEqual(exit_code, 0, f"Browser failure; inspect {artifacts}/browser.log")
    finally:
        report_path.unlink(missing_ok=True)


class BrowserLoginBudgetTests(unittest.TestCase):
    """Pure orchestration tests: no database, browser, or real waiting."""

    def setUp(self):
        self.start = timezone.now()
        self.elapsed = 0.0
        self.waits = []

    def now(self):
        return self.start + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed

    def wait(self, seconds):
        self.waits.append(seconds)
        self.elapsed += seconds

    def budget(self, window, *, deadline=420):
        return _wait_for_login_capacity(lambda: window, required=_LOGIN_RESERVATIONS_PER_PROJECT,
                                        deadline=deadline, now=self.now,
                                        monotonic=self.monotonic, wait=self.wait)

    def test_capacity_sufficient_does_not_wait(self):
        result = self.budget((_GLOBAL_LOGIN_LIMIT-_LOGIN_RESERVATIONS_PER_PROJECT, self.start + _GLOBAL_LOGIN_WINDOW))
        self.assertEqual(result, {"before_available": _LOGIN_RESERVATIONS_PER_PROJECT, "after_available": _LOGIN_RESERVATIONS_PER_PROJECT, "waited_seconds": 0.0})
        self.assertEqual(self.waits, [])

    def test_insufficient_capacity_waits_for_actual_expiry(self):
        expiry = self.start + timedelta(seconds=43)
        result = self.budget((16, expiry))
        self.assertEqual(result, {"before_available": 14, "after_available": 30, "waited_seconds": 43.0})
        self.assertEqual(self.waits, [43.0])

    def test_expired_window_does_not_wait(self):
        self.assertEqual(self.budget((30, self.start - timedelta(seconds=1)))["after_available"], 30)
        self.assertEqual(self.waits, [])

    def test_missing_invalid_or_contradictory_window_fails_closed(self):
        for window in (None, (0, self.start), (31, self.start), (True, self.start),
                       (16, self.start.replace(tzinfo=None)),
                       (16, self.start + timedelta(seconds=61))):
            with self.subTest(window=type(window).__name__), self.assertRaises(AssertionError):
                self.budget(window)
        self.assertEqual(self.waits, [])

    def test_global_deadline_exceeded_without_wait(self):
        with self.assertRaisesRegex(AssertionError, "deadline"):
            self.budget((16, self.start + timedelta(seconds=43)), deadline=40)
        self.assertEqual(self.waits, [])

    def test_interference_during_wait_fails_closed(self):
        first = (16, self.start + timedelta(seconds=43))
        read = Mock(side_effect=[first, (17, first[1])])
        with self.assertRaisesRegex(AssertionError, "changed"):
            _wait_for_login_capacity(read, required=16, deadline=420,
                                     now=self.now, monotonic=self.monotonic, wait=self.wait)
        self.assertEqual(self.waits, [43.0])

    def test_desktop_failure_prevents_mobile_and_mobile_failure_is_not_hidden(self):
        run = Mock(side_effect=AssertionError("desktop failed"))
        between = Mock()
        with self.assertRaisesRegex(AssertionError, "desktop failed"):
            _run_project_sequence(run, between)
        run.assert_called_once_with("desktop")
        between.assert_not_called()
        run = Mock(side_effect=[None, AssertionError("mobile failed")])
        with self.assertRaisesRegex(AssertionError, "mobile failed"):
            _run_project_sequence(run, between)
        self.assertEqual([call.args[0] for call in run.call_args_list], ["desktop", "mobile"])

    def test_owned_process_cleanup_on_timeout(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired("browser", 10), 0]
        with patch("os.killpg") as kill:
            _stop_owned_process(process)
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(process.wait.call_count, 2)


class BrowserFailureDiagnosticsTests(unittest.TestCase):
    """Pure tests: no database, browser, session or external process."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mira-browser-diagnostics-")
        self.addCleanup(self.temporary.cleanup)
        self.report_path = Path(self.temporary.name) / "playwright-report.json"

    def report(self, *, file="catalog.spec.ts", title="catalog-journey",
               project="desktop", line=171, state="timedOut", outcome="unexpected",
               secret="fictional-secret-must-not-appear"):
        payload = {"suites": [{"file": file, "specs": [{"file": file, "title": title,
                    "line": line, "tests": [{"projectName": project, "status": outcome,
                    "results": [{"status": state, "error": {"message": secret},
                                 "stdout": [secret], "stderr": [secret]}]}]}]}],
                   "errors": [secret]}
        self.report_path.write_text(json.dumps(payload))

    def location(self, *, file="catalog.spec.ts", line=200, column=4):
        source = (Path(__file__).resolve().parents[4] / "apps" / "web" / "e2e" / file).resolve()
        return {"file": str(source), "line": line, "column": column}

    def set_result(self, **changes):
        report = json.loads(self.report_path.read_text())
        report["suites"][0]["specs"][0]["tests"][0]["results"][0].update(changes)
        self.report_path.write_text(json.dumps(report))

    def test_known_failure_is_bounded_and_redacted(self):
        self.report()
        result = _safe_browser_failure_summary(self.report_path)
        self.assertLessEqual(len(result.encode("ascii")), _DIAGNOSTIC_MAX_BYTES)
        self.assertEqual(json.loads(result)["cases"], [{"project": "desktop",
            "file": "catalog.spec.ts", "case": "catalog-journey", "line": 171,
            "status": "timedOut"}])
        self.assertNotIn("fictional-secret-must-not-appear", result)

    def test_structured_error_location_reports_only_a_validated_line(self):
        self.report()
        self.set_result(errors=[{"location": self.location(),
                                 "message": "fictional-secret-must-not-appear",
                                 "stack": "fictional-secret-must-not-appear"}])
        result = _safe_browser_failure_summary(self.report_path)
        self.assertEqual(json.loads(result)["cases"][0]["failure_line"], 200)
        self.assertNotIn("fictional-secret-must-not-appear", result)
        self.assertLessEqual(len(result.encode("ascii")), _DIAGNOSTIC_MAX_BYTES)

    def test_result_error_location_alone_is_valid(self):
        self.report()
        self.set_result(errorLocation=self.location())
        self.assertEqual(json.loads(_safe_browser_failure_summary(self.report_path))
                         ["cases"][0]["failure_line"], 200)

    def test_free_text_and_unapproved_location_field_are_ignored(self):
        self.report()
        self.set_result(error={"location": self.location(),
                               "message": "fictional-secret-must-not-appear",
                               "stack": "fictional-secret-must-not-appear"})
        result = _safe_browser_failure_summary(self.report_path)
        self.assertNotIn("failure_line", json.loads(result)["cases"][0])
        self.assertNotIn("fictional-secret-must-not-appear", result)

    def test_matching_structured_locations_are_valid(self):
        self.report()
        location = self.location()
        self.set_result(errors=[{"location": location}], errorLocation=location)
        self.assertEqual(json.loads(_safe_browser_failure_summary(self.report_path))
                         ["cases"][0]["failure_line"], 200)

    def test_conflicting_structured_locations_are_unavailable(self):
        for other in (self.location(line=201), self.location(column=5)):
            self.report()
            self.set_result(errors=[{"location": self.location()}], errorLocation=other)
            self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")

    def test_untrusted_error_paths_are_unavailable(self):
        expected = self.location()["file"]
        for file in ("catalog.spec.ts", "/private/tmp/catalog.spec.ts",
                     expected.replace("/e2e/", "/e2e/../e2e/"),
                     expected.replace("/e2e/", "/e2e/./"),
                     "file://" + expected, expected + "fictional-secret-must-not-appear"):
            self.report()
            self.set_result(errorLocation=self.location() | {"file": file})
            result = _safe_browser_failure_summary(self.report_path)
            self.assertEqual(result, "diagnostic_unavailable")
            self.assertNotIn("fictional-secret-must-not-appear", result)

    def test_invalid_error_lines_and_columns_are_unavailable(self):
        source = Path(self.location()["file"])
        with source.open(encoding="utf-8") as content:
            lines = list(content)
        past_end = len(lines) + 1
        past_column = len(lines[199].rstrip("\r\n").encode("utf-16-le")) // 2 + 2
        for line in (True, -1, 0, past_end, "200"):
            self.report()
            self.set_result(errorLocation=self.location(line=line))
            self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")
        for column in (True, -1, 10001, past_column, "4"):
            self.report()
            self.set_result(errorLocation=self.location(column=column))
            self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")

    def test_malformed_error_locations_are_unavailable(self):
        for location in (None, "fictional-secret-must-not-appear",
                         {"file": self.location()["file"], "line": 200},
                         self.location() | {"secret": "fictional-secret-must-not-appear"}):
            self.report()
            self.set_result(errorLocation=location)
            self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")
        self.report()
        self.set_result(errors=["fictional-secret-must-not-appear"])
        self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")

    def test_oversized_internal_collections_are_unavailable(self):
        self.report()
        report = json.loads(self.report_path.read_text())
        spec = report["suites"][0]["specs"][0]
        test = spec["tests"][0]
        result = test["results"][0]
        for change in (
            lambda r: r["suites"][0].update(specs=[spec] * (_REPORT_MAX_SPECS + 1)),
            lambda r: r["suites"][0]["specs"][0].update(tests=[test] * (_REPORT_MAX_TESTS + 1)),
            lambda r: r["suites"][0]["specs"][0]["tests"][0].update(
                results=[result] * (_REPORT_MAX_RESULTS + 1)),
            lambda r: r["suites"][0]["specs"][0]["tests"][0]["results"][0].update(
                errors=[{}] * (_REPORT_MAX_ERRORS + 1)),
        ):
            current = json.loads(json.dumps(report))
            change(current)
            self.report_path.write_text(json.dumps(current))
            self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")

    def test_playwright_165_json_report_with_test_dir_reports_known_failure(self):
        # Shape observed from Playwright 1.65.0's JSON reporter with testDir="./e2e".
        # The real reporter emits spec.file relative to config.rootDir, without e2e/.
        secret = "fictional-secret-must-not-appear"
        report = {"config": {"rootDir": "/isolated/web/e2e"}, "suites": [{
            "title": "catalog.spec.ts", "file": "catalog.spec.ts", "line": 0,
            "column": 0, "specs": [{"title": "catalog-journey", "ok": False,
                "tags": [], "id": "fictional-case-id", "file": "catalog.spec.ts",
                "line": 2, "column": 1, "tests": [{"projectId": "desktop",
                    "projectName": "desktop", "expectedStatus": "passed",
                    "status": "unexpected", "results": [{"status": "failed",
                        "error": {"message": secret}, "stdout": [secret],
                        "stderr": [secret]}]}]}]}], "errors": [secret], "stats": {}}
        self.report_path.write_text(json.dumps(report))
        result = _safe_browser_failure_summary(self.report_path)
        self.assertNotEqual(result, "diagnostic_unavailable")
        self.assertEqual(json.loads(result), {"cases": [{"case": "catalog-journey",
            "file": "catalog.spec.ts", "line": 2, "project": "desktop",
            "status": "failed"}], "failed_count": 1, "truncated": False})
        self.assertNotIn(secret, result)

    def test_valid_report_without_failures_is_unavailable(self):
        self.report(outcome="expected", state="passed")
        self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")

    def test_missing_invalid_and_oversized_reports_are_unavailable(self):
        self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")
        self.report_path.write_text("{truncated")
        self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")
        self.report_path.write_bytes(b"x" * (_REPORT_MAX_BYTES + 1))
        self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")

    def test_unknown_values_cannot_enter_diagnostics(self):
        for changed in ({"file": "private/secret.spec.ts"},
                        {"file": "e2e/catalog.spec.ts"}, {"title": "secret"},
                        {"project": "secret"}, {"line": "171"}, {"state": "secret"}):
            self.report(**changed)
            self.assertEqual(_safe_browser_failure_summary(self.report_path), "diagnostic_unavailable")

    def test_many_failures_are_truncated_without_raw_data(self):
        self.report()
        report = json.loads(self.report_path.read_text())
        spec = report["suites"][0]["specs"][0]
        report["suites"][0]["specs"] = [dict(spec, title=title) for title in sorted(
            _BROWSER_CASES["catalog.spec.ts"])]
        self.report_path.write_text(json.dumps(report))
        result = _safe_browser_failure_summary(self.report_path)
        self.assertLessEqual(len(result.encode("ascii")), _DIAGNOSTIC_MAX_BYTES)
        self.assertEqual(json.loads(result)["failed_count"], len(_BROWSER_CASES["catalog.spec.ts"]))
        self.assertEqual(len(json.loads(result)["cases"]), _DIAGNOSTIC_MAX_CASES)
        self.assertTrue(json.loads(result)["truncated"])
        self.assertNotIn("fictional-secret-must-not-appear", result)

    def test_exit_code_preserved_and_raw_report_removed(self):
        self.report()
        self.set_result(errorLocation=self.location())
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(AssertionError):
            _assert_browser_exit(self, Mock(wait=Mock(return_value=1)), self.report_path,
                                 Path(self.temporary.name))
        self.assertIn("MIRA_BROWSER_FAILURE=", output.getvalue())
        self.assertIn('"failure_line":200', output.getvalue())
        self.assertNotIn("fictional-secret-must-not-appear", output.getvalue())
        self.assertFalse(self.report_path.exists())
        self.report()
        with redirect_stdout(output):
            _assert_browser_exit(self, Mock(wait=Mock(return_value=0)), self.report_path,
                                 Path(self.temporary.name))
        self.assertFalse(self.report_path.exists())
        self.report()
        with self.assertRaises(RuntimeError):
            _assert_browser_exit(self, Mock(wait=Mock(side_effect=RuntimeError("fictional-secret"))),
                                 self.report_path, Path(self.temporary.name))
        self.assertFalse(self.report_path.exists())


def assert_browser_database(test):
    # LiveServerTestCase uses modify_settings; Django's UserSettingsHolder then
    # reports SETTINGS_MODULE=None. Check the actual source and destination, not
    # a synthetic override of that metadata property.
    test.assertEqual(os.environ.get("DJANGO_SETTINGS_MODULE"), "config.settings.test")
    test.assertEqual(connection.vendor, "postgresql")
    test.assertEqual(connection.settings_dict["TEST"]["NAME"], "test_mira")
    test.assertFalse(connection.settings_dict["TEST"].get("MIRROR"))
    test.assertEqual(connection.settings_dict["NAME"], "test_mira")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        test.assertEqual(cursor.fetchone(), ("test_mira",))


@unittest.skipUnless(os.environ.get("MIRA_RUN_BROWSER_TESTS") == "1", "Browser suite requires its explicit isolated-runner gate")
class OperatorBrowserTests(LiveServerTestCase):
    host = "localhost"

    def test_real_browser_workflows_and_persisted_attribution(self):
        assert_browser_database(self)
        repository = Path(settings.BASE_DIR).resolve().parents[1]
        web = repository / "apps/web"
        artifacts = Path(tempfile.mkdtemp(prefix="mira-phase-1.13-browser-"))
        print(f"BROWSER_ARTIFACTS={artifacts}", flush=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        origin = f"http://localhost:{port}"
        password = secrets.token_urlsafe(32)
        accounts = {}
        from django.contrib.auth.models import Permission
        for project in ("desktop", "mobile"):
            for scenario in ("journey", "csrf", "denied", "revoked", "duplicate", "lost-creation", "lost-receipt", "expired",
                             "catalog-journey", "catalog-readonly", "catalog-revoked", "catalog-stale", "catalog-lost-category", "catalog-lost-change", "catalog-variants", "catalog-variants-lost", "catalog-price", "catalog-price-lost", "procurement-journey", "measured-journey", "pos-journey", "pos-lost"):
                username = f"fictional-{project}-{scenario}"
                user = get_user_model()(username=username)
                validate_password(password, user=user)
                user.set_password(password)
                user.save()
                if scenario != "denied":
                    permissions = PERMISSIONS
                    if scenario.startswith("catalog-"):
                        permissions = (*PERMISSIONS, "catalog.view_category", "catalog.add_category",
                                       "catalog.set_product_category", "catalog.set_product_active")
                    if scenario in ("journey", "catalog-variants"):
                        permissions = (*permissions, "inventory.adjust_stock")
                    if scenario in ("catalog-price", "catalog-price-lost"):
                        permissions = (*permissions, "catalog.set_variant_price", "catalog.view_variantpricerevision",
                                       "catalog.view_variant_price_history")
                    if scenario == "catalog-price":
                        permissions = (*permissions, "catalog.set_variant_measurement_spec", "catalog.view_variantmeasurementspec")
                    if scenario in ("procurement-journey", "measured-journey", "pos-journey", "pos-lost"):
                        permissions = (*permissions, "procurement.view_supplier", "procurement.manage_supplier",
                                       "procurement.view_purchaseorder", "procurement.create_purchase_order",
                                       "procurement.edit_purchase_order", "procurement.confirm_purchase_order",
                                       "procurement.receive_purchase_order", "inventory.view_stocklot",
                                       "inventory.view_stocklocation", "inventory.manage_stock_locations",
                                       "inventory.manage_stock_lots", "inventory.allocate_stock_lots",
                                       "inventory.read_availability", "inventory.view_stockreservation",
                                       "inventory.reserve_stock", "inventory.release_reservation",
                                       "inventory.consume_reservation", "inventory.set_location_eligibility")
                    if scenario == "measured-journey":
                        permissions = (*permissions, "catalog.view_category", "catalog.set_variant_price", "catalog.view_variantpricerevision",
                                       "catalog.view_variant_price_history", "inventory.read_physical_inventory", "inventory.create_physical_count",
                                       "inventory.record_physical_count", "inventory.review_physical_count", "inventory.complete_physical_count", "inventory.write_off_stock")
                    if scenario == "catalog-readonly":
                        permissions = ("catalog.view_product", "catalog.view_variant", "catalog.view_category")
                    if scenario in ("pos-journey", "pos-lost"):
                        permissions = ("pos.discover_products", "pos.quote_sale", "pos.confirm_sale")
                        if scenario == "pos-journey": permissions = (*permissions,"pos.read_sale","pos.list_sales")
                    for permission in permissions:
                        app, code = permission.split(".")
                        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
                accounts[username] = user.pk
        # Direct fictional fixtures are not fabricated operational Audit history.
        fixture_products = {}
        fixture_categories = {}
        for project in ("desktop", "mobile"):
            categories = {}
            for suffix in ("A", "B"):
                name = f"Fictional {project} Navigation {suffix}"
                categories[suffix] = Category.objects.create(name=name, name_key=name.casefold())
            fixture_categories[project] = categories
            for scenario in ("catalog-readonly", "catalog-revoked", "catalog-stale", "catalog-lost-change", "catalog-variants", "catalog-variants-lost", "catalog-price", "catalog-price-lost"):
                category = categories["B"] if scenario in ("catalog-readonly", "catalog-revoked") else categories["A"] if scenario == "catalog-stale" else None
                product = Product.objects.create(name=f"Fictional {project} {scenario}", category=category)
                Variant.objects.create(product=product, label="Fictional fixture", sku=f"CATALOG-FIXTURE-{project}-{scenario}")
                if scenario in ("catalog-price", "catalog-price-lost"):
                    Variant.objects.create(product=product, label="Fictional alternate", sku=f"CATALOG-FIXTURE-{project}-{scenario}-alternate")
                fixture_products[project, scenario] = product
            for index in range(51):
                product = Product.objects.create(name=f"Fictional {project} Page {index:03}", category=categories["A"])
                Variant.objects.create(product=product, label="Fictional page fixture", sku=f"CATALOG-FIXTURE-{project}-page-{index}")
        fixture_count = Product.objects.count()
        self.assertEqual(fixture_count, 118)
        procurement_variants = []
        for project in ("desktop", "mobile"):
            product = Product.objects.create(name=f"Fictional {project} purchase")
            for suffix in ("A", "B"):
                procurement_variants.append(Variant.objects.create(
                    product=product, label=f"Fictional pack {suffix}", sku=f"PROC-FIXTURE-{project}-{suffix}"))
        fixture_count = Product.objects.count()
        self.assertEqual(fixture_count, 120)
        measured_variants = []
        for project in ("desktop", "mobile"):
            product = Product.objects.create(name=f"Fictional {project} measured")
            measured_variants.append(Variant.objects.create(product=product,label="Fictional measured pack",sku=f"MEASURED-FIXTURE-{project}"))
        fixture_count = Product.objects.count()
        self.assertEqual(fixture_count, 122)
        from pos.tests.fixtures import prepare_catalog_stock
        pos_variants = []
        for project in ("desktop", "mobile"):
            _, _, _, prepared = prepare_catalog_stock(prefix=f"POS-{project}")
            pos_variants.extend(prepared)
        fixture_count = Product.objects.count()
        self.assertEqual(fixture_count, 126)
        # No PostgreSQL/Django secret is passed to Next or Playwright.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "PLAYWRIGHT_BROWSERS_PATH") if key in os.environ}
        env.update(MIRA_API_BASE_URL=self.live_server_url.replace("localhost", "127.0.0.1"), NEXT_TELEMETRY_DISABLED="1",
                   MIRA_E2E_BASE_URL=origin, MIRA_E2E_ARTIFACT_DIR=str(artifacts),
                   MIRA_E2E_PASSWORD=password, MIRA_RUN_BROWSER_TESTS="1")
        pnpm = shutil.which("pnpm")
        self.assertIsNotNone(pnpm, "Locked pnpm must be available; do not install a global replacement")
        self.assertEqual(_LOGIN_RESERVATIONS_PER_PROJECT, sum(len(cases) for cases in _BROWSER_CASES.values()))
        self.assertFalse(LoginAttemptWindow.objects.exists(), "Unexpected pre-existing browser login budget")
        print("BROWSER_LOGIN_BUDGET_INITIAL=30", flush=True)
        server = browser = None
        browsers = []
        report_paths = {project: artifacts / f"playwright-{project}.json" for project in ("desktop", "mobile")}
        with override_settings(OPERATOR_HTTP_ENABLED=True, OPERATOR_PUBLIC_ORIGIN=origin,
                               CSRF_TRUSTED_ORIGINS=[origin], SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False):
            try:
                with artifacts.joinpath("build.log").open("w") as log:
                    subprocess.run([pnpm, "build"], cwd=web, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
                with artifacts.joinpath("next.log").open("w") as server_log:
                    server = subprocess.Popen([pnpm, "exec", "next", "start", "--hostname", "127.0.0.1", "--port", str(port)], cwd=web, env=env, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if server.poll() is not None:
                            self.fail("Isolated Next server exited; inspect external logs")
                        try:
                            with urlopen(origin + "/login", timeout=1) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            time.sleep(.1)
                    else:
                        self.fail("Isolated Next server readiness deadline exceeded")
                    browser_deadline = time.monotonic() + 420

                    def run_project(project):
                        nonlocal browser
                        self.assertLess(time.monotonic(), browser_deadline, "Browser suite exceeded its bounded deadline")
                        self.assertIsNone(server.poll(), "Isolated Next server exited")
                        report_path = report_paths[project]
                        report_fd = os.open(report_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        os.close(report_fd)
                        try:
                            browser_env = {**env, "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report_path)}
                            browser = subprocess.Popen([pnpm, "exec", "playwright", "test", f"--project={project}",
                                                        "--reporter=list,json"], cwd=web, env=browser_env,
                                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                                       start_new_session=True)
                            browsers.append(browser)
                            lines = Queue()
                            def consume():
                                for line in browser.stdout:
                                    lines.put(line)
                            reader = Thread(target=consume, daemon=True)
                            reader.start()
                            with artifacts.joinpath("browser.log").open("a") as log:
                                log.write(f"BROWSER_BATCH={project}\n")
                                while reader.is_alive() or not lines.empty() or browser.poll() is None:
                                    if time.monotonic() > browser_deadline:
                                        self.fail("Browser suite exceeded its bounded deadline")
                                    try:
                                        line = lines.get(timeout=.2)
                                    except Empty:
                                        continue
                                    log.write(line)
                                    log.flush()
                                    if "MIRA_BROWSER_CONTROL=" in line:
                                        # Test-process IPC, never an HTTP endpoint or runtime adapter.
                                        command = json.loads(line.partition("MIRA_BROWSER_CONTROL=")[2])
                                        username, action = command["username"], command["action"]
                                        self.assertIn(username, accounts)
                                        self.assertIn(action, {"revoke", "expire"})
                                        assert_browser_database(self)
                                        if action == "revoke":
                                            get_user_model().objects.get(pk=accounts[username]).user_permissions.clear()
                                        else:
                                            for session in Session.objects.all():
                                                if session.get_decoded().get("_auth_user_id") == str(accounts[username]):
                                                    session.expire_date = timezone.now()
                                                    session.save(update_fields=["expire_date"])
                                        artifacts.joinpath(f"{action}-{username}.ack").touch()
                            _assert_browser_exit(self, browser, report_path, artifacts)
                            self.assertLessEqual(time.monotonic(), browser_deadline, "Browser suite exceeded its bounded deadline")
                            print(f"BROWSER_BATCH_PASS={project}", flush=True)
                        finally:
                            try:
                                _stop_owned_process(browser)
                            finally:
                                browser = None
                                report_path.unlink(missing_ok=True)

                    def between_projects():
                        self.assertIsNone(server.poll(), "Isolated Next server exited")
                        budget = _wait_for_login_capacity(lambda: _read_global_login_window(self),
                                                          required=_LOGIN_RESERVATIONS_PER_PROJECT,
                                                          deadline=browser_deadline)
                        print("BROWSER_LOGIN_BUDGET=" + json.dumps(budget, sort_keys=True), flush=True)

                    _run_project_sequence(run_project, between_projects)
            finally:
                try:
                    try:
                        _stop_owned_process(browser)
                    finally:
                        _stop_owned_process(server)
                finally:
                    for report_path in report_paths.values():
                        report_path.unlink(missing_ok=True)
                if server is not None:
                    released = False
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with socket.socket() as probe:
                            if probe.connect_ex(("127.0.0.1", port)) != 0:
                                released = True
                                break
                        time.sleep(.1)
                    self.assertTrue(released, "Isolated frontend port remained bound after cleanup")
                    artifacts.joinpath("lifecycle.json").write_text(json.dumps({
                        "frontend_pid": server.pid, "browser_pid": browsers[-1].pid if browsers else None,
                        "browser_pids": [process.pid for process in browsers],
                        "frontend_port": port, "frontend_stopped": server.poll() is not None,
                        "browser_stopped": all(process.poll() is not None for process in browsers),
                        "frontend_port_released": released,
                    }, sort_keys=True))
        legacy_variants = Variant.objects.filter(sku__startswith="FICTIONAL-")
        self.assertEqual(Product.objects.filter(variants__in=legacy_variants).count(), 10)
        self.assertEqual(legacy_variants.count(), 10)
        self.assertEqual(CatalogAuditEntry.objects.filter(variant_id__in=legacy_variants.values("id")).count(), 10)
        self.assertEqual(Product.objects.count(), fixture_count + 12)
        self.assertEqual(Variant.objects.count(), fixture_count + 22)
        self.assertEqual(CatalogAuditEntry.objects.count(), 16)
        self.assertEqual(StockPosition.objects.count(), 18)
        self.assertEqual(StockMovement.objects.count(), 40)
        self.assertEqual(InventoryMovementAuditEntry.objects.count(), 40)
        for variant in legacy_variants:
            audit = CatalogAuditEntry.objects.get(variant_id=variant.pk)
            owner = variant.sku.removeprefix("FICTIONAL-")
            self.assertEqual(audit.actor_id, str(accounts["fictional-" + owner]))
        for position in StockPosition.objects.exclude(variant_id__in=[variant.pk for variant in procurement_variants + measured_variants + pos_variants]):
            movements = list(StockMovement.objects.filter(position=position).order_by("sequence"))
            audits = [InventoryMovementAuditEntry.objects.get(movement_id=item.pk) for item in movements]
            actor = CatalogAuditEntry.objects.get(variant_id=position.variant_id).actor_id if CatalogAuditEntry.objects.filter(variant_id=position.variant_id).exists() else None
            if Variant.objects.get(pk=position.variant_id).sku.endswith("-journey"):
                self.assertEqual((position.on_hand, position.version), (7, 2))
                self.assertEqual([(item.kind, item.quantity_before, item.delta, item.quantity_after) for item in movements],
                                 [("RECEIPT", 0, 12, 12), ("ADJUSTMENT", 12, -5, 7)])
                self.assertEqual(movements[1].reason, "fictional physical count")
            else:
                self.assertEqual((position.on_hand, position.version), (12, 1))
                self.assertEqual(len(movements), 1)
            for movement, audit in zip(movements, audits):
                self.assertEqual((audit.actor_id, audit.sequence, audit.quantity_after), (actor, movement.sequence, movement.quantity_after))
        print("BROWSER_PERSISTENCE_PASS: 10 creations, 8 positions, 10 movements/audits; confirmed adjustments and lost responses reconciled", flush=True)

        self.assertEqual(Category.objects.count(), 10)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 22)
        for project in ("desktop", "mobile"):
            actor = str(accounts[f"fictional-{project}-catalog-journey"])
            entries = list(CatalogChangeAuditEntry.objects.filter(actor_id=actor).order_by("occurred_at"))
            self.assertEqual([entry.action for entry in entries], [
                "catalog.category_created", "catalog.product_category_changed", "catalog.product_active_changed",
                "catalog.product_category_changed", "catalog.product_active_changed", "catalog.product_category_changed",
            ])
            category_id = entries[0].target_id
            other_id = fixture_categories[project]["B"].id
            self.assertEqual([(entry.previous_category_id, entry.new_category_id) for entry in (entries[1], entries[3], entries[5])],
                             [(None, category_id), (category_id, other_id), (other_id, None)])
            self.assertEqual([(entry.previous_is_active, entry.new_is_active) for entry in (entries[2], entries[4])], [(False, True), (True, False)])
            variant = Variant.objects.get(sku=f"CATALOG-{project}-journey")
            self.assertEqual(CatalogAuditEntry.objects.get(variant_id=variant.pk).actor_id, actor)
            self.assertTrue(all(entry.target_id == variant.product_id for entry in entries[1:]))
            self.assertFalse(variant.product.is_active)
            self.assertIsNone(variant.product.category_id)
            lost = fixture_products[project, "catalog-lost-change"]
            lost.refresh_from_db()
            self.assertTrue(lost.is_active)
            self.assertEqual(lost.category_id, other_id)
            lost_entries = list(CatalogChangeAuditEntry.objects.filter(actor_id=str(accounts[f"fictional-{project}-catalog-lost-change"])).order_by("occurred_at"))
            self.assertEqual(len(lost_entries), 2)
            self.assertEqual((lost_entries[0].previous_category_id, lost_entries[0].new_category_id), (None, other_id))
            self.assertEqual((lost_entries[1].previous_is_active, lost_entries[1].new_is_active), (False, True))
            self.assertEqual(CatalogChangeAuditEntry.objects.filter(actor_id=str(accounts[f"fictional-{project}-catalog-lost-category"]), action="catalog.category_created").count(), 1)
            for scenario in ("catalog-readonly", "catalog-revoked", "catalog-stale"):
                self.assertFalse(CatalogChangeAuditEntry.objects.filter(actor_id=str(accounts[f"fictional-{project}-{scenario}"])).exists())
        print("CATALOG_BROWSER_PERSISTENCE_PASS: 2 creations, 4 category creations, 18 changes/audits; no-op and lost responses did not duplicate writes", flush=True)

        for project in ("desktop", "mobile"):
            for scenario, suffix in (("catalog-variants", "confirmed"), ("catalog-variants-lost", "lost")):
                product = fixture_products[project, scenario]
                self.assertEqual(product.variants.count(), 2)
                variant = Variant.objects.get(sku=f"BC01A-{project}-{suffix}")
                entry = CatalogAuditEntry.objects.get(variant_id=variant.pk)
                self.assertEqual((entry.action, entry.product_id, entry.actor_id),
                                 ("catalog.variant_added_to_product", product.pk, str(accounts[f"fictional-{project}-{scenario}"])))
                product.refresh_from_db()
                if suffix == "confirmed":
                    self.assertTrue(product.is_active)
                    self.assertEqual(product.category_id, fixture_categories[project]["B"].id)
                    self.assertEqual(CatalogChangeAuditEntry.objects.filter(target_id=product.pk, actor_id=entry.actor_id).count(), 2)
                if suffix == "lost":
                    self.assertFalse(StockPosition.objects.filter(variant_id=variant.pk).exists())
                else:
                    self.assertEqual(StockPosition.objects.get(variant_id=variant.pk).on_hand, 12)
                from catalog.models import VariantPriceRevision, VariantMeasurementSpec
                self.assertFalse(VariantPriceRevision.objects.filter(variant_id=variant.pk).exists())
                self.assertFalse(VariantMeasurementSpec.objects.filter(variant_id=variant.pk).exists())
        print("VARIANT_BROWSER_PERSISTENCE_PASS: 4 additions/audits; only explicit receipts, no price or measurement", flush=True)

        self.assertEqual(VariantPriceRevision.objects.count(), 12)
        self.assertEqual(CatalogPriceAuditEntry.objects.count(), 12)
        for project in ("desktop", "mobile"):
            for scenario, amounts in (("catalog-price", ["12.30", "14.50"]), ("catalog-price-lost", ["19.90"])):
                product = fixture_products[project, scenario]
                variant = product.variants.get(sku=f"CATALOG-FIXTURE-{project}-{scenario}")
                revisions = list(VariantPriceRevision.objects.filter(variant=variant).order_by("sequence"))
                self.assertEqual([str(item.amount) for item in revisions], amounts)
                self.assertFalse(VariantPriceRevision.objects.filter(variant__sku=f"CATALOG-FIXTURE-{project}-{scenario}-alternate").exists())
                actor = str(accounts[f"fictional-{project}-{scenario}"])
                entries = list(CatalogPriceAuditEntry.objects.filter(variant_id=variant.pk).order_by("occurred_at"))
                self.assertEqual(len(entries), len(revisions))
                self.assertTrue(all(entry.actor_id == actor for entry in entries))
                self.assertEqual([item.sequence for item in revisions], list(range(1, len(revisions) + 1)))
        print("PRICE_BROWSER_PERSISTENCE_PASS: 6 revisions/audits; no-op, conflict and lost response did not duplicate writes", flush=True)
        from catalog.models import VariantMeasurementSpec
        self.assertEqual(VariantMeasurementSpec.objects.count(), 6)
        self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), 8)
        for project in ("desktop", "mobile"):
            product = fixture_products[project, "catalog-price"]
            variant = product.variants.get(sku=f"CATALOG-FIXTURE-{project}-catalog-price")
            spec = VariantMeasurementSpec.objects.get(variant=variant)
            self.assertEqual((spec.version, spec.sale_unit, str(spec.net_content_amount), spec.net_content_unit),
                             (2, "EACH", "500.000000", "G"))
            self.assertFalse(VariantMeasurementSpec.objects.filter(variant__sku=f"CATALOG-FIXTURE-{project}-catalog-price-alternate").exists())
            entries = list(CatalogMeasurementAuditEntry.objects.filter(variant_id=variant.pk).order_by("new_version"))
            self.assertEqual([entry.new_version for entry in entries], [1, 2])
            self.assertTrue(all(entry.actor_id == str(accounts[f"fictional-{project}-catalog-price"]) for entry in entries))
            self.assertEqual([(entry.new_net_content_amount, entry.new_net_content_unit) for entry in entries],
                             [(spec.net_content_amount / 2, "G"), (spec.net_content_amount, "G")])
        print("MEASUREMENT_BROWSER_PERSISTENCE_PASS: 2 specifications; 4 versions/audits; no-op, conflict and malformed response did not duplicate writes", flush=True)

        self.assertEqual((Supplier.objects.count(), PurchaseOrder.objects.count(), GoodsReceipt.objects.count()), (4, 4, 8))
        self.assertEqual((ReceiptLine.objects.count(), ProcurementEvent.objects.count(), ProcurementAuditEntry.objects.count()), (12, 20, 20))
        self.assertEqual((StockLocation.objects.count(), StockLot.objects.count(), ReceiptAllocation.objects.count()), (6, 8, 8))
        self.assertEqual(StockLotEvent.objects.count(), InventoryLotAuditEntry.objects.count())
        for project in ("desktop", "mobile"):
            actor = str(accounts[f"fictional-{project}-procurement-journey"])
            order = PurchaseOrder.objects.get(supplier__name=f"Fictional {project} Supply Co")
            self.assertEqual((order.status, order.version, order.lines.count(), order.receipts.count()), ("COMPLETE", 4, 2, 2))
            self.assertEqual({line.variant_sku: (line.quantity, line.received_quantity) for line in order.lines.all()},
                             {f"PROC-FIXTURE-{project}-A": (5, 5), f"PROC-FIXTURE-{project}-B": (4, 4)})
            events = list(order.events.order_by("version"))
            self.assertEqual([event.action for event in events],
                             ["order.created", "order.confirmed", "order.received", "order.received"])
            self.assertTrue(all(event.actor_id == actor and ProcurementAuditEntry.objects.filter(
                event_id=event.pk, actor_id=actor).count() == 1 for event in events))
            self.assertEqual(ProcurementAuditEntry.objects.filter(supplier_id=order.supplier_id, actor_id=actor).count(), 5)
            for line in order.lines.all():
                position = StockPosition.objects.get(variant_id=line.variant_id)
                movements = list(StockMovement.objects.filter(position=position).order_by("sequence"))
                consumed = line.variant_sku.endswith("-A")
                self.assertEqual((position.on_hand, position.version), (line.quantity - (1 if consumed else 0), 3 if consumed else 2))
                self.assertEqual(len(movements), 3 if consumed else 2)
                self.assertTrue(all(
                                    InventoryMovementAuditEntry.objects.filter(movement_id=movement.pk, actor_id=actor).count() == 1
                                    for movement in movements))
                self.assertEqual(set(ReceiptLine.objects.filter(order_line=line).values_list("stock_movement_id", flat=True)),
                                 {movement.pk for movement in movements if movement.kind == "RECEIPT"})
                allocations = ReceiptAllocation.objects.filter(receipt_line__order_line=line)
                if line.variant_sku.endswith("-A"):
                    self.assertEqual((allocations.count(), StockLotBalance.objects.get(lot__variant_id=line.variant_id).quantity), (2, 4))
                else:
                    self.assertFalse(allocations.exists())
        print("PROCUREMENT_BROWSER_PERSISTENCE_PASS: 2 orders, 4 receipts, 8 receipt lines and stock movements; Procurement and Inventory Audit reconciled", flush=True)
        self.assertEqual(StockReservation.objects.count(), 12)
        self.assertEqual(ReservationAllocation.objects.count(), 14)
        self.assertEqual(ReservationEvent.objects.count(), ReservationAuditEntry.objects.count())
        for project in ("desktop", "mobile"):
            actor = str(accounts[f"fictional-{project}-procurement-journey"])
            rows = StockReservation.objects.filter(actor_id=actor)
            self.assertCountEqual(rows.values_list("state",flat=True), ["RELEASED","CONSUMED","ACTIVE"])
            consumed = rows.get(state="CONSUMED").allocations.get()
            self.assertEqual(StockMovement.objects.get(pk=consumed.stock_movement_id).kind,"CONSUMPTION")
        for event in ReservationEvent.objects.all():
            self.assertTrue(ReservationAuditEntry.objects.filter(event_id=event.pk, actor_id=event.actor_id, action=event.action).exists())
        print("RESERVATION_BROWSER_PERSISTENCE_PASS: 6 reservations; 2 releases, 2 consumptions and 2 uncertain results; 2 physical exits/Audit; no duplicate writes",flush=True)

        from inventory.models import PhysicalCount, PhysicalCountLine, PhysicalInventoryEvent, PhysicalLoss
        from audit.models import PhysicalInventoryAuditEntry
        self.assertEqual((PhysicalCount.objects.count(), PhysicalCountLine.objects.count(), PhysicalLoss.objects.count()),(2,2,2))
        self.assertEqual((PhysicalInventoryEvent.objects.count(),PhysicalInventoryAuditEntry.objects.count()),(10,10))
        for project in ("desktop", "mobile"):
            variant=Variant.objects.get(sku=f"MEASURED-FIXTURE-{project}")
            actor=str(accounts[f"fictional-{project}-measured-journey"])
            position=StockPosition.objects.get(variant_id=variant.pk)
            self.assertEqual((position.accounting_unit,position.on_hand,position.version),("G",899250,5))
            movements=list(position.movements.order_by("sequence"))
            self.assertEqual([(m.kind,m.delta,m.unit) for m in movements],[("RECEIPT",245001,"G"),("RECEIPT",754999,"G"),("CONSUMPTION",-100000,"G"),("COUNT",-500,"G"),("WRITE_OFF",-250,"G")])
            balance=0
            for movement in movements:
                self.assertEqual(movement.quantity_before,balance)
                balance+=movement.delta
                audit=InventoryMovementAuditEntry.objects.get(movement_id=movement.pk)
                self.assertEqual((movement.quantity_after,audit.actor_id,audit.unit,audit.delta,audit.quantity_after),(balance,actor,"G",movement.delta,balance))
            self.assertEqual(StockLotBalance.objects.get(lot__variant_id=variant.pk).quantity,position.on_hand)
            order=PurchaseOrder.objects.get(supplier__name=f"Fictional {project} Mass Supply")
            line=order.lines.get()
            self.assertEqual((order.status,line.unit,line.cost_basis,line.quantity,line.received_quantity),("COMPLETE","G","PER_KG",1000000,1000000))
            self.assertEqual(set(order.receipts.values_list("lines__stock_movement_id",flat=True)),{m.pk for m in movements if m.kind=="RECEIPT"})
            revision=VariantPriceRevision.objects.get(variant=variant)
            self.assertEqual((str(revision.amount),revision.price_basis),("1000.00","PER_KG"))
            self.assertEqual(CatalogPriceAuditEntry.objects.get(new_revision_id=revision.pk).actor_id,actor)
            count=PhysicalCount.objects.get(responsible_actor_id=actor)
            self.assertEqual((count.state,count.blind,count.lines.get().expected_quantity,count.lines.get().observed_quantity),("COMPLETED",True,900000,899500))
            loss=PhysicalLoss.objects.get(actor_id=actor)
            self.assertEqual((loss.quantity,loss.unit,loss.reason_code),(250,"G","DAMAGE"))
        for event in PhysicalInventoryEvent.objects.all():
            self.assertTrue(PhysicalInventoryAuditEntry.objects.filter(event_id=event.pk,actor_id=event.actor_id,action=event.action).exists())
        print("MEASURED_BROWSER_PERSISTENCE_PASS: 2 mass orders, 4 partial receipts, 2 consumptions, 2 counts and 2 losses; 10 G movements/Audit reconciled exactly",flush=True)

        from pos.models import Sale, SaleLine
        from audit.models import PosAuditEntry
        self.assertEqual(Sale.objects.count(),4)
        self.assertEqual(SaleLine.objects.count(),6)
        self.assertEqual(PosAuditEntry.objects.filter(action="pos.committed").count(),4)
        self.assertEqual(PosAuditEntry.objects.filter(action="pos.quoted").count(),6)
        self.assertEqual(PosAuditEntry.objects.count(),10)
        for project in ("desktop","mobile"):
            for scenario,total,count in (("pos-journey","224.50",2),("pos-lost","100.00",1)):
                actor=str(accounts[f"fictional-{project}-{scenario}"])
                sale=Sale.objects.get(actor_id=actor)
                self.assertEqual((str(sale.total_ars),sale.state,sale.settlement,sale.lines.count()),(total,"COMMITTED_UNSETTLED","UNSETTLED",count))
                self.assertEqual(PosAuditEntry.objects.get(sale_id=sale.pk).actor_id,actor)
                reservation=StockReservation.objects.get(pk=sale.reservation_id)
                self.assertEqual(reservation.state,"CONSUMED")
                for allocation in reservation.allocations.all():
                    movement=StockMovement.objects.get(pk=allocation.stock_movement_id)
                    self.assertEqual(movement.kind,"CONSUMPTION")
                    self.assertEqual(InventoryMovementAuditEntry.objects.get(movement_id=movement.pk).actor_id,actor)
            for suffix,on_hand in (("EACH",7),("MASS",754999)):
                position=StockPosition.objects.get(variant__sku=f"POS-{project}-{suffix}")
                self.assertEqual(position.on_hand,on_hand)
                self.assertEqual(position.on_hand,sum(position.movements.values_list("delta",flat=True)))
                self.assertEqual(position.on_hand,sum(StockLotBalance.objects.filter(lot__variant_id=position.variant_id).values_list("quantity",flat=True)))
        print("POS_BROWSER_PERSISTENCE_PASS: 4 unsettled sales, 6 immutable lines, 4 consumed reservations, 6 consumption movements and matching POS/Inventory Audit; uncertain writes reconciled without duplicate consumption",flush=True)
