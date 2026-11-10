"""Interactive fixtures exist only inside the dedicated launcher's native runner."""
import http.cookiejar
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import unittest
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from django.conf import settings
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.test import LiveServerTestCase, override_settings


def launcher_module():
    name = "mira_interactive_launcher"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[4] / "scripts/engineering/run_interactive_demo.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


demo = launcher_module()


def create_demo_account(runtime):
    demo.verify_runner_database()
    password = secrets.token_urlsafe(32)
    runtime.secrets.append(password)
    user = get_user_model()(username="fictional-demo-" + secrets.token_hex(8), is_active=True, is_staff=False, is_superuser=False)
    validate_password(password, user=user)
    user.set_password(password)
    user.save()
    permissions = []
    for permission in demo.PERMISSIONS:
        app, code = permission.split(".")
        permissions.append(Permission.objects.get(content_type__app_label=app, codename=code))
    user.user_permissions.set(permissions)
    demo.require(set(user.get_all_permissions()) == set(demo.PERMISSIONS), "Unexpected demonstration permissions")
    demo.write_private(runtime.directory / "access.json", {"username": user.username, "password": password}, exclusive=True)
    return user


def require_empty_demo_database():
    """Refuse unknown data before native fixture setup can clean it on failure."""
    demo.verify_runner_database()
    for model in apps.get_models():
        if model._meta.app_label in {"catalog", "inventory", "audit", "identity", "auth", "sessions"} and model._meta.label != "auth.Permission":
            demo.require(not model._default_manager.exists(), "Unexpected test data; stop for human review before fixture setup")


