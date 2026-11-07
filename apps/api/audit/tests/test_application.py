from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.transaction import TransactionManagementError
from django.test import TestCase, TransactionTestCase

from audit.application import record_catalog_success
from audit.contracts import CatalogAuditAction
from audit.models import CatalogAuditEntry
from identity.contracts import ActorContext


class AuditApplicationTests(TestCase):
    def test_appends_only_the_approved_attribution_fields(self) -> None:
        product_id, variant_id = uuid4(), uuid4()

        with transaction.atomic():
            entry_id = record_catalog_success(
                actor=ActorContext(actor_id="fictional-actor"),
                action=CatalogAuditAction.PRODUCT_CREATED_WITH_INITIAL_VARIANT,
                product_id=product_id,
                variant_id=variant_id,
            )

        entry = CatalogAuditEntry.objects.get(pk=entry_id)
        self.assertIsInstance(entry_id, UUID)
        self.assertEqual(entry.actor_id, "fictional-actor")
        self.assertEqual(entry.action, "catalog.product_created_with_initial_variant")
        self.assertEqual(entry.product_id, product_id)
        self.assertEqual(entry.variant_id, variant_id)
        self.assertEqual(entry.result, "success")
        self.assertIsNotNone(entry.occurred_at.utcoffset())
        self.assertEqual(
            {field.name for field in entry._meta.fields},
            {"id", "actor_id", "action", "product_id", "variant_id", "occurred_at", "result"},
        )

    def test_rejects_unknown_and_unconverted_actions(self) -> None:
        for action in ("catalog.unknown", "catalog.variant_added_to_product", None):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    record_catalog_success(
                        actor=ActorContext(actor_id="fictional-actor"),
                        action=action,
                        product_id=uuid4(),
                        variant_id=uuid4(),
                    )

        self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_rejects_invalid_actor_context_without_inserting(self) -> None:
        for actor in (None, {"actor_id": "fictional-actor"}):
            with self.subTest(actor_type=type(actor).__name__):
                with self.assertRaises(TypeError):
                    record_catalog_success(
                        actor=actor,
                        action=CatalogAuditAction.VARIANT_ADDED_TO_PRODUCT,
                        product_id=uuid4(),
                        variant_id=uuid4(),
                    )

        for actor_id in ("", "   ", None):
            with self.subTest(actor_id=actor_id):
                with self.assertRaises(ValueError):
                    record_catalog_success(
                        actor=ActorContext(actor_id=actor_id),
                        action=CatalogAuditAction.VARIANT_ADDED_TO_PRODUCT,
                        product_id=uuid4(),
                        variant_id=uuid4(),
                    )

        self.assertFalse(CatalogAuditEntry.objects.exists())


class AuditTransactionTests(TransactionTestCase):
    def test_rejects_autocommit_without_opening_a_transaction(self) -> None:
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(connection.in_atomic_block)

        with self.assertRaises(TransactionManagementError):
            record_catalog_success(
                actor=ActorContext(actor_id="fictional-actor"),
                action=CatalogAuditAction.VARIANT_ADDED_TO_PRODUCT,
                product_id=uuid4(),
                variant_id=uuid4(),
            )

        self.assertTrue(connection.get_autocommit())
        self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_participates_in_the_callers_rollback(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "rollback requested"):
            with transaction.atomic(using="default"):
                record_catalog_success(
                    actor=ActorContext(actor_id="fictional-actor"),
                    action=CatalogAuditAction.VARIANT_ADDED_TO_PRODUCT,
                    product_id=uuid4(),
                    variant_id=uuid4(),
                )
                raise RuntimeError("rollback requested")

        self.assertFalse(CatalogAuditEntry.objects.exists())
