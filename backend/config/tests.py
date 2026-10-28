from django.test import SimpleTestCase
from django.urls import reverse


class HealthTests(SimpleTestCase):
    # SimpleTestCase forbids database access, including ATOMIC_REQUESTS.
    def test_health_is_public_json_without_database_access(self):
        self.assertEqual(reverse("health"), "/api/health/")
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_ignores_invalid_credentials(self):
        response = self.client.get(
            "/api/health/", HTTP_AUTHORIZATION="Bearer invalid-token"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_allows_head_but_rejects_post(self):
        response = self.client.head("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        response = self.client.post("/api/health/")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET, HEAD")
