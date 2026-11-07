from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from audit.models import CatalogAuditEntry


class AuditDatabaseConstraintTests(TransactionTestCase):
    def entry_values(self, **overrides):
        return {
            "actor_id": "fictional-actor",
            "action": "catalog.product_created_with_initial_variant",
            "product_id": uuid4(),
            "variant_id": uuid4(),
            "result": "success",
            **overrides,
        }

    def test_postgresql_rejects_unknown_actions_without_application_validation(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            CatalogAuditEntry.objects.create(**self.entry_values(action="catalog.unknown"))

        self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_postgresql_rejects_results_other_than_success(self) -> None:
        for result in ("failed", "denied", "", None):
            with self.subTest(result=result):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    CatalogAuditEntry.objects.create(**self.entry_values(result=result))

        self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_postgresql_enforces_required_references(self) -> None:
        for field in ("actor_id", "action", "product_id", "variant_id"):
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    CatalogAuditEntry.objects.create(**self.entry_values(**{field: None}))

        self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_uses_the_isolated_postgresql_database(self) -> None:
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
