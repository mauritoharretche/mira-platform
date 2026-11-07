from contextlib import ExitStack
from queue import Queue
from threading import Event, Thread
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from audit import application as audit_application
from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry
from catalog.application import (
    CatalogValidationError, ProductNotFoundError, create_category,
    create_product_with_initial_variant, get_product, set_product_active, set_product_category,
)
from catalog.models import Category, Product, Variant


class ProductActivationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(
            username="fictional-activation-operator", password=cls.password
        )
        cls.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog"))
        cls.category = Category.objects.create(name="First", name_key="first")
        cls.product = Product.objects.create(name="Fictional product")
        cls.variant = Variant.objects.create(product=cls.product, label="Variant", sku="ABC-01", barcode="00123")

    def setUp(self):
        self.principal = authenticate(username=self.user.username, password=self.password)
        self.assertIsNotNone(self.principal)

    def test_existing_create_contract_creates_inactive_uncategorized_product(self):
        product = create_product_with_initial_variant(
            principal=self.principal, product_name="New fictional product",
            variant_label="New variant", sku="NEW-001",
        )
        self.assertFalse(product.is_active)
        self.assertIsNone(product.category_id)
        self.assertEqual(CatalogAuditEntry.objects.count(), 1)
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())
        with self.assertRaises(TypeError):
            create_product_with_initial_variant(
                principal=self.principal, product_name="No bypass", variant_label="Variant",
                sku="NEW-002", is_active=True,
            )

    def test_activation_requires_category_and_strict_boolean(self):
        with self.assertRaises(CatalogValidationError):
            set_product_active(principal=self.principal, product_id=self.product.id, is_active=True)
        for value in (None, 0, 1, "false", "true"):
            with self.subTest(value=value):
                with self.assertRaises(CatalogValidationError):
                    set_product_active(principal=self.principal, product_id=self.product.id, is_active=value)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_activation_and_deactivation_record_exact_boolean_states_without_changing_variants(self):
        set_product_category(principal=self.principal, product_id=self.product.id, category_id=self.category.id)
        before = Variant.objects.values().get(pk=self.variant.pk)
        for value in (True, False):
            product = set_product_active(principal=self.principal, product_id=self.product.id, is_active=value)
            entry = CatalogChangeAuditEntry.objects.latest("occurred_at")
            self.assertEqual(product.is_active, value)
            self.assertEqual(entry.action, "catalog.product_active_changed")
            self.assertEqual(entry.actor_id, str(self.user.pk))
            self.assertEqual(entry.target_id, self.product.pk)
            self.assertEqual(entry.previous_is_active, not value)
            self.assertEqual(entry.new_is_active, value)
            self.assertIsNone(entry.previous_category_id)
            self.assertIsNone(entry.new_category_id)
        self.assertEqual(Variant.objects.values().get(pk=self.variant.pk), before)

    def test_active_product_can_be_reassigned_but_not_uncategorized(self):
        second = Category.objects.create(name="Second", name_key="second")
        set_product_category(principal=self.principal, product_id=self.product.id, category_id=self.category.id)
        set_product_active(principal=self.principal, product_id=self.product.id, is_active=True)
        set_product_category(principal=self.principal, product_id=self.product.id, category_id=second.id)
        before = CatalogChangeAuditEntry.objects.count()
        with self.assertRaises(CatalogValidationError):
            set_product_category(principal=self.principal, product_id=self.product.id, category_id=None)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)
        self.assertEqual(self.product.category_id, second.id)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), before)

    def test_active_state_noop_preserves_timestamp_and_audit(self):
        for value in (False, True):
            if value:
                set_product_category(principal=self.principal, product_id=self.product.id, category_id=self.category.id)
                set_product_active(principal=self.principal, product_id=self.product.id, is_active=True)
            self.product.refresh_from_db()
            before = self.product.updated_at
            count = CatalogChangeAuditEntry.objects.count()
            set_product_active(principal=self.principal, product_id=self.product.id, is_active=value)
            self.product.refresh_from_db()
            self.assertEqual(self.product.updated_at, before)
            self.assertEqual(CatalogChangeAuditEntry.objects.count(), count)

    def test_admin_query_keeps_inactive_products_and_creates_no_change_audit(self):
        product = get_product(principal=self.principal, product_id=self.product.id)
        self.assertFalse(product.is_active)
        self.assertEqual(list(product.variants.all()), [self.variant])
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_missing_product_does_not_create_success(self):
        with self.assertRaises(ProductNotFoundError):
            set_product_active(principal=self.principal, product_id=uuid4(), is_active=False)
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())


