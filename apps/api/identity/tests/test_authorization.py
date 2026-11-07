from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.db import DatabaseError
from django.test import TestCase

from identity.application import require_capability
from identity.contracts import AccessDenied, ActorContext, AuthenticationRequired, Capability


class CapabilityAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(
            username="fictional-operator", password=cls.password
        )
        cls.superuser = get_user_model().objects.create_user(
            username="fictional-superuser",
            password=cls.password,
            is_staff=True,
            is_superuser=True,
        )
        cls.permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(content_type__app_label="catalog")
        }

    def grant(self, *codenames: str) -> None:
        self.user.user_permissions.add(*(self.permissions[name] for name in codenames))

    def authenticated_principal(self):
        principal = authenticate(username=self.user.username, password=self.password)
        self.assertIsNotNone(principal)
        return principal

    def test_rejects_missing_anonymous_and_arbitrary_principals(self) -> None:
        for principal in (None, AnonymousUser(), object()):
            with self.subTest(principal_type=type(principal).__name__):
                with self.assertRaises(AuthenticationRequired):
                    require_capability(principal=principal, capability=Capability.READ_PRODUCT)

    def test_rejects_unsaved_user_even_with_an_existing_privileged_id(self) -> None:
        principal = get_user_model()(pk=self.superuser.pk, is_superuser=True)

        # This is True even though this instance has never been authenticated.
        self.assertTrue(principal.is_authenticated)
        with self.assertRaises(AuthenticationRequired):
            require_capability(principal=principal, capability=Capability.READ_PRODUCT)

    def test_rejects_arbitrary_ids_including_an_existing_privileged_id(self) -> None:
        for principal in (self.superuser.pk, str(self.superuser.pk), -1):
            with self.subTest(principal_type=type(principal).__name__):
                with self.assertRaises(AuthenticationRequired):
                    require_capability(principal=principal, capability=Capability.READ_PRODUCT)

    def test_rejects_dictionaries_and_objects_spoofing_authentication(self) -> None:
        attributes = {
            "pk": self.superuser.pk,
            "is_authenticated": True,
            "is_active": True,
            "is_superuser": True,
            "has_perm": lambda permission: True,
        }
        for principal in (attributes, SimpleNamespace(**attributes)):
            with self.subTest(principal_type=type(principal).__name__):
                with self.assertRaises(AuthenticationRequired):
                    require_capability(principal=principal, capability=Capability.READ_PRODUCT)

    def test_denies_all_capabilities_without_permissions(self) -> None:
        principal = self.authenticated_principal()

        for capability in Capability:
            with self.subTest(capability=capability):
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)

    def test_allows_each_capability_with_its_direct_permissions(self) -> None:
        cases = (
            (Capability.READ_PRODUCT, ("view_product", "view_variant")),
            (Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT, ("add_product", "add_variant")),
            (Capability.ADD_VARIANT_TO_PRODUCT, ("add_variant",)),
        )
        principal = self.authenticated_principal()

        for capability, codenames in cases:
            with self.subTest(capability=capability):
                self.user.user_permissions.clear()
                self.grant(*codenames)
                actor = require_capability(principal=principal, capability=capability)
                self.assertEqual(actor, ActorContext(actor_id=str(self.user.pk)))

    def test_allows_each_capability_with_group_permissions(self) -> None:
        group = Group.objects.create(name="fictional-test-group")
        self.user.groups.add(group)
        principal = self.authenticated_principal()
        cases = (
            (Capability.READ_PRODUCT, ("view_product", "view_variant")),
            (Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT, ("add_product", "add_variant")),
            (Capability.ADD_VARIANT_TO_PRODUCT, ("add_variant",)),
        )

        for capability, codenames in cases:
            with self.subTest(capability=capability):
                group.permissions.set(self.permissions[name] for name in codenames)
                actor = require_capability(principal=principal, capability=capability)
                self.assertEqual(actor.actor_id, str(self.user.pk))

    def test_denies_when_either_required_permission_is_missing(self) -> None:
        cases = (
            (Capability.READ_PRODUCT, "view_product"),
            (Capability.READ_PRODUCT, "view_variant"),
            (Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT, "add_product"),
            (Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT, "add_variant"),
        )
        principal = self.authenticated_principal()

        for capability, granted_permission in cases:
            with self.subTest(capability=capability, granted_permission=granted_permission):
                self.user.user_permissions.clear()
                self.grant(granted_permission)
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)

    def test_read_permissions_do_not_allow_writes(self) -> None:
        self.grant("view_product", "view_variant")
        principal = self.authenticated_principal()

        for capability in (
            Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT,
            Capability.ADD_VARIANT_TO_PRODUCT,
        ):
            with self.subTest(capability=capability):
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)

    def test_write_permissions_do_not_allow_reads(self) -> None:
        self.grant("add_product", "add_variant")

        with self.assertRaises(AccessDenied):
            require_capability(
                principal=self.authenticated_principal(), capability=Capability.READ_PRODUCT
            )

    def test_variant_permission_does_not_allow_creating_a_product(self) -> None:
        self.grant("add_variant")
        principal = self.authenticated_principal()

        require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        with self.assertRaises(AccessDenied):
            require_capability(
                principal=principal, capability=Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT
            )

    def test_staff_flag_does_not_grant_capabilities(self) -> None:
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        principal = self.authenticated_principal()

        for capability in Capability:
            with self.subTest(capability=capability):
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)

    def test_active_superuser_can_use_each_recognized_capability(self) -> None:
        principal = authenticate(username=self.superuser.username, password=self.password)
        self.assertIsNotNone(principal)

        for capability in Capability:
            with self.subTest(capability=capability):
                actor = require_capability(principal=principal, capability=capability)
                self.assertEqual(actor.actor_id, str(self.superuser.pk))

    def test_inactive_superuser_is_denied(self) -> None:
        principal = authenticate(username=self.superuser.username, password=self.password)
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)

        for capability in Capability:
            with self.subTest(capability=capability):
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)

    def test_unknown_or_unconverted_capabilities_are_denied_even_for_superuser(self) -> None:
        principal = authenticate(username=self.superuser.username, password=self.password)
        for capability in ("catalog.unknown", "catalog.read_product", None, [], {}):
            with self.subTest(capability=capability):
                with self.assertRaises(AccessDenied):
                    require_capability(principal=principal, capability=capability)

    def test_denies_deleted_user_after_an_authorization(self) -> None:
        self.grant("add_variant")
        principal = self.authenticated_principal()
        require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        get_user_model().objects.filter(pk=principal.pk).delete()

        with self.assertRaises(AuthenticationRequired):
            require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)

    def test_denies_deactivated_user_after_an_authorization(self) -> None:
        self.grant("add_variant")
        principal = self.authenticated_principal()
        require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        get_user_model().objects.filter(pk=principal.pk).update(is_active=False)

        with self.assertRaises(AccessDenied):
            require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)

    def test_ignores_original_permission_cache_after_direct_permission_revocation(self) -> None:
        self.grant("add_variant")
        principal = self.authenticated_principal()
        self.assertTrue(principal.has_perm("catalog.add_variant"))
        require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        self.user.user_permissions.clear()

        with self.assertRaises(AccessDenied):
            require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)

    def test_detects_group_permission_revocation(self) -> None:
        group = Group.objects.create(name="fictional-test-group")
        group.permissions.add(self.permissions["add_variant"])
        self.user.groups.add(group)
        principal = self.authenticated_principal()
        self.assertTrue(principal.has_perm("catalog.add_variant"))
        require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        group.permissions.clear()

        with self.assertRaises(AccessDenied):
            require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)

    def test_actor_is_immutable_and_contains_only_a_stable_identifier(self) -> None:
        self.grant("add_variant")
        principal = self.authenticated_principal()
        actor = require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        self.user.username = "renamed-fictional-operator"
        self.user.save(update_fields=["username"])

        self.assertEqual(
            actor,
            require_capability(principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT),
        )
        self.assertEqual([field.name for field in fields(actor)], ["actor_id"])
        self.assertIsInstance(actor.actor_id, str)
        with self.assertRaises(FrozenInstanceError):
            actor.actor_id = "different"

    def test_exceptions_have_generic_messages(self) -> None:
        with self.assertRaises(AuthenticationRequired) as missing_principal:
            require_capability(
                principal={"username": self.user.username, "password": self.password},
                capability=Capability.READ_PRODUCT,
            )
        self.assertEqual(str(missing_principal.exception), "Authentication required")

        with self.assertRaises(AccessDenied) as denied:
            require_capability(
                principal=self.authenticated_principal(), capability=Capability.READ_PRODUCT
            )
        self.assertEqual(str(denied.exception), "Access denied")

    def test_database_failure_propagates_without_authorizing(self) -> None:
        principal = self.authenticated_principal()
        with patch.object(
            get_user_model()._default_manager, "get", side_effect=DatabaseError("unavailable")
        ):
            with self.assertRaises(DatabaseError):
                require_capability(principal=principal, capability=Capability.READ_PRODUCT)
