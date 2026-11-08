from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from audit.models import CatalogMeasurementAuditEntry
from catalog.application import get_variant_measurement_spec, set_variant_measurement_spec
from catalog.contracts import MeasurementUnit
from catalog.models import Product, Variant, VariantMeasurementSpec
from identity.application import require_capability
from identity.contracts import AccessDenied, ActorContext, AuthenticationRequired, Capability


class CatalogMeasurementCapabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(username="fictional-measurement-permissions", password=cls.password)
        cls.permissions = {p.codename: p for p in Permission.objects.filter(content_type__app_label="catalog")}
        cls.mapping = (
            (Capability.SET_VARIANT_MEASUREMENT_SPEC, "set_variant_measurement_spec"),
            (Capability.READ_VARIANT_MEASUREMENT_SPEC, "view_variantmeasurementspec"),
        )

    def principal(self):
        principal = authenticate(username=self.user.username, password=self.password)
        self.assertIsNotNone(principal)
        return principal

    def test_direct_grants_are_independent_and_no_permission_is_automatically_assigned(self):
        self.assertFalse(self.user.user_permissions.exists())
        principal = self.principal()
        for granted in ((), (self.mapping[0][1],), (self.mapping[1][1],), tuple(name for unused, name in self.mapping)):
            self.user.user_permissions.set(self.permissions[name] for name in granted)
            for capability, required in self.mapping:
                with self.subTest(granted=granted, capability=capability):
                    if required in granted:
                        self.assertEqual(require_capability(principal=principal, capability=capability), ActorContext(str(self.user.pk)))
                    else:
                        with self.assertRaises(AccessDenied):
                            require_capability(principal=principal, capability=capability)

    def test_groups_work_without_elevating_staff_or_reusing_older_permissions(self):
        principal = self.principal()
        excluded = {name for unused, name in self.mapping}
        self.user.user_permissions.set(permission for name, permission in self.permissions.items() if name not in excluded)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)
        group = Group.objects.create(name="fictional-measurement-group")
        self.user.groups.add(group)
        for capability, name in self.mapping:
            group.permissions.set([self.permissions[name]])
            require_capability(principal=principal, capability=capability)
            group.permissions.clear()
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)

    def test_fresh_authorization_observes_revocation_inactivity_and_deletion(self):
        principal = self.principal()
        for capability, name in self.mapping:
            self.user.user_permissions.set([self.permissions[name]])
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

    def test_active_superuser_accepts_recognized_capabilities_only(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        principal = self.principal()
        for capability, unused in self.mapping:
            require_capability(principal=principal, capability=capability)
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability.value)
        with self.assertRaises(AccessDenied):
            require_capability(principal=principal, capability="catalog.convert_measurement")
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=principal, capability=capability)

    def test_both_operations_deny_invalid_principals_before_business_validation_or_queries(self):
        calls = (
            lambda actor: set_variant_measurement_spec(
                principal=actor, variant_id="not-a-uuid", sale_unit="invalid",
                net_content_amount=Decimal("0"), net_content_unit=None, expected_version=False,
            ),
            lambda actor: get_variant_measurement_spec(principal=actor, variant_id=uuid4()),
        )
        actors = ((self.principal(), AccessDenied), (None, AuthenticationRequired), (AnonymousUser(), AuthenticationRequired),
                  (self.user.pk, AuthenticationRequired), (str(self.user.pk), AuthenticationRequired),
                  ({"pk": self.user.pk}, AuthenticationRequired), (object(), AuthenticationRequired),
                  (ActorContext(str(self.user.pk)), AuthenticationRequired),
                  (get_user_model()(pk=self.user.pk, is_superuser=True), AuthenticationRequired))
        for actor, error in actors:
            for operation in calls:
                with self.subTest(actor=type(actor).__name__), CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(error):
                        operation(actor)
                self.assertFalse(any('"catalog_' in item["sql"] or '"audit_' in item["sql"] for item in queries))
        self.assertFalse(VariantMeasurementSpec.objects.exists())
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_read_and_write_use_fixed_capabilities_even_for_unknown_and_noop_states(self):
        product = Product.objects.create(name="Fictional product")
        variant = Variant.objects.create(product=product, label="Presentation", sku="PERM-MEASURE-001")
        principal = self.principal()
        args = dict(principal=principal, variant_id=variant.id, sale_unit=None,
                    net_content_amount=None, net_content_unit=None, expected_version=None)
        with self.assertRaises(AccessDenied):
            set_variant_measurement_spec(**args)
        self.user.user_permissions.set([self.permissions["set_variant_measurement_spec"]])
        self.assertIsNone(set_variant_measurement_spec(**args))
        result = set_variant_measurement_spec(**{**args, "sale_unit": MeasurementUnit.EACH})
        with self.assertRaises(AccessDenied):
            get_variant_measurement_spec(principal=principal, variant_id=variant.id)
        self.user.user_permissions.set([self.permissions["view_variantmeasurementspec"]])
        self.assertEqual(get_variant_measurement_spec(principal=principal, variant_id=variant.id), result)
        with self.assertRaises(AccessDenied):
            set_variant_measurement_spec(**{**args, "sale_unit": MeasurementUnit.EACH, "expected_version": 1})
        self.assertEqual(VariantMeasurementSpec.objects.count(), 1)
        self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), 1)
