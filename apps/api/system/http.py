"""Transport-only safety shared by operator adapters; no domain model access."""
import logging
from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import DisallowedHost, RequestDataTooBig
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView, exception_handler

PREFIX = "/api/v1/operator/"
DEADLINE_KEY = "_operator_deadline"
security_log = logging.getLogger("mira.operator.security")


def error_response(code: str, status: int, *, fields=None):
    body = {"error": {"code": code}}
    if fields:
        body["error"]["fields"] = fields
    return JsonResponse(body, status=status)


def csrf_failure(request, reason=""):
    security_log.info("csrf_rejected")
    return error_response("csrf_failed", 403)


def _operator_database_is_isolated():
    """Check routing metadata, never connect to a database to establish safety.

    TEST.NAME alone does not select a test database. The native runner must have
    switched the configured and effective default NAME before HTTP is allowed.
    Its separate current_database() assertion remains the live verification.
    """
    configured = settings.DATABASES.get("default", {})
    if configured.get("ENGINE") != "django.db.backends.postgresql":
        return False
    database = connections["default"]  # Wrapper lookup does not open a connection.
    for destination in (configured, database.settings_dict):
        test = destination.get("TEST", {})
        if (destination.get("ENGINE") != "django.db.backends.postgresql"
                or destination.get("NAME") != "test_mira"
                or test.get("NAME") != "test_mira" or test.get("MIRROR")
                or {"dbname", "database", "service"}.intersection(destination.get("OPTIONS", {}))):
            return False
    # If a connection already exists, inspect only libpq's cached identity.
    # Changing NAME must not make an older connection appear safe.
    active = database.connection
    return active is None or getattr(getattr(active, "info", None), "dbname", None) == "test_mira"


class OperatorResponseMiddleware:
    """Fence the DB before session middleware; sanitize operator responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        operator = request.path.startswith(PREFIX)
        if operator and not settings.OPERATOR_HTTP_ENABLED:
            response = error_response("not_found", 404)
        elif operator and not _operator_database_is_isolated():
            response = error_response("unavailable", 503)
        else:
            response = self.get_response(request)
        if operator:
            if response.status_code >= 500:
                response = error_response("unavailable", 503)
            elif response.status_code >= 400 and not response.get("Content-Type", "").startswith("application/json"):
                original = response
                code = "not_found" if response.status_code == 404 else "method_not_allowed" if response.status_code == 405 else "invalid_request"
                response = error_response(code, original.status_code)
                for header in ("Allow", "Retry-After"):
                    if header in original:
                        response[header] = original[header]
            response["Cache-Control"] = "no-store"
            response["X-Content-Type-Options"] = "nosniff"
            response["X-Frame-Options"] = "DENY"
            response["Referrer-Policy"] = "same-origin"
        return response


def _deadline(value):
    try:
        deadline = datetime.fromisoformat(value)
        return deadline if timezone.is_aware(deadline) else None
    except (TypeError, ValueError):
        return None


class OperatorBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(PREFIX):
            return self.get_response(request)
        response = None
        original = None
        try:
            origin = urlsplit(settings.OPERATOR_PUBLIC_ORIGIN)
            remote = ip_address(request.META.get("REMOTE_ADDR", ""))
            valid_origin = (origin.scheme in {"http", "https"} and origin.hostname in {"localhost", "127.0.0.1", "::1"}
                            and not origin.username and not origin.password and not origin.query and not origin.fragment
                            and origin.path in {"", "/"})
            if not settings.OPERATOR_HTTP_ENABLED or not remote.is_loopback or not valid_origin:
                response = error_response("not_found", 404)
            elif len(request.body) > 16 * 1024:
                response = error_response("request_too_large", 413)
            else:
                request.get_host()  # validate Host even on safe methods
                original = _deadline(request.session.get(DEADLINE_KEY))
                if request.user.is_authenticated and (original is None or original <= timezone.now()):
                    logout(request)
                    original = None
                response = self.get_response(request)
                if original and request.user.is_authenticated and request.path != PREFIX + "auth/login":
                    # A later view or middleware cannot renew the operator deadline.
                    if request.session.get(DEADLINE_KEY) != original.isoformat() or request.session.get_expiry_date() != original:
                        request.session[DEADLINE_KEY] = original.isoformat()
                        request.session.set_expiry(original)
        except RequestDataTooBig:
            response = error_response("request_too_large", 413)
        except DisallowedHost:
            response = error_response("invalid_request", 400)
        except DatabaseError:
            security_log.warning("operator_store_unavailable")
            response = error_response("unavailable", 503)
        except ValueError:
            response = error_response("not_found", 404)
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "same-origin"
        return response


class StrictTextField(serializers.CharField):
    def to_internal_value(self, data):
        if type(data) is not str:
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Unexpected request fields."]})
        return super().to_internal_value(data)


class OperatorAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    http_method_names = ["get", "post", "options"]

    def handle_exception(self, exc):
        if isinstance(exc, (NotAuthenticated, PermissionDenied, PermissionError)):
            code = "access_denied" if self.request.user.is_authenticated else "authentication_required"
            if isinstance(exc, PermissionDenied) and str(exc.detail).startswith("CSRF Failed:"):
                code = "csrf_failed"
            return error_response(code, 403)
        response = exception_handler(exc, self.get_exception_handler_context())
        if response is not None:
            fields = response.data if isinstance(exc, serializers.ValidationError) else None
            code = "validation_error" if fields else ("unsupported_media_type" if response.status_code == 415 else "invalid_request")
            return error_response(code, response.status_code, fields=fields)
        security_log.warning("operator_request_failed")
        return error_response("unavailable", 503 if isinstance(exc, DatabaseError) else 500)
