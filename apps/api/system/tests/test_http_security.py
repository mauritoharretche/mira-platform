import io
import os
import runpy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import TestCase, override_settings

from identity.tests.test_http_authentication import PREFIX, make_operator, operator_client, sign_in
from inventory.tests.test_application import assert_test_database


class OperatorSecurityTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.client = operator_client()

    def test_native_runner_destination_is_accepted_without_guard_queries(self):
        # Includes the runner's direct SELECT current_database() verification.
        assert_test_database(self)
        with self.assertNumQueries(0), patch.object(BaseDatabaseWrapper, "ensure_connection", side_effect=AssertionError("Guard must not connect")) as connect:
            response = self.client.get(PREFIX + "/auth/csrf", secure=True)
        self.assertEqual(response.status_code, 200)
        connect.assert_not_called()

    def test_unsafe_configured_or_effective_destination_is_rejected_before_sessions(self):
        original = dict(connection.settings_dict)
        # Separate configured metadata from the cached connection wrapper.
        for unsafe in ("configured", "effective"):
            configured = dict(original, NAME="mira" if unsafe == "configured" else "test_mira")
            with self.subTest(unsafe=unsafe), self.assertNumQueries(0), patch.dict(settings.DATABASES, {"default": configured}), \
                    patch.dict(connection.settings_dict, {"NAME": "mira" if unsafe == "effective" else "test_mira"}), \
                    patch.object(BaseDatabaseWrapper, "ensure_connection", side_effect=AssertionError("Unsafe connection forbidden")) as connect, \
                    patch("django.contrib.sessions.middleware.SessionMiddleware.process_request", side_effect=AssertionError("Session middleware must not run")) as session, \
                    patch("django.contrib.auth.middleware.AuthenticationMiddleware.process_request", side_effect=AssertionError("Authentication middleware must not run")) as authentication:
                self.client.cookies[settings.SESSION_COOKIE_NAME] = "fictional-existing-session"
                response = self.client.get(PREFIX + "/auth/session", secure=True)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": {"code": "unavailable"}})
                connect.assert_not_called()
                session.assert_not_called()
                authentication.assert_not_called()

    def test_destination_overrides_and_mirrors_fail_closed_without_queries(self):
        original = dict(settings.DATABASES["default"])
        invalid = [dict(original, OPTIONS={"dbname": "mira"}),
                   dict(original, OPTIONS={"database": "mira"}),
                   dict(original, OPTIONS={"service": "fictional-service"}),
                   dict(original, TEST={"NAME": "mira"}),
                   dict(original, TEST={"NAME": "test_mira", "MIRROR": "other"}),
                   dict(original, ENGINE="django.db.backends.sqlite3")]
        for destination in invalid:
            with self.subTest(keys=sorted(destination)), self.assertNumQueries(0), patch.dict(settings.DATABASES, {"default": destination}), \
                    patch.object(BaseDatabaseWrapper, "ensure_connection", side_effect=AssertionError("Guard must not connect")) as connect:
                self.assertEqual(self.client.get(PREFIX + "/auth/csrf", secure=True).status_code, 503)
                connect.assert_not_called()

    def test_cached_connection_identity_cannot_be_hidden_by_safe_metadata(self):
        with self.assertNumQueries(0), patch.object(connection, "connection", SimpleNamespace(info=SimpleNamespace(dbname="mira"))), \
                patch.object(BaseDatabaseWrapper, "ensure_connection", side_effect=AssertionError("Guard must not connect")) as connect:
            self.assertEqual(self.client.get(PREFIX + "/auth/csrf", secure=True).status_code, 503)
            connect.assert_not_called()

    def test_independent_test_settings_process_cannot_open_operator_surface_on_mira(self):
        # Real child process, native settings.test, deliberately no test runner.
        # Only fictional credentials are supplied, and any connection attempt is
        # forbidden before even the first request. No mira connection is opened.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
        env.update(DJANGO_SETTINGS_MODULE="config.settings.test", DJANGO_SECRET_KEY="test-only-independent-process",
                   POSTGRES_DB="mira", POSTGRES_USER="fictional", POSTGRES_PASSWORD="fictional-independent-process",
                   POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="55432")
        worker = '''
import django, json
from contextlib import ExitStack
from unittest.mock import patch
django.setup()
from django.conf import settings
from django.db import connection
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import Client
assert settings.OPERATOR_HTTP_ENABLED is True
assert connection.settings_dict["NAME"] == "mira"
assert connection.settings_dict["TEST"]["NAME"] == "test_mira"
assert connection.connection is None
paths = ["auth/csrf", "auth/login", "auth/session", "auth/logout", "catalog/products", "catalog/products/invalid",
         "inventory/variants/invalid/initial-receipt", "inventory/variants/invalid/stock", "inventory/variants/invalid/movements"]
client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost", HTTP_ORIGIN="http://localhost:3000")
client.cookies[settings.SESSION_COOKIE_NAME] = "fictional-existing-session"
with ExitStack() as stack:
    guards = [stack.enter_context(patch(target, side_effect=AssertionError("Unsafe work forbidden"))) for target in
              ["django.contrib.sessions.middleware.SessionMiddleware.process_request",
               "django.contrib.auth.middleware.AuthenticationMiddleware.process_request",
               "django.contrib.auth.forms.authenticate", "catalog.application.create_product_with_initial_variant",
               "inventory.application.receive_stock"]]
    connect = stack.enter_context(patch.object(BaseDatabaseWrapper, "ensure_connection", side_effect=AssertionError("Unsafe connection forbidden")))
    for path in paths:
        for method in ("get", "post"):
            response = getattr(client, method)("/api/v1/operator/" + path, secure=True)
            assert response.status_code == 503, response.status_code
            assert response.json() == {"error": {"code": "unavailable"}}
            assert response["Cache-Control"] == "no-store"
    connect.assert_not_called()
    for guard in guards:
        guard.assert_not_called()
assert connection.connection is None
print(json.dumps({"rejected_requests": len(paths) * 2, "connections_opened": 0, "session_auth_business_calls": 0}))
'''
        process = subprocess.run([sys.executable, "-B", "-c", worker], cwd=settings.BASE_DIR, env=env,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, "Independent-process isolation regression failed")
        self.assertEqual(json.loads(process.stdout), {"rejected_requests": 18, "connections_opened": 0, "session_auth_business_calls": 0})

    def test_operator_is_disabled_in_base_and_all_endpoints_fail_closed_when_disabled(self):
        fake = {"DJANGO_SECRET_KEY": "fictional-settings-check", "POSTGRES_DB": "mira", "POSTGRES_USER": "fictional", "POSTGRES_PASSWORD": "fictional-settings-check"}
        with patch.dict(os.environ, fake, clear=True):
            base = runpy.run_path(str(Path(settings.BASE_DIR) / "config/settings/base.py"))
        self.assertFalse(base["OPERATOR_HTTP_ENABLED"])
        self.assertTrue(base["SESSION_COOKIE_SECURE"])
        self.assertTrue(base["CSRF_COOKIE_SECURE"])
        with override_settings(OPERATOR_HTTP_ENABLED=False):
            for path in ("auth/csrf", "auth/login", "auth/session", "auth/logout", "catalog/products", "catalog/products/invalid",
                         "inventory/variants/invalid/stock", "inventory/variants/invalid/movements", "inventory/variants/invalid/initial-receipt"):
                with self.subTest(path=path), self.assertNumQueries(0):
                    response = self.client.get(PREFIX + "/" + path, secure=True)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(self.client.post(PREFIX + "/" + path, secure=True).status_code, 404)

    def test_non_loopback_and_invalid_config_are_rejected_without_database_queries(self):
        for remote in ("203.0.113.10", "invalid"):
            with self.subTest(remote=remote), self.assertNumQueries(0):
                self.assertEqual(self.client.get(PREFIX + "/auth/csrf", REMOTE_ADDR=remote, secure=True).status_code, 404)
        for origin in ("https://outside.example", "http://user:secret@localhost:3000", "http://localhost:3000/path"):
            with self.subTest(origin=origin), override_settings(OPERATOR_PUBLIC_ORIGIN=origin):
                self.assertEqual(self.client.get(PREFIX + "/auth/csrf", secure=True).status_code, 404)

    def test_identity_and_forwarding_headers_do_not_authenticate(self):
        response = self.client.get(PREFIX + "/catalog/products/" + str(uuid4()), secure=True,
                                   HTTP_AUTHORIZATION="Basic ZmFrZTpmYWtl", HTTP_X_USER_ID="1", HTTP_REMOTE_USER="1",
                                   HTTP_X_FORWARDED_HOST="localhost:3000", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")
        response = self.client.get(PREFIX + "/auth/csrf", secure=True, HTTP_HOST="untrusted.example")
        self.assertEqual(response.status_code, 400)

    def test_body_limit_and_security_headers(self):
        response = self.client.post(PREFIX + "/auth/login", "x" * 17000, content_type="application/x-www-form-urlencoded", secure=True)
        self.assertEqual(response.status_code, 413)
        for key, value in {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"}.items():
            self.assertEqual(response[key], value)

    def test_method_parser_and_unapproved_surface_are_not_enabled(self):
        make_operator()
        token = sign_in(self.client).json()["csrf_token"]
        denied = self.client.get(PREFIX + "/auth/login", secure=True)
        self.assertEqual(denied.status_code, 405)
        self.assertEqual(denied.json()["error"]["code"], "method_not_allowed")
        self.assertIn("POST", denied["Allow"])
        response = self.client.put(PREFIX + "/catalog/products", "{}", content_type="application/json", secure=True, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 405)
        response = self.client.post(PREFIX + "/catalog/products", "bad-json", content_type="application/json", secure=True, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 400)
        response = self.client.post(PREFIX + "/catalog/products", "name=demo", content_type="application/x-www-form-urlencoded", secure=True, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 415)
        for path in ("/admin/", PREFIX + "/catalog/categories", PREFIX + "/inventory/adjustments"):
            self.assertEqual(self.client.get(path, secure=True).status_code, 404)

    def test_session_storage_failure_is_generic_even_before_view_dispatch(self):
        make_operator()
        sign_in(self.client)
        self.client.raise_request_exception = False
        with patch("django.contrib.sessions.backends.db.SessionStore.load", side_effect=DatabaseError("fictional session secret")):
            response = self.client.get(PREFIX + "/auth/session", secure=True)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": {"code": "unavailable"}})

    def test_migration_drift_sql_and_installed_integrity_are_checked_only_in_test_database(self):
        assert_test_database(self)
        call_command("makemigrations", check=True, dry_run=True, interactive=False, stdout=io.StringIO())
        output = io.StringIO()
        call_command("sqlmigrate", "identity", "0001_login_attempt_window", stdout=output)
        sql = output.getvalue()
        self.assertIn('CREATE TABLE "identity_loginattemptwindow"', sql)
        self.assertIn('"identity_login_attempts_bounded"', sql)
        self.assertNotIn("DROP TABLE", sql)
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'identity_loginattemptwindow'::regclass")
            constraints = dict(cursor.fetchall())
        self.assertIn("identity_login_attempts_bounded", constraints)
        artifact = os.environ.get("MIRA_REVIEW_ARTIFACT_DIR")
        if artifact:
            directory = Path(artifact).resolve()
            repository = Path(settings.BASE_DIR).resolve().parents[1]
            if directory == repository or repository in directory.parents:
                raise AssertionError("Review artifacts must remain outside the repository")
            directory.joinpath("identity-migration.sql").write_text(sql)
            directory.joinpath("identity-constraints.txt").write_text(repr(constraints) + "\n")
