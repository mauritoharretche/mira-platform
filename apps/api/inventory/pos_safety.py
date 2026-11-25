"""POS-01 cannot consume stock outside the isolated PostgreSQL test destination."""
import os

from django.conf import settings
from django.db import connection
from identity.contracts import AccessDenied


def require_test_sale_context():
    d = connection.settings_dict
    configured = settings.DATABASES.get("default", {})
    active = connection.connection
    # Django 6.1 UserSettingsHolder (including LiveServerTestCase) reports
    # SETTINGS_MODULE=None. Match the existing native guard's source check,
    # independently of its configured/effective destination and SQL identity.
    if (os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings.test" or set(settings.DATABASES) != {"default"}
            or not settings.OPERATOR_HTTP_ENABLED or settings.DATABASE_ROUTERS
            or any(x.get("NAME") != "test_mira" or x.get("ENGINE") != "django.db.backends.postgresql"
                   or x.get("HOST") not in {"localhost", "127.0.0.1", "::1"} or str(x.get("PORT")) not in {"5432", "55432"}
                   or x.get("TEST", {}).get("NAME") != "test_mira" or x.get("TEST", {}).get("MIRROR")
                   or {"dbname", "database", "service"}.intersection(x.get("OPTIONS", {})) for x in (d, configured))
            or (active is not None and active.info.dbname != "test_mira")):
        raise AccessDenied("POS-01 stock commitment is isolated-test-only")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        if cursor.fetchone() != ("test_mira",): raise AccessDenied("Unexpected POS destination")


def isolated_sale(function):
    from functools import wraps
    @wraps(function)
    def guarded(*args, **kwargs):
        require_test_sale_context()
        return function(*args, **kwargs)
    return guarded
