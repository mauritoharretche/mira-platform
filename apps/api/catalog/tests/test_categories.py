from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase

from audit.models import CatalogChangeAuditEntry
from catalog.application import (
    CategoryNotFoundError, CatalogValidationError, ProductNotFoundError,
    create_category, list_categories, set_product_category,
)
from catalog.models import Category, Product


class CategoryApplicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(
            username="fictional-category-operator", password=cls.password
        )
        cls.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="catalog",
            codename__in=["add_category", "view_category", "set_product_category"],
        ))
        cls.product = Product.objects.create(name="Fictional product")

    def setUp(self):
        self.principal = authenticate(username=self.user.username, password=self.password)
        self.assertIsNotNone(self.principal)

    def test_normalizes_nfc_trim_and_casefold_without_losing_presentation(self):
        category = create_category(principal=self.principal, name="  CaFe\u0301  ")

        self.assertEqual(category.name, "CaFé")
        self.assertEqual(category.name_key, "café")
        entry = CatalogChangeAuditEntry.objects.get()
        self.assertEqual(entry.actor_id, str(self.user.pk))
        self.assertEqual(entry.action, "catalog.category_created")
        self.assertEqual(entry.target_id, category.id)
        self.assertIsNotNone(entry.occurred_at.utcoffset())
        self.assertEqual(entry.result, "success")
        self.assertIsNone(entry.previous_category_id)
        self.assertIsNone(entry.new_category_id)
        self.assertIsNone(entry.previous_is_active)
        self.assertIsNone(entry.new_is_active)

    def test_equivalent_case_and_unicode_names_conflict_without_false_audit(self):
        create_category(principal=self.principal, name="Café")
        for name in (" CAFÉ ", "CAFE\u0301"):
            with self.subTest(name=name):
                with self.assertRaises(IntegrityError):
                    create_category(principal=self.principal, name=name)

        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 1)

    def test_preserves_accents_and_internal_spaces(self):
        names = ("Café", "Cafe", "Two spaces", "Two  spaces")
        for name in names:
            create_category(principal=self.principal, name=name)

        self.assertEqual(set(Category.objects.values_list("name", flat=True)), set(names))

    def test_rejects_empty_invalid_and_overlong_names_or_keys_without_truncating(self):
        for name in ("", " \t\n ", None, 42, "a" * 256, "ß" * 128):
            with self.subTest(name_type=type(name).__name__):
                with self.assertRaises(CatalogValidationError):
                    create_category(principal=self.principal, name=name)

        self.assertFalse(Category.objects.exists())
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_accepts_technical_name_and_key_boundaries(self):
        first = create_category(principal=self.principal, name="A" * 255)
        second = create_category(principal=self.principal, name="ß" * 127 + "x")
        self.assertEqual(len(first.name), 255)
        self.assertEqual(len(first.name_key), 255)
        self.assertEqual(len(second.name_key), 255)

    def test_lists_in_canonical_order_without_writing_audit(self):
        for name in ("Zulu", "alpha", "Café"):
            create_category(principal=self.principal, name=name)
        before = CatalogChangeAuditEntry.objects.count()

        categories = list_categories(principal=self.principal)

        self.assertIsInstance(categories, list)
        self.assertEqual([category.name for category in categories], ["alpha", "Café", "Zulu"])
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), before)

    def test_assigns_reassigns_and_clears_inactive_product_with_exact_history(self):
        first = Category.objects.create(name="First", name_key="first")
        second = Category.objects.create(name="Second", name_key="second")
        previous = None
        for category_id in (first.id, second.id, None):
            set_product_category(
                principal=self.principal, product_id=self.product.id, category_id=category_id
            )
            entry = CatalogChangeAuditEntry.objects.latest("occurred_at")
            self.assertEqual(entry.target_id, self.product.id)
            self.assertEqual(entry.actor_id, str(self.user.id))
            self.assertEqual(entry.action, "catalog.product_category_changed")
            self.assertEqual(entry.previous_category_id, previous)
            self.assertEqual(entry.new_category_id, category_id)
            self.assertIsNone(entry.previous_is_active)
            self.assertIsNone(entry.new_is_active)
            previous = category_id

        self.product.refresh_from_db()
        self.assertIsNone(self.product.category_id)
        self.assertFalse(self.product.is_active)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 3)

    def test_category_noop_preserves_timestamp_and_audit(self):
        category = Category.objects.create(name="First", name_key="first")
        for category_id in (None, category.id):
            set_product_category(
                principal=self.principal, product_id=self.product.id, category_id=category_id
            )
            self.product.refresh_from_db()
            before = self.product.updated_at
            count = CatalogChangeAuditEntry.objects.count()
            set_product_category(
                principal=self.principal, product_id=self.product.id, category_id=category_id
            )
            self.product.refresh_from_db()
            self.assertEqual(self.product.updated_at, before)
            self.assertEqual(CatalogChangeAuditEntry.objects.count(), count)

    def test_rejects_missing_category_or_product_without_writes(self):
        with self.assertRaises(CategoryNotFoundError):
            set_product_category(
                principal=self.principal, product_id=self.product.id, category_id=uuid4()
            )
        with self.assertRaises(ProductNotFoundError):
            set_product_category(principal=self.principal, product_id=uuid4(), category_id=None)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.category_id)
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_referenced_category_is_protected_from_orm_deletion(self):
        category = Category.objects.create(name="First", name_key="first")
        set_product_category(
            principal=self.principal, product_id=self.product.id, category_id=category.id
        )
        with self.assertRaises(ProtectedError):
            category.delete()


class CategoryDatabaseTests(TransactionTestCase):
    def test_unique_key_is_enforced_without_application_validation(self):
        Category.objects.create(name="First", name_key="shared")
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            Category.objects.create(name="Different display", name_key="shared")
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, "catalog_category_name_key_unique")
        self.assertEqual(Category.objects.count(), 1)

    def test_database_rejects_empty_names_and_keys(self):
        for values in ({"name": "", "name_key": "key"}, {"name": "Name", "name_key": ""}):
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError) as failure, transaction.atomic():
                    Category.objects.create(**values)
                self.assertEqual(failure.exception.__cause__.diag.constraint_name, "catalog_category_names_nonempty")

    def test_foreign_key_rejects_nonexistent_category(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(name="Fictional product", category_id=uuid4())
        self.assertFalse(Product.objects.exists())
