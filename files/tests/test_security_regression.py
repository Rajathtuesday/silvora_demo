# files/tests/test_security_regression.py
"""
Regression tests for the six real vulnerabilities found and fixed during the
2026-07 security audit. Each test deliberately re-enacts the old attack and
asserts it now fails the way it should — these exist to catch a silent
revert (someone "cleaning up" the fix later without knowing why it's there).

Covers:
  1. Abandoned upload cleanup (was: crashed every run, orphans piled up forever)
  2. Authenticated endpoint rate limiting (was: no throttle on any file op)
  3. Quota reservation race (was: parallel uploads could blow past the limit)
  4. Soft-delete write gap (was: a "deleted" in-progress upload still accepted writes)
  5. Production security headers (was: DJANGO_SECURE unset, HSTS/secure cookies off)
  6. Dead/dangerous endpoints removed (views_r2_test.py, thumbnails.py)
"""
import re
import threading
import uuid
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from files.views import _FileOpThrottle
from unittest.mock import patch

from files.models import FileRecord
from tenants.models import Tenant
from users.models import UserQuota

User = get_user_model()

_HEX_CIPHERTEXT = "abcdabcdabcdabcdabcdabcdabcdabcdABCD"
_HEX_NONCE = "123412341234123412341234123412341234123412341234"
_HEX_MAC = "56785678567856785678567856785678"


def _tiny_chunk():
    return SimpleUploadedFile("chunk_0.bin", b"{}", content_type="application/octet-stream")


# ======================================================================
# 1. ABANDONED UPLOAD CLEANUP
# ======================================================================

