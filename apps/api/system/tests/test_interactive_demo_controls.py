"""Control tests use mock routing or the native test database, never development."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.contrib.auth.models import Permission
from django.db import connection
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import SimpleTestCase, TestCase

from identity.application import require_capability
from identity.contracts import AccessDenied, Capability
from system.tests import test_interactive_demo as interactive

demo = interactive.demo
create_demo_account = interactive.create_demo_account


def destination(name="mira"):
    return {"ENGINE": "django.db.backends.postgresql", "HOST": "127.0.0.1", "PORT": "55432", "NAME": name,
            "TEST": {"NAME": "test_mira"}, "OPTIONS": {}}


def runtime(directory, seconds=10):
    return demo.RuntimeSession(directory, {"parent": demo.process_identity(os.getpid()), "duration_seconds": seconds, "verification": False})


@contextmanager
def _account_test_database_validation():
    """Adapt only CI port metadata; the original verifier still checks real SQL."""
    if os.environ.get("CI") != "true" or os.environ.get("GITHUB_ACTIONS") != "true":
        yield
        return
    original = demo.validate_destination

    def validate_account_destination(metadata, *, effective):
        if str(metadata.get("PORT")) == "5432":
            demo.require(effective is True, "CI adaptation requires native test database selection")
            metadata = {**metadata, "PORT": "55432"}
        return original(metadata, effective=effective)

    with patch.object(demo, "validate_destination", side_effect=validate_account_destination):
        yield


class InteractiveDemoCIAdapterTests(SimpleTestCase):
    """Scripted connection doubles only; real SQL is checked by the account tests."""

    @contextmanager
    def _verifier_fixture(self, *, settings_metadata=None, connection_metadata=None,
                          cached_database="test_mira", sql_database="test_mira",
                          settings_module="config.settings.test"):
        configured = deepcopy(settings_metadata if settings_metadata is not None else {**destination("test_mira"), "PORT": "5432"})
        connected = deepcopy(connection_metadata if connection_metadata is not None else configured)
        cursor = Mock()
        cursor.fetchone.return_value = (sql_database,)
        manager = MagicMock()
        manager.__enter__.return_value = cursor
        fake_connection = SimpleNamespace(settings_dict=connected,
                                          connection=SimpleNamespace(info=SimpleNamespace(dbname=cached_database)),
                                          cursor=Mock(return_value=manager))
        fake_settings = SimpleNamespace(DATABASES={"default": configured})
        with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": "true", "DJANGO_SETTINGS_MODULE": settings_module}), \
                patch("django.conf.settings", fake_settings), patch("django.db.connection", fake_connection):
            yield fake_settings, fake_connection, cursor

    def test_real_launcher_rejects_ci_port_before_and_after_adapter(self):
        metadata = {**destination("test_mira"), "PORT": "5432"}
        original = demo.validate_destination
        with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": "true"}):
            with self.assertRaises(demo.DemoError):
                original(metadata, effective=True)
            with _account_test_database_validation():
                demo.validate_destination(metadata, effective=True)
                with self.assertRaises(demo.DemoError):
                    original(metadata, effective=True)
            self.assertIs(demo.validate_destination, original)
            with self.assertRaises(demo.DemoError):
                demo.validate_destination(metadata, effective=True)

    def test_adapter_requires_both_exact_github_actions_ci_flags(self):
        original = demo.validate_destination
        for ci, actions in (("", ""), ("true", ""), ("", "true"), ("1", "true"), ("true", "1"), ("true", "false")):
            with self.subTest(ci=ci, actions=actions), patch.dict(os.environ, {"CI": ci, "GITHUB_ACTIONS": actions}):
                with _account_test_database_validation():
                    self.assertIs(demo.validate_destination, original)
                    with self.assertRaises(demo.DemoError):
                        demo.validate_destination({**destination("test_mira"), "PORT": "5432"}, effective=True)

    def test_adapter_rejects_other_ports_and_preselection_metadata(self):
        with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": "true"}), _account_test_database_validation():
            for port in ("5433", "", None):
                with self.subTest(port=port), self.assertRaises(demo.DemoError):
                    demo.validate_destination({**destination("test_mira"), "PORT": port}, effective=True)
            with self.assertRaises(demo.DemoError):
                demo.validate_destination({**destination(), "PORT": "5432"}, effective=False)

    def test_all_original_destination_invariants_apply_to_both_metadata_sources(self):
        invalid = ({"ENGINE": "django.db.backends.sqlite3"}, {"HOST": "external.example"}, {"NAME": "mira"},
                   {"NAME": "test_other"}, {"TEST": {"NAME": "test_other"}},
                   {"TEST": {"NAME": "test_mira", "MIRROR": "other"}},
                   {"OPTIONS": {"dbname": "mira"}}, {"OPTIONS": {"database": "mira"}}, {"OPTIONS": {"service": "other"}})
        for changes in invalid:
            for source in ("settings_metadata", "connection_metadata"):
                metadata = {**destination("test_mira"), "PORT": "5432", **changes}
                with self.subTest(source=source, changes=changes), self._verifier_fixture(**{source: metadata}) as (_, database, cursor):
                    with _account_test_database_validation(), self.assertRaises(demo.DemoError):
                        demo.verify_runner_database()
                    database.cursor.assert_not_called()
                    cursor.execute.assert_not_called()

    def test_exact_settings_module_still_fails_before_queries(self):
        with self._verifier_fixture(settings_module="config.settings.development") as (_, database, cursor):
            with _account_test_database_validation(), self.assertRaises(demo.DemoError):
                demo.verify_runner_database()
            database.cursor.assert_not_called()
            cursor.execute.assert_not_called()

    def test_wrong_cached_database_fails_before_any_sql(self):
        with self._verifier_fixture(cached_database="mira") as (_, database, cursor):
            with _account_test_database_validation(), self.assertRaises(demo.DemoError):
                demo.verify_runner_database()
            database.cursor.assert_not_called()
            cursor.execute.assert_not_called()

    def test_original_verifier_executes_sql_and_rejects_wrong_result(self):
        with self._verifier_fixture(sql_database="mira") as (_, _, cursor):
            with _account_test_database_validation(), self.assertRaises(demo.DemoError):
                demo.verify_runner_database()
            cursor.execute.assert_called_once_with("SELECT current_database()")

    def test_post_sql_connection_identity_is_checked_independently(self):
        with self._verifier_fixture() as (_, database, cursor):
            def changed_connection():
                database.connection.info.dbname = "mira"
                return ("test_mira",)
            cursor.fetchone.side_effect = changed_connection
            with _account_test_database_validation(), self.assertRaises(demo.DemoError):
                demo.verify_runner_database()
            cursor.execute.assert_called_once_with("SELECT current_database()")

    def test_only_metadata_copy_is_normalized_and_original_validation_runs(self):
        verifier, guard = demo.verify_runner_database, demo.install_connection_guard
        original = demo.validate_destination
        with self._verifier_fixture() as (settings_view, database, cursor):
            configured = settings_view.DATABASES["default"]
            connected = database.settings_dict
            active = database.connection
            before = deepcopy((configured, connected))
            environment = dict(os.environ)
            with patch.object(demo, "validate_destination", wraps=original) as delegated:
                with _account_test_database_validation():
                    demo.verify_runner_database()
                    self.assertIs(demo.verify_runner_database, verifier)
                    self.assertIs(demo.install_connection_guard, guard)
                self.assertEqual(delegated.call_count, 2)
                for call, metadata in zip(delegated.call_args_list, (configured, connected)):
                    self.assertIsNot(call.args[0], metadata)
                    self.assertEqual(call.args[0], {**metadata, "PORT": "55432"})
                    self.assertEqual(call.kwargs, {"effective": True})
            self.assertEqual((configured, connected), before)
            self.assertIs(settings_view.DATABASES["default"], configured)
            self.assertIs(database.settings_dict, connected)
            self.assertIs(database.connection, active)
            self.assertTrue(dict(os.environ) == environment, "Adapter changed the process environment")
            cursor.execute.assert_called_once_with("SELECT current_database()")
        self.assertIs(demo.validate_destination, original)

    def test_context_restores_original_validator_after_exception(self):
        original = demo.validate_destination
        with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": "true"}):
            with self.assertRaises(demo.DemoError):
                with _account_test_database_validation():
                    demo.validate_destination({**destination("mira"), "PORT": "5432"}, effective=True)
            self.assertIs(demo.validate_destination, original)

    def test_local_verification_keeps_strict_port_and_original_sql_path(self):
        original = demo.validate_destination
        with self._verifier_fixture(settings_metadata=destination("test_mira")) as (_, _, cursor):
            with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}), _account_test_database_validation():
                self.assertIs(demo.validate_destination, original)
                demo.verify_runner_database()
            cursor.execute.assert_called_once_with("SELECT current_database()")


class InteractiveDemoControlTests(SimpleTestCase):
    def test_default_discovery_cannot_activate_interactive_case(self):
        self.assertTrue(interactive.InteractiveOperatorDemoTests.__unittest_skip__)
        self.assertIsNone(demo.ACTIVE_SESSION)

    def test_cli_and_ci_optin_are_fail_closed_before_startup(self):
        cases = [SimpleNamespace(command="start", minutes=30, exclusive_test_database=False),
                 SimpleNamespace(command="start", minutes=0, exclusive_test_database=True),
                 SimpleNamespace(command="start", minutes=61, exclusive_test_database=True)]
        with patch.dict(os.environ, {}, clear=True), patch.object(demo.subprocess, "check_output") as command:
            for args in cases:
                with self.assertRaises(demo.DemoError):
                    demo.supervise(args)
            command.assert_not_called()
        with patch.dict(os.environ, {"CI": "1"}, clear=True), self.assertRaises(demo.DemoError):
            demo.supervise(SimpleNamespace(command="start", minutes=30, exclusive_test_database=True))
        with patch.dict(os.environ, {"DJANGO_SETTINGS_MODULE": "config.settings.development"}, clear=True), self.assertRaises(demo.DemoError):
            demo.supervise(SimpleNamespace(command="start", minutes=30, exclusive_test_database=True))

    def test_invalid_database_destinations_are_rejected_without_queries(self):
        for changes in ({"ENGINE": "django.db.backends.sqlite3"}, {"HOST": "external.example"}, {"PORT": "5432"},
                        {"TEST": {"NAME": "test_other"}}, {"TEST": {"NAME": "test_mira", "MIRROR": "other"}},
                        {"OPTIONS": {"dbname": "mira"}}, {"OPTIONS": {"database": "mira"}}, {"OPTIONS": {"service": "other"}},
                        {"NAME": "mira"}):
            with self.subTest(changes=changes), self.assertRaises(demo.DemoError):
                demo.validate_destination({**destination("test_mira"), **changes}, effective=True)
        with self.assertRaises(demo.DemoError):
            demo.validate_destination(destination("test_mira"), effective=False)

    def test_connection_guard_never_delegates_a_development_connection(self):
        original = Mock()
        with patch.object(BaseDatabaseWrapper, "ensure_connection", original):
            demo.install_connection_guard()
            guard = BaseDatabaseWrapper.ensure_connection
            with self.assertRaises(demo.DemoError):
                guard(SimpleNamespace(settings_dict=destination(), alias="default"))
            original.assert_not_called()
            guard(SimpleNamespace(settings_dict=destination("test_mira"), alias="default"))
            self.assertEqual(original.call_count, 1)
            with self.assertRaises(demo.DemoError):
                guard(SimpleNamespace(settings_dict=destination("mira"), alias="__no_db__"))
            self.assertEqual(original.call_count, 1)

    def test_exact_settings_required_before_database_probe(self):
        cursor = Mock()
        with patch.dict(os.environ, {"DJANGO_SETTINGS_MODULE": "config.settings.development"}), patch("django.db.connection", SimpleNamespace(cursor=cursor)):
            with self.assertRaises(demo.DemoError):
                demo.verify_runner_database()
            cursor.assert_not_called()

    def test_cached_unsafe_connection_is_rejected_before_any_sql(self):
        fake = SimpleNamespace(settings_dict=destination("test_mira"), connection=SimpleNamespace(info=SimpleNamespace(dbname="mira")), cursor=Mock())
        from django.conf import settings
        with patch.dict(settings.DATABASES, {"default": destination("test_mira")}), patch("django.db.connection", fake), self.assertRaises(demo.DemoError):
            demo.verify_runner_database()
        fake.cursor.assert_not_called()

    def test_private_files_reject_modes_symlinks_and_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "access.json"
            demo.write_private(path, {"password": "fictional-private-test"}, exclusive=True)
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                demo.write_private(path, b"overwrite", exclusive=True)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(demo.DemoError):
                demo.private_bytes(link)
            path.chmod(0o644)
            with self.assertRaises(demo.DemoError):
                demo.private_bytes(path)
            path.chmod(0o600)
            demo.remove_credentials(directory)
            self.assertFalse(path.exists())

    def test_backend_credentials_and_demo_password_are_not_child_environment(self):
        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "fictional-db-secret", "MIRA_E2E_PASSWORD": "fictional-demo-secret", "DJANGO_SECRET_KEY": "fictional-django-secret"}):
            env = demo.frontend_environment("http://127.0.0.1:12345")
        self.assertNotIn("POSTGRES_PASSWORD", env)
        self.assertNotIn("MIRA_E2E_PASSWORD", env)
        self.assertNotIn("DJANGO_SECRET_KEY", env)
        self.assertEqual(env["MIRA_API_BASE_URL"], "http://127.0.0.1:12345")

    def test_redaction_handles_split_writes_without_secret_output(self):
        output = io.StringIO()
        stream = demo.RedactedStream(output, ["fictional-private-value"])
        stream.write("prefix fictional-pr")
        stream.write("ivate-value suffix\n")
        stream.flush()
        self.assertNotIn("fictional-private-value", output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())

    def test_private_log_scan_detects_credential_leak_without_printing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner.log"
            demo.write_private(path, b"fictional-sensitive-runtime-value\n", exclusive=True)
            with self.assertRaises(demo.DemoError) as failure:
                demo.verify_no_secret_logs(directory, ["fictional-sensitive-runtime-value"])
            self.assertNotIn("fictional-sensitive-runtime-value", str(failure.exception))
            demo.write_private(path, b"[REDACTED]\n")
            demo.verify_no_secret_logs(directory, ["fictional-sensitive-runtime-value"])

    def test_deadline_is_monotonic_and_independent_of_wall_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            session = runtime(directory)
            with patch.object(demo.time, "monotonic", return_value=100), patch.object(demo.time, "time", return_value=-999999):
                self.assertFalse(session.should_stop(101))
            with patch.object(demo.time, "monotonic", return_value=102):
                self.assertTrue(session.should_stop(101))
            self.assertEqual(session.reason, "deadline")

    def test_stop_marker_and_parent_loss_request_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            session = runtime(directory)
            demo.request_stop(directory)
            self.assertTrue(session.should_stop(time.monotonic() + 60))
            self.assertEqual(session.reason, "STOP")
        with tempfile.TemporaryDirectory() as directory:
            session = runtime(directory)
            with patch.object(demo, "process_identity", return_value=None):
                self.assertTrue(session.should_stop(time.monotonic() + 60))
            self.assertEqual(session.reason, "parent_lost")

    def test_process_identity_is_independent_of_django_timezone_changes(self):
        with patch.dict(os.environ, {"TZ": "Pacific/Auckland"}):
            first = demo.process_identity(os.getpid())
        with patch.dict(os.environ, {"TZ": "UTC"}):
            second = demo.process_identity(os.getpid())
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    def test_sigint_and_sigterm_request_stop_without_signalling_unrelated_processes(self):
        callback = Mock()
        with patch.object(demo.signal, "signal") as install:
            previous = demo.install_stop_signals(callback)
        self.assertEqual(set(previous), {signal.SIGINT, signal.SIGTERM})
        self.assertEqual(install.call_count, 2)
        for invocation in install.call_args_list:
            invocation.args[1](invocation.args[0], None)
        self.assertEqual(callback.call_count, 2)

    def test_lock_refuses_concurrent_demo_and_stale_incomplete_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(demo, "lease_path", return_value=Path(directory) / "lease"):
            with demo.Lease() as first:
                with self.assertRaises(demo.DemoError):
                    with demo.Lease():
                        self.fail("Concurrent demo allowed")
                first.state({"state": "incomplete"})
            with self.assertRaises(demo.DemoError):
                with demo.Lease():
                    self.fail("Incomplete teardown ignored")

    def test_recovery_refuses_reused_or_unowned_pid_without_signalling(self):
        record = {"pid": 98765, "pgid": 98765, "uid": os.getuid(), "birth": "fictional-birth"}
        with patch.object(demo, "process_identity", return_value={**record, "birth": "other"}), patch.object(demo.os, "killpg") as kill:
            with self.assertRaises(demo.DemoError):
                demo.signal_owned(record, signal.SIGTERM)
            kill.assert_not_called()
        with patch.object(demo.os, "killpg") as kill, self.assertRaises(demo.DemoError):
            demo.signal_owned({**record, "uid": os.getuid() + 1}, signal.SIGTERM)
        kill.assert_not_called()

    def test_owned_process_and_port_cleanup_use_recorded_identity(self):
        # A disposable HTTP process, not Django and not connected to any database.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, "-I", "-B", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        record = demo.record_process(process)
        try:
            deadline = time.monotonic() + 5
            while demo.port_closed(port) and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertFalse(demo.port_closed(port))
            demo.terminate_owned(record, grace=2)
            process.wait(timeout=3)
            self.assertTrue(demo.wait_port_closed(port))
        finally:
            if process.poll() is None:
                demo.signal_owned(record, signal.SIGKILL)
                process.wait(timeout=3)

    def test_build_startup_failure_and_stop_cleanup_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            session = runtime(directory)
            with self.assertRaises(demo.DemoError):
                demo.owned_command(session, [sys.executable, "-I", "-B", "-c", "raise SystemExit(2)"], env={}, cwd=directory, timeout=2)
            self.assertTrue(demo.read_state(Path(directory) / "build-stopped.json")["stopped"])
        with tempfile.TemporaryDirectory() as directory:
            session = runtime(directory)
            demo.request_stop(directory)
            with self.assertRaises(demo.DemoError):
                demo.owned_command(session, [sys.executable, "-I", "-B", "-c", "import time; time.sleep(60)"], env={}, cwd=directory, timeout=2)
            record = demo.read_state(Path(directory) / "build-process.json")
            self.assertIsNone(demo.process_identity(record["pid"]))

    def test_abnormal_recovery_does_not_claim_native_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            process = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time; time.sleep(60)"], start_new_session=True)
            record = demo.record_process(process)
            demo.write_private(path / "session.json", {"parent": {"uid": os.getuid()}}, exclusive=True)
            demo.write_private(path / "runner-process.json", record, exclusive=True)
            demo.write_private(path / "access.json", {"password": "fictional-abnormal-test"}, exclusive=True)
            demo.signal_owned(record, signal.SIGKILL)
            process.wait(timeout=3)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(demo.recover_stop(SimpleNamespace(session=directory)), 1)
            result = demo.read_state(path / "recovery-result.json")
            self.assertFalse(result["native_cleanup_verified"])
            self.assertFalse(result["normal_shutdown"])
            self.assertTrue(result["credentials_file_removed"])
            self.assertNotIn("fictional-abnormal-test", output.getvalue())


class InteractiveDemoAccountTests(TestCase):
    def _assert_native_database_identity(self):
        demo.verify_runner_database()
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        self.assertEqual(connection.connection.info.dbname, "test_mira")

    def test_unexpected_data_is_rejected_before_listener_or_fixture_setup(self):
        with _account_test_database_validation(), tempfile.TemporaryDirectory() as directory:
            self._assert_native_database_identity()
            create_demo_account(runtime(directory))
            with self.assertRaises(demo.DemoError):
                interactive.require_empty_demo_database()

    def test_account_has_exactly_eleven_permissions_and_no_unrelated_capabilities(self):
        with _account_test_database_validation(), tempfile.TemporaryDirectory() as directory:
            self._assert_native_database_identity()
            session = runtime(directory)
            user = create_demo_account(session)
            access = demo.read_state(Path(directory) / "access.json")
            self.assertFalse(user.is_staff)
            self.assertFalse(user.is_superuser)
            self.assertTrue(user.check_password(access["password"]))
            self.assertEqual(set(user.get_all_permissions()), set(demo.PERMISSIONS))
            self.assertEqual(user.user_permissions.count(), 11)
            self.assertEqual(Permission.objects.filter(user=user).count(), 11)
            require_capability(principal=user, capability=Capability.READ_PRODUCT)
            for capability in (Capability.ADJUST_STOCK, Capability.SET_VARIANT_PRICE, Capability.SET_VARIANT_MEASUREMENT_SPEC):
                with self.assertRaises(AccessDenied):
                    require_capability(principal=user, capability=capability)
            self.assertEqual(stat.S_IMODE((Path(directory) / "access.json").stat().st_mode), 0o600)
            self.assertNotIn(access["password"], json.dumps(demo.frontend_environment("http://127.0.0.1:12345")))
            demo.remove_credentials(directory)
