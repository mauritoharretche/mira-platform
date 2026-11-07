from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from audit import application as audit_application
from audit.models import CatalogAuditEntry
from catalog.application import (
    CatalogValidationError,
    ProductNotFoundError,
    add_variant_to_product,
    create_product_with_initial_variant,
    get_product,
)
from catalog.models import Product, Variant
from identity.contracts import AccessDenied, ActorContext, AuthenticationRequired


class AuthorizedCatalogOperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.password = "fictional-test-password-only"
        cls.operator = get_user_model().objects.create_user(
            username="fictional-catalog-operator", password=cls.password
        )
        cls.permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(content_type__app_label="catalog")
        }
        cls.product = Product.objects.create(name="Fictional fixture product")
        cls.variant = Variant.objects.create(
            product=cls.product, label="Fixture variant", sku="FIXTURE-001"
        )

    def principal(self):
        principal = authenticate(username=self.operator.username, password=self.password)
        self.assertIsNotNone(principal)
        return principal

    def grant(self, *permissions: str) -> None:
        self.operator.user_permissions.add(*(self.permissions[name] for name in permissions))

    def counts(self):
        return (
            Product.objects.count(), Variant.objects.count(), CatalogAuditEntry.objects.count()
        )

    def invoke(self, operation, principal):
        if operation == "create":
            return create_product_with_initial_variant(
                principal=principal,
                product_name="Fictional created product",
                variant_label="Fictional initial variant",
                sku="CREATED-001",
            )
        if operation == "add":
            return add_variant_to_product(
                principal=principal,
                product_id=self.product.pk,
                label="Fictional added variant",
                sku="ADDED-001",
            )
        return get_product(principal=principal, product_id=self.product.pk)

    def assert_denied_without_business_access(self, principal, error_type):
        before = self.counts()
        for operation in ("create", "add", "read"):
            with self.subTest(operation=operation):
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(error_type):
                        self.invoke(operation, principal)
                for query in queries:
                    for table in ("catalog_product", "catalog_variant", "audit_catalogauditentry"):
                        self.assertNotIn(f'"{table}"', query["sql"])
        self.assertEqual(self.counts(), before)

    def test_direct_permissions_create_product_with_exact_audit_attribution(self) -> None:
        self.grant("add_product", "add_variant")

        product = self.invoke("create", self.principal())

        entry = CatalogAuditEntry.objects.get()
        self.assertEqual(entry.actor_id, str(self.operator.pk))
        self.assertEqual(entry.action, "catalog.product_created_with_initial_variant")
        self.assertEqual(entry.product_id, product.pk)
        self.assertEqual(entry.variant_id, product.variants.get().pk)
        self.assertEqual(entry.result, "success")
        self.assertIsNotNone(entry.occurred_at.utcoffset())

    def test_direct_permission_adds_variant_with_exact_audit_attribution(self) -> None:
        self.grant("add_variant")

        variant = self.invoke("add", self.principal())

        entry = CatalogAuditEntry.objects.get()
        self.assertEqual(entry.actor_id, str(self.operator.pk))
        self.assertEqual(entry.action, "catalog.variant_added_to_product")
        self.assertEqual(entry.product_id, self.product.pk)
        self.assertEqual(entry.variant_id, variant.pk)
        self.assertEqual(entry.result, "success")
        self.assertIsNotNone(entry.occurred_at.utcoffset())

    def test_read_requires_both_permissions_and_creates_no_audit(self) -> None:
        self.grant("view_product", "view_variant")

        product = self.invoke("read", self.principal())

        self.assertEqual(product.pk, self.product.pk)
        self.assertEqual(list(product.variants.all()), [self.variant])
        self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_group_permissions_allow_all_three_operations(self) -> None:
        group = Group.objects.create(name="fictional-catalog-test-group")
        group.permissions.add(*self.permissions.values())
        self.operator.groups.add(group)
        principal = self.principal()

        self.invoke("create", principal)
        self.invoke("add", principal)
        self.invoke("read", principal)

        self.assertEqual(self.counts(), (2, 3, 2))

    def test_missing_permissions_deny_before_business_access(self) -> None:
        self.assert_denied_without_business_access(self.principal(), AccessDenied)

    def test_each_permission_in_a_required_pair_is_necessary(self) -> None:
        principal = self.principal()
        for operation, permission in (
            ("create", "add_product"), ("create", "add_variant"),
            ("read", "view_product"), ("read", "view_variant"),
        ):
            with self.subTest(operation=operation, permission=permission):
                self.operator.user_permissions.clear()
                self.grant(permission)
                with self.assertRaises(AccessDenied):
                    self.invoke(operation, principal)

        self.assertEqual(self.counts(), (1, 1, 0))

    def test_invalid_principals_cannot_use_any_operation(self) -> None:
        for principal in (
            None,
            AnonymousUser(),
            self.operator.pk,
            {"pk": self.operator.pk, "is_authenticated": True},
            SimpleNamespace(pk=self.operator.pk, is_authenticated=True),
            get_user_model()(pk=self.operator.pk),
            ActorContext(actor_id=str(self.operator.pk)),
        ):
            with self.subTest(principal_type=type(principal).__name__):
                self.assert_denied_without_business_access(principal, AuthenticationRequired)

    def test_inactive_user_is_denied_even_with_permissions(self) -> None:
        self.grant(*self.permissions)
        principal = self.principal()
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)

        self.assert_denied_without_business_access(principal, AccessDenied)

    def test_deleted_user_is_denied(self) -> None:
        self.grant(*self.permissions)
        principal = self.principal()
        get_user_model().objects.filter(pk=principal.pk).delete()

        self.assert_denied_without_business_access(principal, AuthenticationRequired)

    def test_staff_flag_does_not_grant_catalog_access(self) -> None:
        self.operator.is_staff = True
        self.operator.save(update_fields=["is_staff"])

        self.assert_denied_without_business_access(self.principal(), AccessDenied)

    def test_active_superuser_can_use_recognized_operations(self) -> None:
        self.operator.is_superuser = True
        self.operator.save(update_fields=["is_superuser"])
        principal = self.principal()

        for operation in ("create", "add", "read"):
            self.invoke(operation, principal)

        self.assertEqual(self.counts(), (2, 3, 2))
        self.assertEqual(
            set(CatalogAuditEntry.objects.values_list("actor_id", flat=True)),
            {str(self.operator.pk)},
        )

    def test_permission_revocation_is_detected_between_operations(self) -> None:
        self.grant(*self.permissions)
        principal = self.principal()
        self.assertTrue(principal.has_perm("catalog.add_product"))
        for operation in ("create", "add", "read"):
            self.invoke(operation, principal)
        self.operator.user_permissions.clear()

        self.assert_denied_without_business_access(principal, AccessDenied)

    def test_principal_is_required_for_all_public_operations(self) -> None:
        with self.assertRaises(TypeError):
            create_product_with_initial_variant(product_name="Product", variant_label="Variant", sku="X")
        with self.assertRaises(TypeError):
            add_variant_to_product(product_id=self.product.pk, label="Variant", sku="X")
        with self.assertRaises(TypeError):
            get_product(product_id=self.product.pk)

        self.assertEqual(self.counts(), (1, 1, 0))

    def test_unauthorized_calls_do_not_reveal_existence_or_validation_details(self) -> None:
        principal = self.principal()
        for product_id in (self.product.pk, uuid4()):
            with self.subTest(product_id=product_id):
                with self.assertRaisesMessage(AccessDenied, "Access denied"):
                    get_product(principal=principal, product_id=product_id)
                with self.assertRaisesMessage(AccessDenied, "Access denied"):
                    add_variant_to_product(principal=principal, product_id=product_id, label="", sku="")
        with self.assertRaisesMessage(AccessDenied, "Access denied"):
            create_product_with_initial_variant(
                principal=principal, product_name="", variant_label="", sku=""
            )

        self.assertEqual(self.counts(), (1, 1, 0))

    def test_audit_preserves_historical_actor_id_after_user_deletion(self) -> None:
        self.grant("add_variant")
        principal = self.principal()
        self.invoke("add", principal)
        actor_id = str(principal.pk)
        get_user_model().objects.filter(pk=principal.pk).delete()

        self.assertEqual(CatalogAuditEntry.objects.get().actor_id, actor_id)


