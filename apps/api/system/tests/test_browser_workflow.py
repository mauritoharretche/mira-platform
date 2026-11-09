"""Browser lifecycle belongs exclusively to the native isolated Django runner."""
import json
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
from urllib.request import urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry, InventoryMovementAuditEntry
from catalog.models import Category, Product, Variant
from identity.tests.test_http_authentication import PERMISSIONS
from inventory.models import StockMovement, StockPosition


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
                             "catalog-journey", "catalog-readonly", "catalog-revoked", "catalog-stale", "catalog-lost-category", "catalog-lost-change"):
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
                    if scenario == "catalog-readonly":
                        permissions = ("catalog.view_product", "catalog.view_variant", "catalog.view_category")
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
            for scenario in ("catalog-readonly", "catalog-revoked", "catalog-stale", "catalog-lost-change"):
                category = categories["B"] if scenario in ("catalog-readonly", "catalog-revoked") else categories["A"] if scenario == "catalog-stale" else None
                product = Product.objects.create(name=f"Fictional {project} {scenario}", category=category)
                Variant.objects.create(product=product, label="Fictional fixture", sku=f"CATALOG-FIXTURE-{project}-{scenario}")
                fixture_products[project, scenario] = product
            for index in range(51):
                product = Product.objects.create(name=f"Fictional {project} Page {index:03}", category=categories["A"])
                Variant.objects.create(product=product, label="Fictional page fixture", sku=f"CATALOG-FIXTURE-{project}-page-{index}")
        fixture_count = Product.objects.count()
        self.assertEqual(fixture_count, 110)
        # No PostgreSQL/Django secret is passed to Next or Playwright.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "PLAYWRIGHT_BROWSERS_PATH") if key in os.environ}
        env.update(MIRA_API_BASE_URL=self.live_server_url.replace("localhost", "127.0.0.1"), NEXT_TELEMETRY_DISABLED="1",
                   MIRA_E2E_BASE_URL=origin, MIRA_E2E_ARTIFACT_DIR=str(artifacts),
                   MIRA_E2E_PASSWORD=password, MIRA_RUN_BROWSER_TESTS="1")
        pnpm = shutil.which("pnpm")
        self.assertIsNotNone(pnpm, "Locked pnpm must be available; do not install a global replacement")
        server = browser = None
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
                    browser = subprocess.Popen([pnpm, "exec", "playwright", "test"], cwd=web, env=env,
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                    lines = Queue()
                    def consume():
                        for line in browser.stdout:
                            lines.put(line)
                    reader = Thread(target=consume, daemon=True)
                    reader.start()
                    deadline = time.monotonic() + 420
                    with artifacts.joinpath("browser.log").open("w") as log:
                        while reader.is_alive() or not lines.empty() or browser.poll() is None:
                            if time.monotonic() > deadline:
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
                    self.assertEqual(browser.wait(timeout=5), 0, f"Browser failure; inspect {artifacts}/browser.log")
            finally:
                import signal
                for process in (browser, server):
                    if process is not None:
                        # Stop only process groups created here, including Next's child.
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
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
                        "frontend_pid": server.pid, "browser_pid": browser.pid if browser else None,
                        "frontend_port": port, "frontend_stopped": server.poll() is not None,
                        "browser_stopped": browser is None or browser.poll() is not None,
                        "frontend_port_released": released,
                    }, sort_keys=True))
        legacy_variants = Variant.objects.filter(sku__startswith="FICTIONAL-")
        self.assertEqual(Product.objects.filter(variants__in=legacy_variants).count(), 10)
        self.assertEqual(legacy_variants.count(), 10)
        self.assertEqual(CatalogAuditEntry.objects.filter(variant_id__in=legacy_variants.values("id")).count(), 10)
        self.assertEqual(Product.objects.count(), fixture_count + 12)
        self.assertEqual(Variant.objects.count(), fixture_count + 12)
        self.assertEqual(CatalogAuditEntry.objects.count(), 12)
        self.assertEqual(StockPosition.objects.count(), 6)
        self.assertEqual(StockMovement.objects.count(), 6)
        self.assertEqual(InventoryMovementAuditEntry.objects.count(), 6)
        for variant in legacy_variants:
            audit = CatalogAuditEntry.objects.get(variant_id=variant.pk)
            owner = variant.sku.removeprefix("FICTIONAL-")
            self.assertEqual(audit.actor_id, str(accounts["fictional-" + owner]))
        for position in StockPosition.objects.all():
            movement = StockMovement.objects.get(position=position)
            audit = InventoryMovementAuditEntry.objects.get(movement_id=movement.pk)
            self.assertEqual((position.on_hand, position.version, movement.sequence, movement.quantity_after), (12, 1, 1, 12))
            self.assertEqual(audit.actor_id, CatalogAuditEntry.objects.get(variant_id=position.variant_id).actor_id)
        print("BROWSER_PERSISTENCE_PASS: 10 creations, 6 positions/movements/audits; lost responses did not duplicate writes", flush=True)

        self.assertEqual(Category.objects.count(), 8)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 18)
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
