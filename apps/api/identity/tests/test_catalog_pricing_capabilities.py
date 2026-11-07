from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from audit.models import CatalogPriceAuditEntry
from catalog.application import get_current_variant_price, list_variant_price_history, set_variant_price
from catalog.models import Product, Variant, VariantPriceRevision
from identity.application import require_capability
from identity.contracts import AccessDenied, ActorContext, AuthenticationRequired, Capability


class CatalogPricingCapabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(username="fictional-price-permissions", password=cls.password)
        cls.permissions = {p.codename: p for p in Permission.objects.filter(content_type__app_label="catalog")}
        cls.mapping = (
            (Capability.SET_VARIANT_PRICE, ("set_variant_price",)),
            (Capability.READ_VARIANT_PRICE, ("view_variantpricerevision",)),
            (Capability.READ_VARIANT_PRICE_HISTORY, ("view_variantpricerevision", "view_variant_price_history")),
        )

    def principal(self):
        principal = authenticate(username=self.user.username, password=self.password)
        self.assertIsNotNone(principal)
        return principal

    def test_direct_grants_and_every_permission_in_history_pair_are_required(self):
        principal = self.principal()
        grants = ((), ("set_variant_price",), ("view_variantpricerevision",),
                  ("view_variant_price_history",), ("view_variantpricerevision", "view_variant_price_history"))
        for names in grants:
            self.user.user_permissions.set(self.permissions[name] for name in names)
            for capability, required in self.mapping:
                with self.subTest(granted=names, capability=capability):
                    if set(required) <= set(names):
                        self.assertEqual(require_capability(principal=principal, capability=capability),
                                         ActorContext(actor_id=str(self.user.pk)))
                    else:
                        with self.assertRaises(AccessDenied):
                            require_capability(principal=principal, capability=capability)

    def test_groups_supply_the_same_granular_permissions(self):
        group = Group.objects.create(name="fictional-price-group")
        self.user.groups.add(group)
        principal = self.principal()
        for capability, names in self.mapping:
            group.permissions.set(self.permissions[name] for name in names)
            require_capability(principal=principal, capability=capability)
        group.permissions.remove(self.permissions["view_variant_price_history"])
        with self.assertRaises(AccessDenied):
            require_capability(principal=principal, capability=Capability.READ_VARIANT_PRICE_HISTORY)

    def test_old_permissions_native_model_mutation_permissions_and_staff_do_not_grant_pricing(self):
        excluded = {"set_variant_price", "view_variantpricerevision", "view_variant_price_history"}
        self.user.user_permissions.set(permission for name, permission in self.permissions.items() if name not in excluded)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        principal = self.principal()
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)

    def test_revocation_inactivity_and_deletion_are_observed_on_the_original_principal(self):
        principal = self.principal()
        for capability, names in self.mapping:
            self.user.user_permissions.set(self.permissions[name] for name in names)
            require_capability(principal=principal, capability=capability)
            self.user.user_permissions.clear()
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)
        self.user.user_permissions.set(self.permissions.values())
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)
        get_user_model().objects.filter(pk=principal.pk).delete()
        for capability, unused in self.mapping:
            with self.assertRaises(AuthenticationRequired):
                require_capability(principal=principal, capability=capability)

    def test_active_superuser_has_known_enum_capabilities_only(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        principal = self.principal()
        for capability, unused in self.mapping:
            require_capability(principal=principal, capability=capability)
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability.value)
        with self.assertRaises(AccessDenied):
            require_capability(principal=principal, capability="catalog.withdraw_variant_price")
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)

    def test_all_entry_points_deny_invalid_or_unprivileged_principals_before_business_access(self):
        principal = self.principal()
        calls = (
            lambda actor: set_variant_price(principal=actor, variant_id=uuid4(), amount=Decimal("0"),
                                           currency="USD", expected_revision_id=None),
            lambda actor: get_current_variant_price(principal=actor, variant_id=uuid4()),
            lambda actor: list_variant_price_history(principal=actor, variant_id=uuid4()),
        )
        actors = ((principal, AccessDenied), (None, AuthenticationRequired), (AnonymousUser(), AuthenticationRequired),
                  (self.user.pk, AuthenticationRequired), (str(self.user.pk), AuthenticationRequired),
                  ({"pk": self.user.pk}, AuthenticationRequired), (object(), AuthenticationRequired),
                  (ActorContext(actor_id=str(self.user.pk)), AuthenticationRequired),
                  (get_user_model()(pk=self.user.pk, is_superuser=True), AuthenticationRequired))
        for actor, error in actors:
            for operation in calls:
                with self.subTest(actor=type(actor).__name__), CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(error):
                        operation(actor)
                self.assertFalse(any('"catalog_' in item["sql"] or '"audit_' in item["sql"] for item in queries))
        self.assertFalse(VariantPriceRevision.objects.exists())
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_operations_use_fixed_capabilities_and_a_noop_still_requires_write_permission(self):
        product = Product.objects.create(name="Fictional product")
        variant = Variant.objects.create(product=product, label="Presentation", sku="FICT-001")
        principal = self.principal()
        self.user.user_permissions.set([self.permissions["set_variant_price"]])
        result = set_variant_price(principal=principal, variant_id=variant.id, amount=Decimal("1"),
                                   currency="ARS", expected_revision_id=None)
        for read in (get_current_variant_price, list_variant_price_history):
            with self.assertRaises(AccessDenied):
                read(principal=principal, variant_id=variant.id)
        self.user.user_permissions.set([self.permissions["view_variantpricerevision"]])
        self.assertEqual(get_current_variant_price(principal=principal, variant_id=variant.id), result)
        with self.assertRaises(AccessDenied):
            list_variant_price_history(principal=principal, variant_id=variant.id)
        with self.assertRaises(AccessDenied):
            set_variant_price(principal=principal, variant_id=variant.id, amount=Decimal("1"),
                              currency="ARS", expected_revision_id=result.revision_id)
        self.user.user_permissions.add(self.permissions["view_variant_price_history"])
        self.assertEqual(list_variant_price_history(principal=principal, variant_id=variant.id), (result,))
        self.assertEqual(VariantPriceRevision.objects.count(), 1)
        self.assertEqual(CatalogPriceAuditEntry.objects.count(), 1)
