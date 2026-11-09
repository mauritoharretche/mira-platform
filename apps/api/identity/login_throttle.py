from datetime import timedelta
from unicodedata import normalize

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import LoginAttemptWindow


class LoginThrottled(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


def reserve_login_attempt(identifier: str) -> None:
    """Commit budgets before password authentication, serializing every reservation.

    The global row is always locked first. First-row races recover only from its
    PK conflict inside a savepoint, never by retrying authentication or a command.
    No raw identifier, credential, IP, or forwarding header is persisted.
    """
    now = timezone.now()
    account_key = salted_hmac("mira.operator.login.account", normalize("NFKC", identifier), algorithm="sha256").hexdigest()
    global_key = salted_hmac("mira.operator.login.global", "global", algorithm="sha256").hexdigest()
    throttled_until = None
    with transaction.atomic(using="default"):
        windows = []
        for key, seconds, limit in ((global_key, 60, 30), (account_key, 900, 5)):
            window = LoginAttemptWindow.objects.select_for_update().filter(pk=key).first()
            if window is None:
                try:
                    with transaction.atomic(using="default"):
                        window = LoginAttemptWindow.objects.create(key=key, attempts=1, expires_at=now + timedelta(seconds=seconds))
                    windows.append((window, True, limit))
                    continue
                except IntegrityError as error:
                    if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) != "identity_loginattemptwindow_pkey":
                        raise
                    window = LoginAttemptWindow.objects.select_for_update().get(pk=key)
            if window.expires_at <= now:
                window.attempts = 0
                window.expires_at = now + timedelta(seconds=seconds)
            windows.append((window, False, limit))
            if window.attempts >= limit:
                throttled_until = max(throttled_until or now, window.expires_at)
                if key == global_key:
                    break

        # Count the global attempt even when the identifier budget is exhausted.
        # Do not roll back the reservation by raising inside the atomic block.
        for window, created, limit in windows:
            if not created and window.attempts < limit:
                window.attempts += 1
                window.save(update_fields=["attempts", "expires_at"])
        expired = list(LoginAttemptWindow.objects.filter(expires_at__lt=now - timedelta(days=1)).exclude(pk=global_key).values_list("pk", flat=True)[:100])
        if expired:
            LoginAttemptWindow.objects.filter(pk__in=expired, expires_at__lt=now - timedelta(days=1)).delete()
    if throttled_until is not None:
        raise LoginThrottled(max(1, int((throttled_until - now).total_seconds()) + 1))