def verify_http_login(runtime, origin):
    """Short-lived verification only; no password is passed to Node or stdout."""
    credentials = demo.read_state(runtime.directory / "access.json")
    client = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    prefix = origin + "/api/v1/operator"
    with client.open(prefix + "/auth/csrf", timeout=5) as response:
        before = json.load(response)["csrf_token"]
    login = Request(prefix + "/auth/login", data=urlencode(credentials).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": origin, "X-CSRFToken": before})
    with client.open(login, timeout=5) as response:
        after = json.load(response)["csrf_token"]
    with client.open(prefix + "/auth/session", timeout=5) as response:
        session = json.load(response)
    demo.require(session["authenticated"] is True and len(session["capabilities"]) == 9, "Native HTTP session verification failed")
    logout = Request(prefix + "/auth/logout", data=b"", headers={"Origin": origin, "X-CSRFToken": after})
    with client.open(logout, timeout=5) as response:
        demo.require(response.status == 200, "Logout verification failed")
    with client.open(prefix + "/auth/session", timeout=5) as response:
        demo.require(json.load(response)["authenticated"] is False, "Session invalidation failed")
    # Leave a second authenticated session for the native teardown to invalidate.
    with client.open(prefix + "/auth/csrf", timeout=5) as response:
        token = json.load(response)["csrf_token"]
    again = Request(prefix + "/auth/login", data=urlencode(credentials).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": origin, "X-CSRFToken": token})
    with client.open(again, timeout=5) as response:
        demo.require(response.status == 200, "Second verification login failed")
    demo.require(Session.objects.count() == 1, "Expected an active session before native teardown")
    demo.write_private(runtime.directory / "http-verification.json", {"login": True, "capabilities": 9, "logout": True,
                       "active_session_before_native_teardown": True}, exclusive=True)


@unittest.skipUnless(demo.ACTIVE_SESSION is not None and os.environ.get("MIRA_RUN_INTERACTIVE_DEMO") == "1"
                     and not os.environ.get("CI") and not os.environ.get("GITHUB_ACTIONS"),
                     "Interactive mode requires the dedicated launcher; never enabled by normal test discovery")
class InteractiveOperatorDemoTests(LiveServerTestCase):
    host = "localhost"
    _demo_thread_stopped = False

    @classmethod
    def setUpClass(cls):
        demo.require(demo.ACTIVE_SESSION is not None, "Launcher-controlled runtime required")
        demo.verify_runner_database()  # Before the native backend listener starts.
        require_empty_demo_database()
        super().setUpClass()
        demo.require(ipaddress.ip_address(cls.server_thread.httpd.server_address[0]).is_loopback, "Backend listener is not loopback")
        demo.ACTIVE_SESSION.backend_port = cls.server_thread.port

    @classmethod
    def _terminate_thread(cls):
        if not cls._demo_thread_stopped:
            super()._terminate_thread()
            cls._demo_thread_stopped = True

    def test_interactive_session(self):
        runtime = demo.ACTIVE_SESSION
        demo.verify_runner_database()
        self.assertIsNotNone(runtime)
        self.account = None
        server = None
        record = None
        snapshot = demo.frontend_snapshot()
        try:
            if runtime.stop.is_set():
                return
            self.account = create_demo_account(runtime)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            runtime.frontend_port = port
            origin = f"http://localhost:{port}"
            env = demo.frontend_environment(self.live_server_url.replace("localhost", "127.0.0.1"))
            pnpm = shutil.which("pnpm")
            demo.require(pnpm is not None, "Locked pnpm is required; no automatic installation")
            with override_settings(OPERATOR_PUBLIC_ORIGIN=origin, CSRF_TRUSTED_ORIGINS=[origin],
                                   SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False):
                demo.owned_command(runtime, [pnpm, "build"], cwd=demo.ROOT / "apps/web", env=env)
                demo.require(snapshot == demo.frontend_snapshot(), "Tracked frontend drift after build; no restoration authorized")
                next_log = runtime.directory / "next.log"
                with next_log.open("x") as log:
                    next_log.chmod(0o600)
                    server = subprocess.Popen([pnpm, "exec", "next", "start", "--hostname", "127.0.0.1", "--port", str(port)],
                                              cwd=demo.ROOT / "apps/web", env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    record = demo.record_process(server)
                    demo.write_private(runtime.directory / "frontend-process.json", record, exclusive=True)
                    startup = time.monotonic() + 30
                    while time.monotonic() < startup:
                        demo.require(server.poll() is None, "Frontend stopped during startup")
                        if runtime.should_stop(startup):
                            break
                        try:
                            with urlopen(origin + "/login", timeout=1) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            time.sleep(.1)
                    else:
                        raise demo.DemoError("Frontend readiness timed out")
                    if runtime.stop.is_set():
                        return
                    # Verify both the isolated DB and same-origin API before READY.
                    demo.verify_runner_database()
                    with urlopen(origin + "/api/v1/operator/auth/csrf", timeout=3) as response:
                        demo.require(isinstance(json.load(response).get("csrf_token"), str), "Backend unavailable through same-origin rewrite")
                    deadline = runtime.ready(port, self.server_thread.port)
                    if runtime.config["verification"]:
                        verify_http_login(runtime, origin)
                    while not runtime.should_stop(deadline):
                        demo.require(server.poll() is None, "Frontend terminated unexpectedly")
                        demo.require(snapshot == demo.frontend_snapshot(), "Tracked frontend drift during demonstration")
                        runtime.stop.wait(.25)
        finally:
            errors = []
            try:
                if record is not None:
                    demo.terminate_owned(record)
                if server is not None:
                    server.wait(timeout=5)
                    demo.write_private(runtime.directory / "frontend-stopped.json", {"stopped": True}, exclusive=True)
            except Exception:
                errors.append("frontend_cleanup")
            try:
                self._terminate_thread()  # Native listener stops before fixture cleanup.
            except Exception:
                errors.append("backend_cleanup")
            try:
                demo.remove_credentials(runtime.directory)
            except Exception:
                errors.append("credential_cleanup")
            ports = all(demo.wait_port_closed(port) for port in (runtime.frontend_port, runtime.backend_port) if port is not None)
            runtime.servers_stopped = not errors and ports
            demo.require(runtime.servers_stopped, "Server teardown incomplete")
            demo.require(snapshot == demo.frontend_snapshot(), "Tracked frontend changed; preserve for review")

    def _post_teardown(self):
        super()._post_teardown()  # Native test fixture cleanup only; no manual reset.
        runtime = demo.ACTIVE_SESSION
        demo.verify_runner_database()
        if getattr(self, "account", None) is not None:
            self.assertFalse(get_user_model().objects.filter(pk=self.account.pk).exists())
        self.assertFalse(Session.objects.exists())
        runtime.fixture_cleanup_verified = True
