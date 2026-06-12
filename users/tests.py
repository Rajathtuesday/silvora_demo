from django.core.cache import cache
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from users.models import MasterKeyEnvelope

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


class RecoveryFlowTests(APITestCase):
    """Recovery phrase + password change. Envelopes are opaque to the server,
    so these lock in the security logic, not the crypto."""

    REGISTER = "/api/auth/register/"
    SETUP = "/api/auth/master-key/setup/"
    REC_START = "/api/auth/recover/start/"
    RECOVER = "/api/auth/recover/"
    CHANGE_PW = "/api/auth/master-key/change-password/"
    TOKEN = "/api/auth/token/"

    PW = "Str0ng!Vault#Key2026"
    NEW_PW = "N3w!Str0ng#Vault2027"
    AUTH_KEY = "recovery-auth-key-7f3a9c2d"

    ENV = {
        "enc_master_key": "ab" * 48,
        "enc_master_key_nonce": "cd" * 24,
        "kdf_salt": "ef" * 16,
        "kdf_memory_kb": 65536, "kdf_iterations": 3, "kdf_parallelism": 1,
    }
    REC = {
        "enc_master_key_recovery": "11" * 48,
        "enc_master_key_recovery_nonce": "22" * 24,
        "recovery_kdf_salt": "33" * 16,
        "recovery_kdf_memory_kb": 65536, "recovery_kdf_iterations": 3,
        "recovery_kdf_parallelism": 1, "recovery_auth_key": AUTH_KEY,
    }

    def setUp(self):
        cache.clear()
        self.email = "vault@example.com"
        self.client.post(self.REGISTER, {"email": self.email, "password": self.PW}, format="json")
        self.user = User.objects.get(email=self.email)
        self._auth(self.PW)
        res = self.client.post(self.SETUP, {**self.ENV, **self.REC}, format="json")
        assert res.status_code == 201, res.content

    def _auth(self, password):
        res = self.client.post(self.TOKEN, {"username": self.email, "password": password}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")

    def _logout(self):
        self.client.credentials()
        cache.clear()

    def test_setup_stored_recovery(self):
        env = MasterKeyEnvelope.objects.get(user=self.user)
        self.assertIsNotNone(env.recovery_auth_hash)
        self.assertIsNotNone(env.enc_master_key_recovery)

    def test_recover_start_returns_meta(self):
        self._logout()
        res = self.client.post(self.REC_START, {"email": self.email}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("recovery_kdf_salt_hex", res.json())

    def test_recover_with_correct_key_resets_password(self):
        self._logout()
        body = {"email": self.email, "recovery_auth_key": self.AUTH_KEY,
                "new_password": self.NEW_PW, **self.ENV}
        res = self.client.post(self.RECOVER, body, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        cache.clear()
        self.assertEqual(self.client.post(self.TOKEN, {"username": self.email, "password": self.PW}, format="json").status_code, 401)
        self.assertEqual(self.client.post(self.TOKEN, {"username": self.email, "password": self.NEW_PW}, format="json").status_code, 200)

    def test_recover_with_wrong_key_is_rejected(self):
        self._logout()
        body = {"email": self.email, "recovery_auth_key": "WRONG-key",
                "new_password": self.NEW_PW, **self.ENV}
        res = self.client.post(self.RECOVER, body, format="json")
        self.assertEqual(res.status_code, 403)
        cache.clear()  # password must be unchanged
        self.assertEqual(self.client.post(self.TOKEN, {"username": self.email, "password": self.PW}, format="json").status_code, 200)

    def test_change_password_logged_in(self):
        cache.clear()
        res = self.client.post(self.CHANGE_PW, {"new_password": self.NEW_PW, **self.ENV}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        cache.clear()
        self.assertEqual(self.client.post(self.TOKEN, {"username": self.email, "password": self.PW}, format="json").status_code, 401)
        self.assertEqual(self.client.post(self.TOKEN, {"username": self.email, "password": self.NEW_PW}, format="json").status_code, 200)

    def test_setup_without_recovery_is_backward_compatible(self):
        self._logout()
        self.client.post(self.REGISTER, {"email": "norec@example.com", "password": self.PW}, format="json")
        r = self.client.post(self.TOKEN, {"username": "norec@example.com", "password": self.PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
        res = self.client.post(self.SETUP, self.ENV, format="json")
        self.assertEqual(res.status_code, 201, res.content)
