from unittest.mock import patch

from django.db import DatabaseError, connection, connections
from django.test import TestCase
from rest_framework.test import APIClient


class HealthEndpointTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_liveness_returns_ok_without_database_access(self) -> None:
        with patch.object(connections["default"], "cursor") as cursor:
            response = self.client.get("/api/v1/health/live/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        cursor.assert_not_called()

    def test_runner_uses_the_isolated_test_database(self) -> None:
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")

    def test_readiness_returns_ok_after_database_query(self) -> None:
        response = self.client.get("/api/v1/health/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readiness_hides_database_failure_details(self) -> None:
        with patch.object(connections["default"], "cursor", side_effect=DatabaseError("private")):
            response = self.client.get("/api/v1/health/ready/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