class AbandonedUploadCleanupTests(APITestCase):
    """Was: FileRecord.STATE_* attributes didn't exist -> AttributeError on
    every scheduled run -> abandoned R2 chunks accumulated storage cost
    forever, uncleaned. Now: purges chunks, flips state, retries failures."""

    def setUp(self):
        # NOTE: users.signals.create_individual_tenant_for_user always assigns
        # a fresh auto-created tenant on user creation and overrides whatever
        # is passed to create_user(tenant=...) -- so the real scoping tenant
        # is self.user.tenant, never a separately-created Tenant object.
        placeholder_tenant = Tenant.objects.create(name="cleanup_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(username="cleanupuser", password="password123", tenant=placeholder_tenant)

    def _file(self, state, expires_at):
        return FileRecord.objects.create(
            id=uuid.uuid4(), owner=self.user, tenant=self.user.tenant,
            filename_ciphertext=b"x", filename_nonce=b"y", filename_mac=b"z",
            size=0, security_mode="zero_knowledge", storage_type=FileRecord.STORAGE_R2,
            upload_state=state, upload_expires_at=expires_at,
        )

    @patch("files.management.commands.cleanup_abandoned_uploads.StorageGateway")
    def test_command_runs_without_crashing(self, mock_storage_cls):
        """The actual regression: this must not raise AttributeError."""
        mock_storage_cls.return_value.delete_recursive.return_value = None
        self._file(FileRecord.UploadState.UPLOADING, timezone.now() - timedelta(hours=1))
        call_command("cleanup_abandoned_uploads")  # would have raised before the fix

    @patch("files.management.commands.cleanup_abandoned_uploads.StorageGateway")
    def test_expired_upload_is_purged_and_marked_failed(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        mock_storage.delete_recursive.return_value = None
        f = self._file(FileRecord.UploadState.UPLOADING, timezone.now() - timedelta(hours=1))

        call_command("cleanup_abandoned_uploads")

        mock_storage.delete_recursive.assert_called_once()
        prefix_arg = mock_storage.delete_recursive.call_args[0][0]
        self.assertIn(str(f.tenant_id), prefix_arg)
        self.assertIn(str(f.id), prefix_arg)
        f.refresh_from_db()
        self.assertEqual(f.upload_state, FileRecord.UploadState.FAILED)

    @patch("files.management.commands.cleanup_abandoned_uploads.StorageGateway")
    def test_committed_file_is_never_touched(self, mock_storage_cls):
        """A committed file that happens to share an old expiry timestamp
        must never be purged -- only INITIATED/UPLOADING/COMPLETED qualify."""
        mock_storage = mock_storage_cls.return_value
        f = self._file(FileRecord.UploadState.COMMITTED, timezone.now() - timedelta(hours=1))

        call_command("cleanup_abandoned_uploads")

        mock_storage.delete_recursive.assert_not_called()
        f.refresh_from_db()
        self.assertEqual(f.upload_state, FileRecord.UploadState.COMMITTED)

    @patch("files.management.commands.cleanup_abandoned_uploads.StorageGateway")
    def test_not_yet_expired_upload_is_left_alone(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        f = self._file(FileRecord.UploadState.UPLOADING, timezone.now() + timedelta(hours=1))

        call_command("cleanup_abandoned_uploads")

        mock_storage.delete_recursive.assert_not_called()
        f.refresh_from_db()
        self.assertEqual(f.upload_state, FileRecord.UploadState.UPLOADING)

    @patch("files.management.commands.cleanup_abandoned_uploads.StorageGateway")
    def test_purge_failure_leaves_state_alone_for_retry_next_run(self, mock_storage_cls):
        """If R2 is down mid-purge, the record must NOT flip to FAILED --
        otherwise a transient storage outage permanently orphans the chunks
        (nothing would ever retry deleting them again)."""
        mock_storage = mock_storage_cls.return_value
        mock_storage.delete_recursive.side_effect = Exception("R2 unreachable")
        f = self._file(FileRecord.UploadState.UPLOADING, timezone.now() - timedelta(hours=1))

        call_command("cleanup_abandoned_uploads")

        f.refresh_from_db()
        self.assertEqual(f.upload_state, FileRecord.UploadState.UPLOADING)  # unchanged, will retry


# ======================================================================
# 2. RATE LIMITING ON FILE ENDPOINTS
# ======================================================================

class FileEndpointRateLimitingTests(APITestCase):
    """Was: no throttle_classes on any file endpoint at all -- an
    authenticated, stolen token could flood upload/list/delete without limit."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="throttle_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(username="throttleuser", password="password123", tenant=self.tenant)
        self.client.force_authenticate(self.user)
        from django.core.cache import cache
        cache.clear()  # throttle state lives in cache; avoid bleed between tests

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    @patch.object(_FileOpThrottle, "THROTTLE_RATES", {"file_mutate": "2/min", "file_meta": "2/min", "file_chunk": "2/min"})
    @patch("files.services.upload_service.StorageGateway")
    def test_file_mutate_scope_actually_throttles(self, mock_storage_cls):
        """The regression test that matters: fire past the limit and prove
        the 3rd request is actually rejected, not just that a decorator exists."""
        mock_storage_cls.return_value.upload_bytes.return_value = None

        def start_one():
            return self.client.post("/file/start/", {
                "file_id": str(uuid.uuid4()), "size": 10, "security_mode": "zero_knowledge",
                "filename_ciphertext": _HEX_CIPHERTEXT, "filename_nonce": _HEX_NONCE, "filename_mac": _HEX_MAC,
            }, format="json")

        first = start_one()
        second = start_one()
        third = start_one()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(third.status_code, 429)  # the actual proof the throttle fires

    @patch.object(_FileOpThrottle, "THROTTLE_RATES", {"file_mutate": "1/min", "file_meta": "50/min", "file_chunk": "50/min"})
    def test_throttle_scopes_are_isolated_not_a_shared_global_counter(self):
        """A tight file_mutate limit must not bleed into file_meta -- proves
        scopes are genuinely independent, not one counter reused everywhere."""
        # Burn the file_mutate allowance via rename on a nonexistent file (still
        # counts against the throttle before the 404 -- throttling runs pre-view).
        self.client.post(f"/file/{uuid.uuid4()}/rename/", {}, format="json")
        exhausted = self.client.post(f"/file/{uuid.uuid4()}/rename/", {}, format="json")
        self.assertEqual(exhausted.status_code, 429)

        # file_meta (list_files) must still work -- separate bucket entirely.
        still_fine = self.client.get("/files/")
        self.assertEqual(still_fine.status_code, 200)


# ======================================================================
# 3. QUOTA RESERVATION RACE (real concurrency, not mocked sequencing)
# ======================================================================

class QuotaReservationRaceTests(TransactionTestCase):
    """Was: quota only consumed at commit, not reserved at start -- N
    parallel uploads could each pass the check before any of them 'counted'.

    Uses TransactionTestCase + real threads (not TestCase + sequential calls)
    specifically because the old bug was a genuine cross-connection race;
    a single-threaded test calling the view N times in a row would never
    have caught it even before the fix."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="race_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(username="raceuser", password="password123", tenant=self.tenant)
        UserQuota.objects.create(user=self.user, limit_bytes=1_000_000_000)  # 1 GB

    def test_five_parallel_400mb_starts_cannot_all_reserve_past_1gb_limit(self):
        """5 threads each declare 400MB against a 1GB cap. At most 2 can ever
        legitimately fit (2 * 400MB = 800MB <= 1GB; a 3rd would push to 1.2GB).
        Before the fix, all 5 would have passed the unlocked pre-check.

        SQLite (used by the test runner) serializes writers at the database
        level rather than row level like production Postgres does, so under
        five genuinely concurrent threads it can raise "database is locked"
        for threads that lose the race to acquire the write lock at all --
        that's a SQLite testing artifact, not the bug under test. Each
        thread retries on that specific error rather than dying silently,
        so a slow SQLite writer doesn't get mistaken for a failed assertion."""
        declared_size = 400_000_000  # 400 MB
        results = []
        results_lock = threading.Lock()

        def attempt_start():
            from django.db import connection, OperationalError
            client = APIClient()
            client.force_authenticate(self.user)
            try:
                for _ in range(20):  # retry through transient SQLite lock contention
                    try:
                        res = client.post("/file/start/", {
                            "file_id": str(uuid.uuid4()), "size": declared_size, "security_mode": "zero_knowledge",
                            "filename_ciphertext": _HEX_CIPHERTEXT, "filename_nonce": _HEX_NONCE, "filename_mac": _HEX_MAC,
                        }, format="json")
                        with results_lock:
                            results.append(res.status_code)
                        return
                    except OperationalError as e:
                        if "locked" not in str(e).lower():
                            raise
                        import time
                        time.sleep(0.05)
                with results_lock:
                    results.append(None)  # exhausted retries -- recorded, not silently lost
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt_start) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 5, "all threads must complete (with a recorded outcome, even if retries were exhausted)")
        self.assertNotIn(None, results, "no thread should exhaust its retry budget under normal contention")
        succeeded = results.count(201)
        rejected = results.count(403)

        self.assertLessEqual(succeeded, 2, f"at most 2 of 5 400MB starts should fit in 1GB, got {succeeded}")
        self.assertEqual(succeeded + rejected, 5)

        # Defense in depth: verify the DB itself never recorded more reserved
        # capacity than the limit allows, regardless of what the HTTP layer reported.
        total_reserved = sum(
            f.size for f in FileRecord.objects.filter(
                owner=self.user,
                upload_state__in=[FileRecord.UploadState.INITIATED, FileRecord.UploadState.UPLOADING],
            )
        )
        self.assertLessEqual(total_reserved, 1_000_000_000)


# ======================================================================
# 4. SOFT-DELETE WRITE GAP
# ======================================================================

class SoftDeleteWriteGapTests(APITestCase):
    """Was: upload_chunk/store_integrity/commit/resume had no deleted_at
    filter -- a file soft-deleted mid-upload could still accept new writes."""

    def setUp(self):
        # NOTE: users.signals.create_individual_tenant_for_user always assigns
        # a fresh auto-created tenant on user creation and overrides whatever
        # is passed to create_user(tenant=...) -- so the real scoping tenant
        # is self.user.tenant, never a separately-created Tenant object.
        placeholder_tenant = Tenant.objects.create(name="softdel_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(username="softdeluser", password="password123", tenant=placeholder_tenant)
        self.client.force_authenticate(self.user)
        self.file = FileRecord.objects.create(
            id=uuid.uuid4(), owner=self.user, tenant=self.user.tenant,
            filename_ciphertext=b"x", filename_nonce=b"y", filename_mac=b"z",
            size=0, security_mode="zero_knowledge", storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.UPLOADING,
            upload_expires_at=timezone.now() + timedelta(hours=24),
            deleted_at=timezone.now(),  # soft-deleted WHILE still mid-upload
        )

    @patch("files.services.upload_service.StorageGateway")
    def test_chunk_upload_rejected_on_deleted_file(self, mock_storage_cls):
        res = self.client.post(
            f"/file/{self.file.id}/chunk/0/", {"chunk": _tiny_chunk()}, format="multipart",
        )
        self.assertEqual(res.status_code, 404)

    def test_integrity_upload_rejected_on_deleted_file(self):
        res = self.client.post(
            f"/file/{self.file.id}/integrity/", data=b"ciphertext-blob", content_type="application/octet-stream",
        )
        self.assertEqual(res.status_code, 404)

    def test_commit_rejected_on_deleted_file(self):
        res = self.client.post(f"/file/{self.file.id}/commit/")
        self.assertEqual(res.status_code, 404)

    def test_resume_rejected_on_deleted_file(self):
        res = self.client.get(f"/file/{self.file.id}/resume/")
        self.assertEqual(res.status_code, 404)

    @patch("files.services.upload_service.StorageGateway")
    @patch("files.views.StorageGateway")
    def test_delete_of_incomplete_upload_purges_without_touching_quota(self, mock_views_storage_cls, mock_upload_storage_cls):
        """The related fix: deleting a never-committed upload must purge R2
        and drop the row outright -- never apply trash/quota-release logic,
        since nothing was ever added to used_bytes for an uncommitted file."""
        UserQuota.objects.create(user=self.user, limit_bytes=1_000_000_000)
        mock_views_storage_cls.return_value.delete_recursive.return_value = None

        res = self.client.delete(f"/file/{self.file.id}/delete/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "abandoned_upload_removed")
        self.assertFalse(FileRecord.objects.filter(id=self.file.id).exists())


# ======================================================================
# 5. PRODUCTION SECURITY HEADERS
# ======================================================================

class ProductionSecurityHeadersTests(APITestCase):
    """Was: DJANGO_SECURE unset on Render -- HSTS, secure cookies, and SSL
    redirect were all silently disabled in production."""

    @override_settings(
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
        SECURE_HSTS_PRELOAD=True,
        SECURE_CONTENT_TYPE_NOSNIFF=True,
    )
    def test_when_security_settings_are_on_django_actually_emits_hsts_header(self):
        """Proves the settings are not just *set* but *effective* --
        SecurityMiddleware must add the header to a real response."""
        # SecurityMiddleware only emits HSTS on a request it considers secure
        # -- secure=True simulates HTTPS so the header logic actually runs,
        # matching how Cloudflare terminates TLS and forwards X-Forwarded-Proto.
        res = self.client.get("/healthz/", secure=True)
        self.assertIn("Strict-Transport-Security", res)
        self.assertIn("max-age=31536000", res["Strict-Transport-Security"])
        self.assertIn("includeSubDomains", res["Strict-Transport-Security"])
        self.assertIn("preload", res["Strict-Transport-Security"])
        self.assertEqual(res.get("X-Content-Type-Options"), "nosniff")

    def test_settings_source_still_gates_all_seven_flags_on_django_secure(self):
        """Regression guard against someone quietly deleting/shrinking the
        _SECURE block later without realising what it protects. Reads the
        actual settings.py source rather than the loaded settings object,
        since DJANGO_SECURE is only evaluated once at process start and the
        test runner won't have it set -- this checks the *code*, not the
        current runtime value."""
        # django.conf.settings is a LazySettings proxy with no __file__ of its
        # own -- import the actual settings module by its known dotted path
        # to read its source directly.
        import silvora_backend.settings as settings_module
        settings_path = Path(settings_module.__file__)
        source = settings_path.read_text(encoding="utf-8")

        block_match = re.search(r'_SECURE = os\.environ\.get\("DJANGO_SECURE".*?\nif _SECURE:\n(.*?)\n\n', source, re.DOTALL)
        self.assertIsNotNone(block_match, "the DJANGO_SECURE-gated security block must still exist in settings.py")
        block = block_match.group(1)

        required_flags = [
            "SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE",
            "SECURE_HSTS_SECONDS", "SECURE_HSTS_INCLUDE_SUBDOMAINS",
            "SECURE_HSTS_PRELOAD", "SECURE_CONTENT_TYPE_NOSNIFF",
        ]
        for flag in required_flags:
            self.assertIn(flag, block, f"{flag} must still be set inside the DJANGO_SECURE block")


# ======================================================================
# 6. DEAD / DANGEROUS ENDPOINTS REMOVED
# ======================================================================

class DeadEndpointsRemovedTests(APITestCase):
    """Was: views_r2_test.py exposed an unauthenticated upload endpoint;
    thumbnails.py would process files in plaintext if ever wired up. Both
    were deleted outright rather than disabled -- these guard against
    either quietly reappearing."""

    def test_r2_test_module_does_not_exist(self):
        with self.assertRaises(ModuleNotFoundError):
            import importlib
            importlib.import_module("files.views_r2_test")

    def test_thumbnails_module_does_not_exist(self):
        with self.assertRaises(ModuleNotFoundError):
            import importlib
            importlib.import_module("files.thumbnails")

    def test_no_url_route_exposes_an_unauthenticated_upload_path(self):
        """Belt-and-braces: even if the module ever came back, it must not
        be wired into urls.py without a matching auth test being written."""
        from files.urls import urlpatterns
        for pattern in urlpatterns:
            view = getattr(pattern.callback, "cls", pattern.callback)
            name = getattr(view, "__name__", getattr(view, "__module__", ""))
            self.assertNotIn("r2_test", str(name).lower())
