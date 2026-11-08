from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from identity.application import require_capability
from identity.contracts import AccessDenied, ActorContext, AuthenticationRequired, Capability
from inventory.application import adjust_stock, get_stock, list_stock_movements, receive_stock
from inventory.contracts import InventoryUnit
from inventory.models import StockPosition
from inventory.tests.test_application import assert_test_database, make_principal, make_variant


class InventoryCapabilityTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user, self.principal = make_principal(grant=False)
        self.permissions = {p.codename: p for p in Permission.objects.filter(content_type__app_label="inventory")}
        self.mapping = ((Capability.RECEIVE_STOCK, "receive_stock"), (Capability.ADJUST_STOCK, "adjust_stock"),
                        (Capability.READ_STOCK, "view_stockposition"), (Capability.READ_STOCK_MOVEMENTS, "view_stockmovement"))

    def test_direct_and_group_grants_are_independent_and_none_are_assigned(self):
        self.assertFalse(self.user.user_permissions.exists())
        for capability, required in self.mapping:
            self.user.user_permissions.set([self.permissions[required]])
            for candidate, unused in self.mapping:
                if candidate is capability:
                    self.assertEqual(require_capability(principal=self.principal, capability=candidate), ActorContext(str(self.user.pk)))
                else:
                    with self.assertRaises(AccessDenied):
                        require_capability(principal=self.principal, capability=candidate)
        self.user.user_permissions.clear()
        group = Group.objects.create(name="fictional-stock-group")
        self.user.groups.add(group)
        for capability, name in self.mapping:
            group.permissions.set([self.permissions[name]])
            require_capability(principal=self.principal, capability=capability)
            group.permissions.clear()
            with self.assertRaises(AccessDenied):
                require_capability(principal=self.principal, capability=capability)

    def test_native_writes_staff_and_catalog_permissions_do_not_elevate(self):
        self.user.user_permissions.set(Permission.objects.exclude(
            codename__in=[name for unused, name in self.mapping], content_type__app_label="inventory"))
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=self.principal, capability=capability)

    def test_fresh_authorization_handles_revocation_inactivity_and_deleted_users(self):
        for capability, name in self.mapping:
            self.user.user_permissions.set([self.permissions[name]])
            require_capability(principal=self.principal, capability=capability)
            self.user.user_permissions.clear()
            with self.assertRaises(AccessDenied):
                require_capability(principal=self.principal, capability=capability)
        self.user.user_permissions.set(self.permissions.values())
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        for capability, unused in self.mapping:
            with self.assertRaises(AccessDenied):
                require_capability(principal=self.principal, capability=capability)
        self.user.delete()
        with self.assertRaises(AuthenticationRequired):
            require_capability(principal=self.principal, capability=Capability.READ_STOCK)

    def test_superusers_accept_only_known_capabilities_and_inactive_are_denied(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=True)
        for capability, unused in self.mapping:
            require_capability(principal=self.principal, capability=capability)
            with self.assertRaises(AccessDenied):
                require_capability(principal=self.principal, capability=capability.value)
        with self.assertRaises(AccessDenied):
            require_capability(principal=self.principal, capability="inventory.sell")
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(AccessDenied):
            require_capability(principal=self.principal, capability=Capability.READ_STOCK)

    def test_all_operations_deny_spoofed_principals_before_business_validation_or_queries(self):
        calls = (
            lambda p: receive_stock(principal=p, variant_id="bad", unit="bad", quantity=-1, expected_version=False),
            lambda p: adjust_stock(principal=p, variant_id="bad", new_on_hand=-1, reason=None, expected_version=False),
            lambda p: get_stock(principal=p, variant_id=uuid4()),
            lambda p: list_stock_movements(principal=p, variant_id=uuid4()),
        )
        actors = ((self.principal, AccessDenied), (None, AuthenticationRequired), (AnonymousUser(), AuthenticationRequired),
                  (self.user.pk, AuthenticationRequired), (str(self.user.pk), AuthenticationRequired),
                  ({"pk": self.user.pk}, AuthenticationRequired), (object(), AuthenticationRequired),
                  (ActorContext(str(self.user.pk)), AuthenticationRequired),
                  (get_user_model()(pk=self.user.pk, is_superuser=True), AuthenticationRequired))
        for actor, error in actors:
            for call in calls:
                with self.subTest(actor=type(actor).__name__), CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(error):
                        call(actor)
                self.assertFalse(any(any('"'+name+'_' in item["sql"] for name in ("catalog", "inventory", "audit")) for item in queries))

    def test_inventory_permissions_alone_allow_receipts_but_not_reads_adjustments_or_history(self):
        variant = make_variant()
        self.user.user_permissions.set([self.permissions["receive_stock"]])
        receive_stock(principal=self.principal, variant_id=variant.pk, unit=InventoryUnit.EACH, quantity=3, expected_version=None)
        for call in (
            lambda: get_stock(principal=self.principal, variant_id=variant.pk),
            lambda: list_stock_movements(principal=self.principal, variant_id=variant.pk),
            lambda: adjust_stock(principal=self.principal, variant_id=variant.pk, new_on_hand=3, reason="fictional no-op", expected_version=1),
        ):
            with self.assertRaises(AccessDenied):
                call()
        self.assertEqual(StockPosition.objects.get().on_hand, 3)
