from django.core import mail
from django.core.cache import cache
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from users.models import MasterKeyEnvelope
from users.services import make_verification_token

User = get_user_model()

REGISTER_URL = "/api/auth/register/"
STRONG_PW = "Str0ng!Vault#Key2026"


class RegistrationSecurityTests(APITestCase):
    """The vault password derives the KEK — these lock in the strength policy."""

    def setUp(self):
        cache.clear()  # reset throttle counters between tests

    def test_short_password_rejected(self):
        res = self.client.post(
            REGISTER_URL, {"email": "a@example.com", "password": "Ab1!xy", "accepted_privacy_policy": True}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="a@example.com").exists())

    def test_all_numeric_password_rejected(self):
        # 12 chars, passes length, but NumericPasswordValidator must reject it
        res = self.client.post(
            REGISTER_URL, {"email": "b@example.com", "password": "123456789012", "accepted_privacy_policy": True}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_common_password_rejected(self):
        res = self.client.post(
            REGISTER_URL, {"email": "c@example.com", "password": "password1234", "accepted_privacy_policy": True}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_strong_password_accepted(self):
        res = self.client.post(
            REGISTER_URL, {"email": "alice@example.com", "password": STRONG_PW, "accepted_privacy_policy": True}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(email="alice@example.com").exists())

    def test_duplicate_email_rejected(self):
        first = self.client.post(
            REGISTER_URL, {"email": "dup@example.com", "password": STRONG_PW, "accepted_privacy_policy": True}, format="json"
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post(
            REGISTER_URL, {"email": "dup@example.com", "password": STRONG_PW, "accepted_privacy_policy": True}, format="json"
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.filter(email="dup@example.com").count(), 1)


class LoginCaseInsensitivityTests(APITestCase):
    """Registration lowercases the email (RegisterSerializer.validate_email);
    login must match the same normalisation, or anyone who registers as
    Tuesday@gmail.com and later types it the same way can't log back in."""

    TOKEN = "/api/auth/token/"

    def setUp(self):
        cache.clear()
        User.objects.create_user(
            username="casefold@example.com", email="casefold@example.com",
            password="Str0ng!Vault#Key2026",
        )

    def test_login_with_different_case_succeeds(self):
        res = self.client.post(
            self.TOKEN, {"username": "CaseFold@Example.com", "password": "Str0ng!Vault#Key2026"}, format="json"
        )
        self.assertEqual(res.status_code, 200)

    def test_login_with_original_lowercase_still_works(self):
        res = self.client.post(
            self.TOKEN, {"username": "casefold@example.com", "password": "Str0ng!Vault#Key2026"}, format="json"
        )
        self.assertEqual(res.status_code, 200)

    def test_wrong_password_still_rejected_regardless_of_case(self):
        res = self.client.post(
            self.TOKEN, {"username": "CASEFOLD@EXAMPLE.COM", "password": "wrong"}, format="json"
        )
        self.assertEqual(res.status_code, 401)


class PrivacyPolicyConsentTests(APITestCase):
    """Required, not just recorded -- see RegisterSerializer.validate_accepted_privacy_policy."""

    def setUp(self):
        cache.clear()

    def test_registration_rejected_without_acceptance(self):
        res = self.client.post(
            REGISTER_URL, {"email": "noconsent@example.com", "password": STRONG_PW}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="noconsent@example.com").exists())

    def test_registration_rejected_when_acceptance_is_false(self):
        res = self.client.post(
            REGISTER_URL,
            {"email": "falseconsent@example.com", "password": STRONG_PW, "accepted_privacy_policy": False},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="falseconsent@example.com").exists())

    def test_acceptance_is_recorded_with_timestamp_and_version(self):
        from django.conf import settings as dj_settings

        res = self.client.post(
            REGISTER_URL,
            {"email": "consenting@example.com", "password": STRONG_PW, "accepted_privacy_policy": True},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="consenting@example.com")
        self.assertIsNotNone(user.privacy_policy_accepted_at)
        self.assertEqual(user.privacy_policy_version, dj_settings.PRIVACY_POLICY_VERSION)


class RateLimitTests(APITestCase):
    """Brute-force protection: the register scope must start returning 429."""

    def setUp(self):
        cache.clear()

    def test_register_endpoint_is_throttled(self):
        statuses = []
        for i in range(12):
            res = self.client.post(
                REGISTER_URL,
                {"email": f"user{i}@example.com", "password": STRONG_PW, "accepted_privacy_policy": True},
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


class KdfParameterValidationTests(APITestCase):
    """The server never runs Argon2 itself, but it stores these parameters
    and hands them back to the client on every future unlock -- a weak
    value here means the server faithfully replays a trivially
    brute-forceable KDF forever after. See MasterKeySetupSerializer."""

    REGISTER = "/api/auth/register/"
    SETUP = "/api/auth/master-key/setup/"
    TOKEN = "/api/auth/token/"
    PW = "Str0ng!Vault#Key2026"

    BASE_ENV = {
        "enc_master_key": "ab" * 48,
        "enc_master_key_nonce": "cd" * 24,
        "kdf_salt": "ef" * 16,
    }

    def setUp(self):
        cache.clear()
        self.email = "kdftest@example.com"
        self.client.post(self.REGISTER, {"email": self.email, "password": self.PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        res = self.client.post(self.TOKEN, {"username": self.email, "password": self.PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")

    def _setup(self, memory_kb=65536, iterations=3, parallelism=1):
        return self.client.post(self.SETUP, {
            **self.BASE_ENV,
            "kdf_memory_kb": memory_kb, "kdf_iterations": iterations, "kdf_parallelism": parallelism,
        }, format="json")

    def test_normal_parameters_accepted(self):
        res = self._setup()
        self.assertEqual(res.status_code, 201, res.content)

    def test_near_zero_memory_rejected(self):
        res = self._setup(memory_kb=1)
        self.assertEqual(res.status_code, 400)

    def test_near_zero_iterations_rejected(self):
        res = self._setup(iterations=1)
        self.assertEqual(res.status_code, 400)

    def test_excessive_parallelism_rejected(self):
        res = self._setup(parallelism=999)
        self.assertEqual(res.status_code, 400)


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
        self.client.post(self.REGISTER, {"email": self.email, "password": self.PW, "accepted_privacy_policy": True}, format="json")
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
        self.client.post(self.REGISTER, {"email": "norec@example.com", "password": self.PW, "accepted_privacy_policy": True}, format="json")
        r = self.client.post(self.TOKEN, {"username": "norec@example.com", "password": self.PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
        res = self.client.post(self.SETUP, self.ENV, format="json")
        self.assertEqual(res.status_code, 201, res.content)


class LoginKdfParamsTests(APITestCase):
    """2026-08-31 fix: the client must be able to fetch KDF params BEFORE
    authenticating, to derive the KEK -- and from it, the login_auth_key it
    actually authenticates with -- locally. See LoginKdfParamsView."""

    REGISTER = "/api/auth/register/"
    SETUP = "/api/auth/master-key/setup/"
    TOKEN = "/api/auth/token/"
    KDF_PARAMS = "/api/auth/login-kdf-params/"
    PW = "Str0ng!Vault#Key2026"

    ENV = {
        "enc_master_key": "ab" * 48,
        "enc_master_key_nonce": "cd" * 24,
        "kdf_salt": "ef" * 16,
        "kdf_memory_kb": 65536, "kdf_iterations": 3, "kdf_parallelism": 1,
    }

    def setUp(self):
        cache.clear()
        self.email = "kdfparams@example.com"
        self.client.post(self.REGISTER, {"email": self.email, "password": self.PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        res = self.client.post(self.TOKEN, {"username": self.email, "password": self.PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")
        self.client.post(self.SETUP, self.ENV, format="json")
        self.client.credentials()  # log out -- this endpoint must work unauthenticated

    def test_returns_kdf_params_for_existing_account(self):
        cache.clear()
        res = self.client.post(self.KDF_PARAMS, {"username": self.email}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["kdf_salt_hex"], self.ENV["kdf_salt"])
        self.assertEqual(body["kdf_memory_kb"], self.ENV["kdf_memory_kb"])
        self.assertEqual(body["kdf_iterations"], self.ENV["kdf_iterations"])
        self.assertEqual(body["kdf_parallelism"], self.ENV["kdf_parallelism"])

    def test_never_returns_the_encrypted_master_key(self):
        """The whole point: KDF params alone reveal nothing about the vault."""
        cache.clear()
        res = self.client.post(self.KDF_PARAMS, {"username": self.email}, format="json")
        body = res.json()
        self.assertNotIn("encrypted_master_key_hex", body)
        self.assertNotIn("enc_master_key", body)
        self.assertNotIn("nonce_hex", body)

    def test_unknown_email_returns_404_not_500(self):
        cache.clear()
        res = self.client.post(self.KDF_PARAMS, {"username": "nobody@example.com"}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_accepts_email_field_name_too(self):
        """The client sends 'username' (matching the token endpoint's field
        name), but 'email' is accepted too so this isn't fragile to which
        key a caller happens to use."""
        cache.clear()
        res = self.client.post(self.KDF_PARAMS, {"email": self.email}, format="json")
        self.assertEqual(res.status_code, 200, res.content)

    def test_works_while_logged_out(self):
        """Not authenticated at all in this test (setUp already logged out) --
        this is the entire reason the endpoint exists."""
        cache.clear()
        res = self.client.post(self.KDF_PARAMS, {"username": self.email}, format="json")
        self.assertEqual(res.status_code, 200)


class LoginAuthKeySeparationTests(APITestCase):
    """The headline guarantee of the 2026-08-31 fix: whatever string the
    client sends as 'password' is opaque to the backend -- it's checked
    exactly like a real password, with no way to distinguish a raw password
    from a derived login_auth_key. That opacity is what makes the whole
    client-side fix possible without any backend auth-mechanism change."""

    REGISTER = "/api/auth/register/"
    TOKEN = "/api/auth/token/"

    def setUp(self):
        cache.clear()

    def test_a_high_entropy_derived_value_works_exactly_like_a_password(self):
        # Simulates what the client now actually sends: not a human-chosen
        # password, but a 32-byte HKDF output, hex-encoded (64 hex chars).
        derived_value = "3f9a7b2c" * 8
        email = "derived@example.com"

        res = self.client.post(
            self.REGISTER,
            {"email": email, "password": derived_value, "accepted_privacy_policy": True},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)

        cache.clear()
        res = self.client.post(self.TOKEN, {"username": email, "password": derived_value}, format="json")
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_stored_hash_is_of_whatever_was_sent_not_a_real_password(self):
        derived_value = "a1b2c3d4" * 8
        email = "derived2@example.com"
        self.client.post(
            self.REGISTER,
            {"email": email, "password": derived_value, "accepted_privacy_policy": True},
            format="json",
        )
        user = User.objects.get(email=email)
        self.assertTrue(user.check_password(derived_value))
        self.assertFalse(user.check_password("some totally different string"))


class CutoverManagementCommandTests(APITestCase):
    """cutover_login_auth_key: the clean-cutover migration for existing
    accounts once the login/KEK-separation fix ships. Recovery-enabled
    accounts must be identified correctly, since that's what determines
    whether an account has a self-service way back in afterward."""

    REGISTER = "/api/auth/register/"
    SETUP = "/api/auth/master-key/setup/"
    TOKEN = "/api/auth/token/"
    PW = "Str0ng!Vault#Key2026"

    ENV = {
        "enc_master_key": "ab" * 48, "enc_master_key_nonce": "cd" * 24,
        "kdf_salt": "ef" * 16, "kdf_memory_kb": 65536, "kdf_iterations": 3, "kdf_parallelism": 1,
    }
    REC = {
        "enc_master_key_recovery": "11" * 48, "enc_master_key_recovery_nonce": "22" * 24,
        "recovery_kdf_salt": "33" * 16, "recovery_kdf_memory_kb": 65536,
        "recovery_kdf_iterations": 3, "recovery_kdf_parallelism": 1,
        "recovery_auth_key": "some-recovery-auth-key",
    }

    def setUp(self):
        cache.clear()

    def _register_with_envelope(self, email, extra=None):
        self.client.post(self.REGISTER, {"email": email, "password": self.PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        res = self.client.post(self.TOKEN, {"username": email, "password": self.PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")
        self.client.post(self.SETUP, {**self.ENV, **(extra or {})}, format="json")
        self.client.credentials()
        cache.clear()

    def test_dry_run_makes_no_changes(self):
        from io import StringIO
        from django.core.management import call_command

        self._register_with_envelope("dryrun@example.com")
        user = User.objects.get(email="dryrun@example.com")
        old_hash = user.password

        call_command("cutover_login_auth_key", "--dry-run", stdout=StringIO())

        user.refresh_from_db()
        self.assertEqual(user.password, old_hash)
        self.assertTrue(user.has_usable_password())
        # And the old password still logs in -- dry-run touched nothing.
        res = self.client.post(self.TOKEN, {"username": "dryrun@example.com", "password": self.PW}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_real_run_invalidates_old_password(self):
        from io import StringIO
        from django.core.management import call_command

        self._register_with_envelope("realrun@example.com")
        call_command("cutover_login_auth_key", stdout=StringIO())

        user = User.objects.get(email="realrun@example.com")
        self.assertFalse(user.has_usable_password())

        res = self.client.post(self.TOKEN, {"username": "realrun@example.com", "password": self.PW}, format="json")
        self.assertEqual(res.status_code, 401)

    def test_account_with_recovery_can_self_serve_a_new_password_after_cutover(self):
        from io import StringIO
        from django.core.management import call_command

        self._register_with_envelope("hasrecovery@example.com", extra=self.REC)
        call_command("cutover_login_auth_key", stdout=StringIO())

        new_pw = "N3w!Str0ng#Vault2027"
        res = self.client.post("/api/auth/recover/", {
            "email": "hasrecovery@example.com",
            "recovery_auth_key": self.REC["recovery_auth_key"],
            "new_password": new_pw,
            **self.ENV,
        }, format="json")
        self.assertEqual(res.status_code, 200, res.content)

        cache.clear()
        res = self.client.post(self.TOKEN, {"username": "hasrecovery@example.com", "password": new_pw}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_command_reports_accounts_without_recovery_separately(self):
        from io import StringIO
        from django.core.management import call_command

        self._register_with_envelope("norecovery@example.com")  # no REC extras
        out = StringIO()
        call_command("cutover_login_auth_key", "--dry-run", stdout=out)
        self.assertIn("norecovery@example.com", out.getvalue())
        self.assertIn("do NOT", out.getvalue())


class EmailVerificationTests(APITestCase):
    """Verification is non-blocking by design: recovery already works without
    email, so an unverified account must still log in and use the vault
    fully. These lock in that design, not just the happy path."""

    REGISTER = "/api/auth/register/"
    TOKEN = "/api/auth/token/"
    RESEND = "/api/auth/resend-verification/"

    PW = "Str0ng!Vault#Key2026"

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def _verify_url(self, token):
        return f"/api/auth/verify-email/{token}/"

    def _auth(self, email, password):
        res = self.client.post(self.TOKEN, {"username": email, "password": password}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")

    def test_register_sends_a_verification_email(self):
        res = self.client.post(self.REGISTER, {"email": "verify1@example.com", "password": self.PW, "accepted_privacy_policy": True}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verify1@example.com", mail.outbox[0].to)
        self.assertIn("/api/auth/verify-email/", mail.outbox[0].body)

    def test_unverified_account_can_still_log_in_and_act(self):
        """The core non-blocking guarantee: nothing about login depends on
        email_verified being true."""
        self.client.post(self.REGISTER, {"email": "unverified@example.com", "password": self.PW, "accepted_privacy_policy": True}, format="json")
        user = User.objects.get(email="unverified@example.com")
        self.assertFalse(user.email_verified)

        cache.clear()
        res = self.client.post(self.TOKEN, {"username": "unverified@example.com", "password": self.PW}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_valid_token_marks_email_verified(self):
        self.client.post(self.REGISTER, {"email": "verify2@example.com", "password": self.PW, "accepted_privacy_policy": True}, format="json")
        user = User.objects.get(email="verify2@example.com")
        token = make_verification_token(user)

        res = self.client.get(self._verify_url(token))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertIsNotNone(user.email_verified_at)

    def test_invalid_token_rejected_without_crashing(self):
        res = self.client.get(self._verify_url("not-a-real-token"))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertContains(res, "Invalid link", status_code=status.HTTP_400_BAD_REQUEST)

    def test_resend_sends_a_second_email_when_unverified(self):
        self.client.post(self.REGISTER, {"email": "resend1@example.com", "password": self.PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        self._auth("resend1@example.com", self.PW)
        mail.outbox = []  # clear the register-time email, isolate the resend

        res = self.client.post(self.RESEND, {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()["status"], "verification_sent")
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_is_a_noop_once_already_verified(self):
        self.client.post(self.REGISTER, {"email": "resend2@example.com", "password": self.PW, "accepted_privacy_policy": True}, format="json")
        user = User.objects.get(email="resend2@example.com")
        token = make_verification_token(user)
        self.client.get(self._verify_url(token))  # verify it first

        cache.clear()
        self._auth("resend2@example.com", self.PW)
        mail.outbox = []

        res = self.client.post(self.RESEND, {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()["status"], "already_verified")
        self.assertEqual(len(mail.outbox), 0)  # no email sent for an already-verified account

    def test_resend_requires_authentication(self):
        res = self.client.post(self.RESEND, {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_endpoint_reflects_verification_state(self):
        self.client.post(self.REGISTER, {"email": "me1@example.com", "password": self.PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        self._auth("me1@example.com", self.PW)

        res = self.client.get("/api/auth/me/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json(), {"email": "me1@example.com", "email_verified": False})

        user = User.objects.get(email="me1@example.com")
        token = make_verification_token(user)
        self.client.get(self._verify_url(token))

        res = self.client.get("/api/auth/me/")
        self.assertTrue(res.json()["email_verified"])

    def test_me_requires_authentication(self):
        res = self.client.get("/api/auth/me/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