class CatalogAuditAtomicityTests(TransactionTestCase):
    def setUp(self) -> None:
        password = "fictional-test-password-only"
        operator = get_user_model().objects.create_user(
            username="fictional-transaction-operator", password=password
        )
        operator.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="catalog")
        )
        self.principal = authenticate(username=operator.username, password=password)
        self.assertIsNotNone(self.principal)
        self.product = Product.objects.create(name="Fictional fixture product")
        self.variant = Variant.objects.create(
            product=self.product, label="Fixture variant", sku="FIXTURE-001", barcode="00123"
        )

    def invoke(self, operation):
        if operation == "create":
            return create_product_with_initial_variant(
                principal=self.principal, product_name="Created product",
                variant_label="Initial variant", sku="CREATED-001",
            )
        return add_variant_to_product(
            principal=self.principal, product_id=self.product.pk,
            label="Added variant", sku="ADDED-001",
        )

    def assert_fixture_only(self):
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Variant.objects.count(), 1)
        self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_success_commits_business_changes_and_audit_in_autocommit_mode(self) -> None:
        self.assertTrue(connection.get_autocommit())
        self.invoke("create")
        self.invoke("add")

        self.assertTrue(connection.get_autocommit())
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(Variant.objects.count(), 3)
        self.assertEqual(CatalogAuditEntry.objects.count(), 2)

    def test_audit_failure_before_insert_rolls_back_both_commands(self) -> None:
        for operation in ("create", "add"):
            with self.subTest(operation=operation):
                with patch.object(
                    audit_application, "record_catalog_success",
                    side_effect=DatabaseError("fictional audit failure"),
                ):
                    with self.assertRaises(DatabaseError):
                        self.invoke(operation)
                self.assert_fixture_only()

    def test_audit_failure_after_insert_rolls_back_business_and_audit(self) -> None:
        original_record = audit_application.record_catalog_success

        def insert_then_fail(**kwargs):
            entry_id = original_record(**kwargs)
            self.assertTrue(CatalogAuditEntry.objects.filter(pk=entry_id).exists())
            raise DatabaseError("fictional failure after audit insert")

        for operation in ("create", "add"):
            with self.subTest(operation=operation):
                with patch.object(audit_application, "record_catalog_success", side_effect=insert_then_fail):
                    with self.assertRaises(DatabaseError):
                        self.invoke(operation)
                self.assert_fixture_only()

    def test_outer_transaction_rollback_removes_business_changes_and_audit(self) -> None:
        for operation in ("create", "add"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(RuntimeError, "rollback requested"):
                    with transaction.atomic(using="default"):
                        self.invoke(operation)
                        self.assertEqual(CatalogAuditEntry.objects.count(), 1)
                        raise RuntimeError("rollback requested")
                self.assert_fixture_only()

    def test_sku_and_barcode_duplicates_leave_no_partial_changes_or_false_success(self) -> None:
        for operation in ("create", "add"):
            for values in (
                {"sku": "FIXTURE-001", "barcode": None},
                {"sku": "UNIQUE-001", "barcode": "00123"},
            ):
                with self.subTest(operation=operation, conflict=values):
                    with self.assertRaises(IntegrityError):
                        if operation == "create":
                            create_product_with_initial_variant(
                                principal=self.principal, product_name="Partial product",
                                variant_label="Conflicting variant", **values,
                            )
                        else:
                            add_variant_to_product(
                                principal=self.principal, product_id=self.product.pk,
                                label="Conflicting variant", **values,
                            )
                    self.assert_fixture_only()

    def test_validation_and_missing_product_leave_no_false_success(self) -> None:
        with self.assertRaises(CatalogValidationError):
            create_product_with_initial_variant(
                principal=self.principal, product_name=" ", variant_label="Variant", sku="X"
            )
        with self.assertRaises(CatalogValidationError):
            add_variant_to_product(
                principal=self.principal, product_id=self.product.pk, label=" ", sku="X"
            )
        with self.assertRaises(ProductNotFoundError):
            add_variant_to_product(
                principal=self.principal, product_id=uuid4(), label="Variant", sku="X"
            )
        self.assert_fixture_only()

    def test_catalog_failure_after_product_insert_rolls_back_without_audit(self) -> None:
        with patch.object(Variant.objects, "create", side_effect=DatabaseError("fictional catalog failure")):
            with self.assertRaises(DatabaseError):
                self.invoke("create")

        self.assert_fixture_only()
