from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalog.application import create_category, list_categories, set_product_active, set_product_category
from catalog.models import Category, Product
from identity.application import require_capability
from identity.contracts import AccessDenied, ActorContext, AuthenticationRequired, Capability


class CatalogCommercialCapabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(
            username="fictional-permission-operator", password=cls.password
        )
        cls.permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(content_type__app_label="catalog")
        }
        cls.mapping = (
            (Capability.CREATE_CATEGORY, "add_category"),
            (Capability.LIST_CATEGORIES, "view_category"),
            (Capability.SET_PRODUCT_CATEGORY, "set_product_category"),
            (Capability.SET_PRODUCT_ACTIVE, "set_product_active"),
        )

    def principal(self):
        principal = authenticate(username=self.user.username, password=self.password)
        self.assertIsNotNone(principal)
        return principal

    def test_new_capabilities_require_their_own_direct_permission(self):
        principal = self.principal()
        for granted_capability, codename in self.mapping:
            self.user.user_permissions.set([self.permissions[codename]])
            for capability, unused in self.mapping:
                with self.subTest(granted=granted_capability, requested=capability):
                    if capability == granted_capability:
                        self.assertEqual(
                            require_capability(principal=principal, capability=capability),
                            ActorContext(actor_id=str(self.user.pk)),
                        )
                    else:
                        with self.assertRaises(AccessDenied):
                            require_capability(principal=principal, capability=capability)

    def test_native_groups_grant_each_new_capability(self):
        group = Group.objects.create(name="fictional-category-permissions")
        self.user.groups.add(group)
        principal = self.principal()
        for capability, codename in self.mapping:
            with self.subTest(capability=capability):
                group.permissions.set([self.permissions[codename]])
                self.assertEqual(
                    require_capability(principal=principal, capability=capability).actor_id,
                    str(self.user.pk),
                )

    def test_existing_permissions_and_staff_do_not_grant_new_capabilities(self):
        self.user.user_permissions.set([
            self.permissions[name]
            for name in ("add_product", "add_variant", "view_product", "view_variant", "change_product")
        ])
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        principal = self.principal()
        for capability, unused in self.mapping:
            with self.subTest(capability=capability):
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)

    def test_revocation_inactivity_and_deletion_are_detected_on_original_principal(self):
        principal = self.principal()
        for capability, codename in self.mapping:
            with self.subTest(capability=capability):
                permission = self.permissions[codename]
                self.user.user_permissions.add(permission)
                require_capability(principal=principal, capability=capability)
                self.user.user_permissions.remove(permission)
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)
        self.user.user_permissions.set([self.permissions[name] for unused, name in self.mapping])
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)
        get_user_model().objects.filter(pk=principal.pk).delete()
        for capability, unused in self.mapping:
            with self.assertRaises(AuthenticationRequired):
                require_capability(principal=principal, capability=capability)

    def test_active_superuser_has_known_capabilities_but_not_strings(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        principal = self.principal()
        for capability, unused in self.mapping:
            require_capability(principal=principal, capability=capability)
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability.value)
        with self.assertRaises(AccessDenied):
            require_capability(principal=principal, capability="catalog.unknown")
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)

    def test_all_new_entry_points_deny_before_business_validation_or_queries(self):
        principal = self.principal()
        calls = (
            lambda actor: create_category(principal=actor, name=""),
            lambda actor: list_categories(principal=actor),
            lambda actor: set_product_category(principal=actor, product_id=uuid4(), category_id=uuid4()),
            lambda actor: set_product_active(principal=actor, product_id=uuid4(), is_active="true"),
        )
        for actor, error in (
            (principal, AccessDenied), (None, AuthenticationRequired),
            (AnonymousUser(), AuthenticationRequired),
            (ActorContext(actor_id=str(self.user.pk)), AuthenticationRequired),
            ({"pk": self.user.pk}, AuthenticationRequired),
        ):
            for index, call in enumerate(calls):
                with self.subTest(actor_type=type(actor).__name__, operation=index):
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(error):
                            call(actor)
                    self.assertFalse(any(
                        '"catalog_' in query["sql"] or '"audit_' in query["sql"]
                        for query in queries
                    ))
        self.assertFalse(Category.objects.exists())
        self.assertFalse(Product.objects.exists())
