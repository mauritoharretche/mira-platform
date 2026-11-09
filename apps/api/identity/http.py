"""Native Django session adapters. No client-provided principal is accepted."""
from datetime import timedelta

from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.db import DatabaseError
from django.http import JsonResponse
from django.middleware.csrf import get_token, rotate_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from system.http import DEADLINE_KEY, error_response, security_log

from .application import require_capability
from .contracts import AccessDenied, AuthenticationRequired, Capability
from .login_throttle import LoginThrottled, reserve_login_attempt

_SURFACE_CAPABILITIES = (
    Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT, Capability.READ_PRODUCT,
    Capability.RECEIVE_STOCK, Capability.READ_STOCK, Capability.READ_STOCK_MOVEMENTS,
)


@require_GET
def csrf_token(request):
    return JsonResponse({"csrf_token": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class OperatorLoginView(LoginView):
    http_method_names = ["post", "options"]
    authentication_form = AuthenticationForm
    redirect_authenticated_user = False

    def post(self, request, *args, **kwargs):
        if request.content_type != "application/x-www-form-urlencoded":
            return error_response("unsupported_media_type", 415)
        if set(request.POST) != {"username", "password"} or any(len(request.POST.getlist(key)) != 1 for key in request.POST):
            return error_response("validation_error", 400)
        username, password = request.POST["username"], request.POST["password"]
        if not username.strip() or len(username) > 150 or not password or len(password) > 1024:
            return error_response("invalid_credentials", 401)
        try:
            reserve_login_attempt(username.strip())
            return super().post(request, *args, **kwargs)
        except LoginThrottled as exc:
            security_log.info("login_throttled")
            response = error_response("login_throttled", 429)
            response["Retry-After"] = str(exc.retry_after)
            return response
        except DatabaseError:
            security_log.warning("login_store_unavailable")
            return error_response("unavailable", 503)

    def form_valid(self, form):
        login(self.request, form.get_user())
        deadline = timezone.now() + timedelta(hours=8)
        self.request.session[DEADLINE_KEY] = deadline.isoformat()
        self.request.session.set_expiry(deadline)
        security_log.info("login_succeeded")
        return JsonResponse({"authenticated": True, "csrf_token": get_token(self.request)})

    def form_invalid(self, form):
        security_log.info("login_failed")
        return error_response("invalid_credentials", 401)


@require_GET
def session_status(request):
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False, "capabilities": []})
    capabilities = []
    for capability in _SURFACE_CAPABILITIES:
        try:
            require_capability(principal=request.user, capability=capability)
        except (AccessDenied, AuthenticationRequired):
            continue
        capabilities.append(capability.value)
    return JsonResponse({"authenticated": True, "capabilities": capabilities})


@require_POST
@csrf_protect
def session_logout(request):
    logout(request)
    rotate_token(request)
    security_log.info("logout_succeeded")
    return JsonResponse({}, status=200)
