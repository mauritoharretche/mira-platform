from datetime import timedelta
from urllib.parse import urlencode
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.http import JsonResponse
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from inventory.tests.test_application import assert_test_database
from system.http import DEADLINE_KEY, OperatorBoundaryMiddleware

PASSWORD = "fictional-demo-password-for-tests-only"
PREFIX = "/api/v1/operator"
PERMISSIONS = ("catalog.add_product", "catalog.add_variant", "catalog.view_product", "catalog.view_variant",
               "inventory.receive_stock", "inventory.view_stockposition", "inventory.view_stockmovement")


def make_operator(*, username="fictional-browser-operator", permissions=PERMISSIONS):
    user = get_user_model().objects.create_user(username=username, password=PASSWORD)
    for permission in permissions:
        app, codename = permission.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
    return user


def operator_client():
    return Client(enforce_csrf_checks=True, HTTP_HOST="localhost", HTTP_ORIGIN="http://localhost:3000")


def csrf(client):
    return client.get(PREFIX + "/auth/csrf", secure=True).json()["csrf_token"]


def sign_in(client, username="fictional-browser-operator", password=PASSWORD):
    return client.post(PREFIX + "/auth/login", urlencode({"username": username, "password": password}),
                       content_type="application/x-www-form-urlencoded", secure=True, HTTP_X_CSRFTOKEN=csrf(client))


class OperatorAuthenticationTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user = make_operator()
        self.client = operator_client()

    def test_native_login_rotates_session_and_csrf_and_uses_secure_scoped_cookies(self):
        old_token = csrf(self.client)
        old_cookie = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        response = sign_in(self.client)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.client.cookies[settings.CSRF_COOKIE_NAME].value, old_cookie)
        for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            cookie = self.client.cookies[name]
            self.assertTrue(cookie["secure"])
            self.assertTrue(cookie["httponly"])
            self.assertEqual(cookie["samesite"], "Lax")
            self.assertEqual(cookie["path"], PREFIX + "/")
            self.assertFalse(cookie["domain"])
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        denied = self.client.post(PREFIX + "/auth/logout", secure=True, HTTP_X_CSRFTOKEN=old_token)
        self.assertEqual(denied.status_code, 403)
        state = self.client.get(PREFIX + "/auth/session", secure=True).json()
        self.assertTrue(state["authenticated"])
        self.assertCountEqual(state["capabilities"], ["catalog.create_product_with_initial_variant", "catalog.read_product",
                              "inventory.receive_stock", "inventory.read_stock", "inventory.read_stock_movements"])

    def test_absolute_expiry_is_eight_hours_and_reads_cannot_extend_it(self):
        now = timezone.now()
        with patch("identity.http.timezone.now", return_value=now):
            self.assertEqual(sign_in(self.client).status_code, 200)
        deadline = self.client.session[DEADLINE_KEY]
        self.assertEqual(deadline, (now + timedelta(hours=8)).isoformat())
        key = self.client.session.session_key
        before = Session.objects.get(pk=key).expire_date
        with patch("system.http.timezone.now", return_value=now + timedelta(hours=2)):
            self.client.get(PREFIX + "/auth/session", secure=True)
        self.assertEqual(self.client.session[DEADLINE_KEY], deadline)
        self.assertEqual(Session.objects.get(pk=key).expire_date, before)

    def test_session_mutation_cannot_extend_original_deadline(self):
        sign_in(self.client)
        deadline = self.client.session[DEADLINE_KEY]
        from django.test import RequestFactory
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.contrib.auth.middleware import AuthenticationMiddleware
        request = RequestFactory().get(PREFIX + "/auth/session", REMOTE_ADDR="127.0.0.1")
        request.COOKIES = {settings.SESSION_COOKIE_NAME: self.client.session.session_key}
        SessionMiddleware(lambda r: None).process_request(request)
        AuthenticationMiddleware(lambda r: None).process_request(request)
        def mutate(request):
            request.session[DEADLINE_KEY] = (timezone.now() + timedelta(days=20)).isoformat()
            request.session.set_expiry(60 * 60 * 24 * 20)
            request.session["fictional_mutation"] = True
            return JsonResponse({})
        OperatorBoundaryMiddleware(mutate)(request)
        self.assertEqual(request.session[DEADLINE_KEY], deadline)
        self.assertEqual(request.session.get_expiry_date().isoformat(), deadline)

    def test_expired_session_is_unusable_and_logout_is_csrf_protected(self):
        login = sign_in(self.client)
        key = self.client.session.session_key
        with patch("system.http.timezone.now", return_value=timezone.now() + timedelta(hours=9)):
            self.assertFalse(self.client.get(PREFIX + "/auth/session", secure=True).json()["authenticated"])
        # Django may retain an expired row until normal session maintenance. It
        # is no longer loadable and must not acquire a renewed expiry.
        self.assertLessEqual(Session.objects.get(pk=key).expire_date, timezone.now() + timedelta(hours=9))
        login = sign_in(self.client)
        logout_key = self.client.session.session_key
        self.assertEqual(self.client.post(PREFIX + "/auth/logout", secure=True).status_code, 403)
        response = self.client.post(PREFIX + "/auth/logout", secure=True, HTTP_X_CSRFTOKEN=login.json()["csrf_token"])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Session.objects.filter(pk=logout_key).exists())
        self.assertFalse(self.client.get(PREFIX + "/auth/session", secure=True).json()["authenticated"])

    def test_bad_unknown_and_inactive_users_have_identical_generic_failure(self):
        outcomes = []
        for username, password in ((self.user.username, "incorrect"), ("fictional-absent", "incorrect")):
            response = sign_in(self.client, username, password)
            outcomes.append((response.status_code, response.json()))
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = sign_in(self.client, self.user.username)
        outcomes.append((response.status_code, response.json()))
        self.assertEqual(outcomes, [(401, {"error": {"code": "invalid_credentials"}})] * 3)

    def test_login_anonymous_csrf_foreign_origin_and_payload_authority_are_rejected(self):
        payload = urlencode({"username": self.user.username, "password": PASSWORD})
        self.assertEqual(self.client.post(PREFIX + "/auth/login", payload, content_type="application/x-www-form-urlencoded", secure=True).status_code, 403)
        token = csrf(self.client)
        response = self.client.post(PREFIX + "/auth/login", payload, content_type="application/x-www-form-urlencoded", secure=True,
                                    HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://untrusted.example")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        response = self.client.post(PREFIX + "/auth/login", payload + "&principal=1", content_type="application/x-www-form-urlencoded", secure=True, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 400)

    def test_permission_revocation_and_group_grants_are_fresh(self):
        sign_in(self.client)
        self.user.user_permissions.clear()
        self.assertEqual(self.client.get(PREFIX + "/auth/session", secure=True).json()["capabilities"], [])
        group = Group.objects.create(name="Fictional browser permission group")
        group.permissions.add(Permission.objects.get(content_type__app_label="inventory", codename="view_stockposition"))
        self.user.groups.add(group)
        self.assertEqual(self.client.get(PREFIX + "/auth/session", secure=True).json()["capabilities"], ["inventory.read_stock"])
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertFalse(self.client.get(PREFIX + "/auth/session", secure=True).json()["authenticated"])

    def test_native_non_operator_session_does_not_establish_operator_provenance(self):
        self.client.force_login(self.user)
        self.assertFalse(self.client.get(PREFIX + "/auth/session", secure=True).json()["authenticated"])

    def test_throttle_occurs_before_authentication_and_store_failure_is_safe(self):
        with patch("identity.http.reserve_login_attempt", side_effect=DatabaseError("fictional private detail")), patch("django.contrib.auth.forms.authenticate") as authenticate:
            response = sign_in(self.client)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": {"code": "unavailable"}})
        authenticate.assert_not_called()

    def test_valid_login_does_not_assign_business_permissions_or_require_staff(self):
        self.user.user_permissions.clear()
        self.assertFalse(self.user.is_staff)
        self.assertEqual(sign_in(self.client).status_code, 200)
        self.assertEqual(self.client.get(PREFIX + "/auth/session", secure=True).json()["capabilities"], [])
