from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase
from django.urls import URLResolver, get_resolver, resolve


class IdentitySurfaceTests(SimpleTestCase):
    def test_only_existing_health_routes_are_exposed(self) -> None:
        def leaf_routes(resolver, prefix=""):
            routes = []
            for pattern in resolver.url_patterns:
                route = prefix + str(pattern.pattern)
                if isinstance(pattern, URLResolver):
                    routes.extend(leaf_routes(pattern, route))
                else:
                    routes.append(route)
            return routes

        self.assertCountEqual(
            leaf_routes(get_resolver()),
            ["api/v1/health/live/", "api/v1/health/ready/"],
        )
        self.assertEqual(resolve("/api/v1/health/live/").url_name, "health-live")
        self.assertEqual(resolve("/api/v1/health/ready/").url_name, "health-ready")

    def test_keeps_native_user_and_authentication_backend(self) -> None:
        self.assertEqual(settings.AUTH_USER_MODEL, "auth.User")
        self.assertEqual(get_user_model()._meta.label, "auth.User")
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS, ["django.contrib.auth.backends.ModelBackend"]
        )

    def test_identity_has_no_models_or_migrations(self) -> None:
        self.assertEqual(list(apps.get_app_config("identity").get_models()), [])
        migrations = MigrationLoader(None).disk_migrations
        self.assertFalse(any(app_label == "identity" for app_label, name in migrations))


class IdentityDatabaseIsolationTests(TestCase):
    def test_uses_postgresql_and_the_isolated_test_database(self) -> None:
        self.assertEqual(settings.SETTINGS_MODULE, "config.settings.test")
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(connection.settings_dict["TEST"]["NAME"], "test_mira")
        self.assertFalse(connection.settings_dict["TEST"].get("MIRROR"))
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
