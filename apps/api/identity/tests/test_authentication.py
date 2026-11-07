from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.test import TestCase


class NativeAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(
            username="fictional-operator", password=cls.password
        )

    def test_authenticates_correct_credentials(self) -> None:
        principal = authenticate(username=self.user.username, password=self.password)

        self.assertIsNotNone(principal)
        self.assertEqual(principal.pk, self.user.pk)

    def test_rejects_incorrect_password(self) -> None:
        self.assertIsNone(authenticate(username=self.user.username, password="incorrect"))

    def test_rejects_nonexistent_user(self) -> None:
        self.assertIsNone(authenticate(username="nonexistent", password=self.password))

    def test_rejects_unusable_password(self) -> None:
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])

        self.assertIsNone(authenticate(username=self.user.username, password=self.password))

    def test_rejects_inactive_user(self) -> None:
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertIsNone(authenticate(username=self.user.username, password=self.password))

    def test_persists_only_a_password_hash(self) -> None:
        stored_user = get_user_model().objects.get(pk=self.user.pk)

        self.assertNotEqual(stored_user.password, self.password)
        self.assertIsNotNone(identify_hasher(stored_user.password))
        self.assertTrue(stored_user.check_password(self.password))
