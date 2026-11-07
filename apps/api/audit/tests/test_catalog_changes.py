from io import StringIO
from itertools import product
from uuid import uuid4

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.transaction import TransactionManagementError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from audit.application import (
    record_category_created, record_product_active_changed, record_product_category_changed,
)
from audit.contracts import CatalogChangeAuditAction
from audit.models import CatalogChangeAuditEntry
from identity.contracts import ActorContext


class CatalogChangeAuditContractTests(TestCase):
    def test_typed_contracts_store_only_their_permitted_fields(self):
        actor = ActorContext(actor_id="fictional-actor")
        category_id, product_id = uuid4(), uuid4()
        created_id = record_category_created(actor=actor, category_id=category_id)
        assigned_id = record_product_category_changed(
            actor=actor, product_id=product_id, previous_category_id=None, new_category_id=category_id
        )
        active_id = record_product_active_changed(
            actor=actor, product_id=product_id, previous_is_active=False, new_is_active=True
        )
        created = CatalogChangeAuditEntry.objects.get(pk=created_id)
        assigned = CatalogChangeAuditEntry.objects.get(pk=assigned_id)
        active = CatalogChangeAuditEntry.objects.get(pk=active_id)
        self.assertEqual(created.action, "catalog.category_created")
        self.assertEqual(created.target_id, category_id)
        self.assertEqual(assigned.target_id, product_id)
        self.assertIsNone(assigned.previous_category_id)
        self.assertEqual(assigned.new_category_id, category_id)
        self.assertEqual(active.action, "catalog.product_active_changed")
        self.assertFalse(active.previous_is_active)
        self.assertTrue(active.new_is_active)
        self.assertIsNone(active.previous_category_id)
        self.assertIsNone(active.new_category_id)
        for entry in (created, assigned, active):
            self.assertEqual(entry.actor_id, actor.actor_id)
            self.assertEqual(entry.result, "success")
            self.assertIsNotNone(entry.occurred_at.utcoffset())
        self.assertEqual(
            {field.name for field in created._meta.fields},
            {"id", "actor_id", "action", "target_id", "previous_category_id", "new_category_id",
             "previous_is_active", "new_is_active", "occurred_at", "result"},
        )

    def test_contracts_reject_noops_and_invalid_boolean_states(self):
        actor, product_id, category_id = ActorContext(actor_id="fictional-actor"), uuid4(), uuid4()
        for category in (None, category_id):
            with self.assertRaises(ValueError):
                record_product_category_changed(
                    actor=actor, product_id=product_id, previous_category_id=category, new_category_id=category
                )
        for before, after in ((False, False), (True, True), (None, True), (False, None), (0, True)):
            with self.subTest(before=before, after=after):
                with self.assertRaises(ValueError):
                    record_product_active_changed(
                        actor=actor, product_id=product_id, previous_is_active=before, new_is_active=after
                    )
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_new_contracts_validate_actor_shape_without_authenticating_it(self):
        for actor, error in ((None, TypeError), ({"actor_id": "fake"}, TypeError), (ActorContext(actor_id=" "), ValueError)):
            with self.subTest(actor_type=type(actor).__name__):
                with self.assertRaises(error):
                    record_category_created(actor=actor, category_id=uuid4())
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())


class CatalogChangeAuditDatabaseTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")

    def values(self, action="catalog.category_created", **overrides):
        return {
            "actor_id": "fictional-actor", "action": action, "target_id": uuid4(),
            "previous_category_id": None, "new_category_id": None,
            "previous_is_active": None, "new_is_active": None, "result": "success",
            **overrides,
        }

    def assert_shape_rejected(self, values, sql=False):
        before = CatalogChangeAuditEntry.objects.count()
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            if sql:
                columns = ["id", "actor_id", "action", "target_id", "previous_category_id",
                           "new_category_id", "previous_is_active", "new_is_active", "occurred_at", "result"]
                data = {"id": uuid4(), "occurred_at": timezone.now(), **values}
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO "audit_catalogchangeauditentry" ('
                        + ", ".join(f'"{column}"' for column in columns)
                        + ") VALUES (" + ", ".join(["%s"] * len(columns)) + ")",
                        [data[column] for column in columns],
                    )
            else:
                # Deliberately bypass application validation and full_clean().
                CatalogChangeAuditEntry.objects.create(**values)
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, "audit_change_shape_valid")
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), before)

    def test_postgresql_exhaustive_null_uuid_and_boolean_shape_matrix(self):
        first, second = uuid4(), uuid4()
        valid_count = 0
        for action in CatalogChangeAuditAction:
            for old_category, new_category, old_active, new_active in product(
                (None, first, second), (None, first, second), (None, False, True), (None, False, True)
            ):
                values = self.values(
                    action.value,
                    previous_category_id=old_category, new_category_id=new_category,
                    previous_is_active=old_active, new_is_active=new_active,
                )
                if action == CatalogChangeAuditAction.CATEGORY_CREATED:
                    valid = all(value is None for value in (old_category, new_category, old_active, new_active))
                elif action == CatalogChangeAuditAction.PRODUCT_CATEGORY_CHANGED:
                    valid = old_active is None and new_active is None and old_category != new_category
                else:
                    valid = (
                        old_category is None and new_category is None
                        and old_active is not None and new_active is not None and old_active != new_active
                    )
                with self.subTest(action=action.value, before_category=old_category, after_category=new_category,
                                  before_active=old_active, after_active=new_active):
                    if valid:
                        CatalogChangeAuditEntry.objects.create(**values)
                        valid_count += 1
                    else:
                        self.assert_shape_rejected(values)

        self.assertEqual(valid_count, 9)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 9)

    def test_parameterized_sql_cannot_bypass_null_or_action_field_guards(self):
        category_id = uuid4()
        invalid = [
            self.values(**{field: value})
            for field, value in (
                ("previous_category_id", category_id), ("new_category_id", category_id),
                ("previous_is_active", False), ("new_is_active", False),
            )
        ]
        invalid.extend([
            self.values("catalog.product_category_changed"),
            self.values("catalog.product_category_changed", previous_category_id=category_id, new_category_id=category_id),
            self.values("catalog.product_category_changed", new_category_id=category_id, previous_is_active=False),
            self.values("catalog.product_category_changed", new_category_id=category_id, new_is_active=False),
        ])
        for before, after in product((None, False, True), repeat=2):
            if before is None or after is None or before == after:
                invalid.append(self.values("catalog.product_active_changed", previous_is_active=before, new_is_active=after))
        for field in ("previous_category_id", "new_category_id"):
            invalid.append(self.values(
                "catalog.product_active_changed", previous_is_active=False, new_is_active=True,
                **{field: category_id},
            ))
        for index, values in enumerate(invalid):
            with self.subTest(case=index):
                self.assert_shape_rejected(values, sql=True)

    def test_postgresql_rejects_unknown_actions_and_invalid_results(self):
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            CatalogChangeAuditEntry.objects.create(**self.values(action="catalog.unknown"))
        self.assertIn(failure.exception.__cause__.diag.constraint_name,
                      {"audit_change_action_valid", "audit_change_shape_valid"})
        for result in ("failed", "denied", ""):
            with self.subTest(result=result):
                with self.assertRaises(IntegrityError) as failure, transaction.atomic():
                    CatalogChangeAuditEntry.objects.create(**self.values(result=result))
                self.assertEqual(failure.exception.__cause__.diag.constraint_name, "audit_change_result_success")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CatalogChangeAuditEntry.objects.create(**self.values(result=None))
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_new_append_contracts_require_the_callers_transaction(self):
        actor, target = ActorContext(actor_id="fictional-actor"), uuid4()
        calls = (
            lambda: record_category_created(actor=actor, category_id=target),
            lambda: record_product_category_changed(actor=actor, product_id=target, previous_category_id=None, new_category_id=uuid4()),
            lambda: record_product_active_changed(actor=actor, product_id=target, previous_is_active=False, new_is_active=True),
        )
        self.assertTrue(connection.get_autocommit())
        for call in calls:
            with self.assertRaises(TransactionManagementError):
                call()
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_generated_sql_and_installed_constraint_have_explicit_null_guards(self):
        output = StringIO()
        call_command("sqlmigrate", "audit", "0002_catalog_change_audit_entry", stdout=output)
        generated = output.getvalue()
        self.assertIn("audit_change_shape_valid", generated)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
                           ["audit_change_shape_valid"])
            installed = cursor.fetchone()[0]
        for sql in (generated, installed):
            for field in ("previous_category_id", "new_category_id", "previous_is_active", "new_is_active"):
                self.assertIn(field, sql)
            self.assertIn("IS NULL", sql)
            self.assertIn("IS NOT NULL", sql)
            for action in CatalogChangeAuditAction:
                self.assertIn(action.value, sql)
