from django.core.cache import cache
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

User = get_user_model()

REGISTER_URL = "/api/auth/register/"
STRONG_PW = "Str0ng!Vault#Key2026"


class RegistrationSecurityTests(APITestCase):
    """The vault password derives the KEK — these lock in the strength policy."""

    def setUp(self):
        cache.clear()  # reset throttle counters between tests

    def test_short_password_rejected(self):
        res = self.client.post(
            REGISTER_URL, {"email": "a@example.com", "password": "Ab1!xy"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="a@example.com").exists())

    def test_all_numeric_password_rejected(self):
        # 12 chars, passes length, but NumericPasswordValidator must reject it
        res = self.client.post(
            REGISTER_URL, {"email": "b@example.com", "password": "123456789012"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_common_password_rejected(self):
        res = self.client.post(
            REGISTER_URL, {"email": "c@example.com", "password": "password1234"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_strong_password_accepted(self):
        res = self.client.post(
            REGISTER_URL, {"email": "alice@example.com", "password": STRONG_PW}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(email="alice@example.com").exists())

    def test_duplicate_email_rejected(self):
        first = self.client.post(
            REGISTER_URL, {"email": "dup@example.com", "password": STRONG_PW}, format="json"
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post(
            REGISTER_URL, {"email": "dup@example.com", "password": STRONG_PW}, format="json"
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.filter(email="dup@example.com").count(), 1)


class RateLimitTests(APITestCase):
    """Brute-force protection: the register scope must start returning 429."""

    def setUp(self):
        cache.clear()

    def test_register_endpoint_is_throttled(self):
        statuses = []
        for i in range(12):
            res = self.client.post(
                REGISTER_URL,
                {"email": f"user{i}@example.com", "password": STRONG_PW},
                format="json",
            )
            statuses.append(res.status_code)
            if res.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                break

        self.assertIn(
            status.HTTP_429_TOO_MANY_REQUESTS, statuses,
            msg=f"Expected throttling to kick in; got statuses {statuses}",
        )
        # And at least some legitimate registrations succeeded before the limit
        self.assertIn(status.HTTP_201_CREATED, statuses)