class CommercialTransactionTests(TransactionTestCase):
    def setUp(self):
        password = "fictional-test-password-only"
        self.user = get_user_model().objects.create_user(username="fictional-transaction-operator", password=password)
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog"))
        self.principal = authenticate(username=self.user.username, password=password)
        self.assertIsNotNone(self.principal)
        self.first = Category.objects.create(name="First", name_key="first")
        self.second = Category.objects.create(name="Second", name_key="second")
        self.third = Category.objects.create(name="Third", name_key="third")
        self.product = Product.objects.create(name="Fictional product", category=self.first)

    def invoke(self, operation):
        if operation == "category":
            return create_category(principal=self.principal, name="Created category")
        if operation == "assignment":
            return set_product_category(principal=self.principal, product_id=self.product.id, category_id=self.second.id)
        return set_product_active(principal=self.principal, product_id=self.product.id, is_active=True)

    def assert_fixture_only(self):
        self.product.refresh_from_db()
        self.assertEqual(self.product.category_id, self.first.id)
        self.assertFalse(self.product.is_active)
        self.assertEqual(Category.objects.count(), 3)
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_audit_failures_before_and_after_insertion_roll_back_each_write(self):
        methods = ("record_category_created", "record_product_category_changed", "record_product_active_changed")
        for operation in ("category", "assignment", "activation"):
            for after_insert in (False, True):
                with self.subTest(operation=operation, after_insert=after_insert):
                    with ExitStack() as stack:
                        for name in methods:
                            original = getattr(audit_application, name)

                            def fail(original=original, **kwargs):
                                if after_insert:
                                    entry_id = original(**kwargs)
                                    self.assertTrue(CatalogChangeAuditEntry.objects.filter(pk=entry_id).exists())
                                raise DatabaseError("fictional audit failure")

                            stack.enter_context(patch.object(audit_application, name, side_effect=fail))
                        with self.assertRaises(DatabaseError):
                            self.invoke(operation)
                    self.assert_fixture_only()

    def test_outer_rollback_removes_each_change_and_its_audit(self):
        for operation in ("category", "assignment", "activation"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(RuntimeError, "rollback requested"):
                    with transaction.atomic():
                        self.invoke(operation)
                        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 1)
                        raise RuntimeError("rollback requested")
                self.assert_fixture_only()

    def test_success_commits_each_change_and_audit(self):
        self.assertTrue(connection.get_autocommit())
        for operation in ("category", "assignment", "activation"):
            self.invoke(operation)
        self.product.refresh_from_db()
        self.assertEqual(self.product.category_id, self.second.id)
        self.assertTrue(self.product.is_active)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 3)

    def test_postgresql_rejects_active_uncategorized_product_directly(self):
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            Product.objects.create(name="Invalid product", is_active=True)
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, "catalog_active_product_has_category")
        self.assert_fixture_only()

    def _exercise_locking(self, second_operation):
        first_locked, second_lock_requested, release_first = Event(), Event(), Event()
        errors = Queue()
        original_record = audit_application.record_product_category_changed

        def pause_first(**kwargs):
            if kwargs["new_category_id"] == self.second.id:
                first_locked.set()
                if not release_first.wait(10):
                    raise RuntimeError("test synchronization timeout")
            return original_record(**kwargs)

        def worker(is_first):
            close_old_connections()
            db = connections["default"]
            try:
                if db.settings_dict["NAME"] != "test_mira":
                    raise RuntimeError("unexpected test database")

                def observe_lock(execute, sql, params, many, context):
                    if not is_first and "FOR UPDATE" in sql:
                        second_lock_requested.set()
                    return execute(sql, params, many, context)

                with db.execute_wrapper(observe_lock):
                    if is_first:
                        self.invoke("assignment")
                    elif second_operation == "assignment":
                        set_product_category(principal=self.principal, product_id=self.product.id, category_id=self.third.id)
                    else:
                        self.invoke("activation")
            except Exception as error:
                errors.put(error)
            finally:
                db.close()

        first = Thread(target=worker, args=(True,), daemon=True)
        second = Thread(target=worker, args=(False,), daemon=True)
        with patch.object(audit_application, "record_product_category_changed", side_effect=pause_first):
            first.start()
            try:
                self.assertTrue(first_locked.wait(10), "first writer did not reach the audit boundary")
                second.start()
                self.assertTrue(second_lock_requested.wait(10), "second writer did not request its row lock")
                with self.assertRaises(DatabaseError) as failure, transaction.atomic():
                    Product.objects.select_for_update(nowait=True).get(pk=self.product.pk)
                self.assertEqual(failure.exception.__cause__.sqlstate, "55P03")
            finally:
                release_first.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(errors.empty(), list(errors.queue))
        self.product.refresh_from_db()
        entries = list(CatalogChangeAuditEntry.objects.order_by("occurred_at", "id"))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].previous_category_id, self.first.id)
        self.assertEqual(entries[0].new_category_id, self.second.id)
        if second_operation == "assignment":
            self.assertEqual(entries[1].previous_category_id, self.second.id)
            self.assertEqual(entries[1].new_category_id, self.third.id)
            self.assertEqual(self.product.category_id, self.third.id)
        else:
            self.assertFalse(entries[1].previous_is_active)
            self.assertTrue(entries[1].new_is_active)
            self.assertEqual(self.product.category_id, self.second.id)
            self.assertTrue(self.product.is_active)

    def test_concurrent_category_changes_audit_the_fresh_previous_value(self):
        self._exercise_locking("assignment")

    def test_category_and_activation_changes_use_the_same_product_lock(self):
        self._exercise_locking("activation")
