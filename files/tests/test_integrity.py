import hashlib
import uuid
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from unittest.mock import patch

from files.models import FileRecord
from tenants.models import Tenant
from files.services.quota_service import QuotaService

User = get_user_model()


class IntegrityUploadTests(APITestCase):
    """The encrypted integrity manifest is stored opaquely and gates commit."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="itg_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL
        )
        self.user = User.objects.create_user(
            username="itguser", password="password123", tenant=self.tenant
        )
        self.client.force_authenticate(self.user)

    def _make_uploading_file(self, with_integrity_hash=False):
        file = FileRecord.objects.create(
            id=uuid.uuid4(),
            owner=self.user,
            tenant=self.user.tenant,
            filename_ciphertext=b"abc",
            filename_nonce=b"123",
            filename_mac=b"456",
            size=0,
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.UPLOADING,
        )
        if with_integrity_hash:
            # Simulates a real store_integrity() call having already run --
            # these tests mock storage.exists() directly instead of going
            # through the real upload_integrity endpoint, so this fills in
            # the fingerprint bookkeeping that call would otherwise leave
            # behind and that commit() now requires alongside it.
            file.integrity_generation = 1
            file.integrity_sha256 = "0" * 64
            file.save(update_fields=["integrity_generation", "integrity_sha256"])
        return file

    @patch("files.services.upload_service.StorageGateway")
    def test_store_integrity_writes_opaque_blob(self, mock_storage_cls):
        storage = mock_storage_cls.return_value
        file = self._make_uploading_file()

        blob = b"\x01\x02\x03 opaque-aead-envelope"
        res = self.client.post(
            f"/file/{file.id}/integrity/",
            data=blob,
            content_type="application/octet-stream",
        )

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["stored"])
        # Stored under the file's integrity key, byte-for-byte (server never reads it).
        args, _ = storage.upload_bytes.call_args
        self.assertEqual(args[0], blob)
        self.assertTrue(args[1].endswith("/integrity.bin"))

    @patch("files.services.upload_service.StorageGateway")
    def test_store_integrity_records_fingerprint_and_generation(self, mock_storage_cls):
        file = self._make_uploading_file()
        blob = b"\x01\x02\x03 opaque-aead-envelope"

        self.client.post(
            f"/file/{file.id}/integrity/",
            data=blob,
            content_type="application/octet-stream",
        )

        file.refresh_from_db()
        self.assertEqual(file.integrity_generation, 1)
        self.assertEqual(file.integrity_sha256, hashlib.sha256(blob).hexdigest())

    @patch("files.services.upload_service.StorageGateway")
    def test_store_integrity_again_bumps_generation_and_updates_fingerprint(self, mock_storage_cls):
        # A client may legitimately (re)send the integrity manifest more than
        # once before commit (retry after a dropped connection, or the user
        # picks a different file before ever committing). Each such call is
        # from the same authenticated owner, so it's accepted and simply
        # overwrites the bookkeeping -- but the counter must still climb, and
        # the fingerprint must always reflect the LATEST bytes actually
        # written, not the first.
        file = self._make_uploading_file()
        first = b"first-attempt-blob"
        second = b"second-attempt-blob-different-content"

        self.client.post(
            f"/file/{file.id}/integrity/", data=first, content_type="application/octet-stream",
        )
        self.client.post(
            f"/file/{file.id}/integrity/", data=second, content_type="application/octet-stream",
        )

        file.refresh_from_db()
        self.assertEqual(file.integrity_generation, 2)
        self.assertEqual(file.integrity_sha256, hashlib.sha256(second).hexdigest())

    @patch("files.services.upload_service.StorageGateway")
    def test_empty_integrity_rejected(self, mock_storage_cls):
        file = self._make_uploading_file()
        res = self.client.post(
            f"/file/{file.id}/integrity/",
            data=b"",
            content_type="application/octet-stream",
        )
        self.assertEqual(res.status_code, 400)

    @patch("files.services.upload_service.StorageGateway")
    def test_integrity_rejected_after_commit(self, mock_storage_cls):
        file = self._make_uploading_file()
        file.upload_state = FileRecord.UploadState.COMMITTED
        file.save(update_fields=["upload_state"])

        res = self.client.post(
            f"/file/{file.id}/integrity/",
            data=b"late",
            content_type="application/octet-stream",
        )
        self.assertEqual(res.status_code, 400)

    @patch("files.services.upload_service.QuotaService")
    @patch("files.services.upload_service.StorageGateway")
    def test_commit_requires_integrity(self, mock_storage_cls, mock_quota):
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = False  # no integrity manifest present
        mock_quota.consume.return_value = True

        file = self._make_uploading_file()
        res = self.client.post(f"/file/{file.id}/commit/")

        self.assertEqual(res.status_code, 400)
        self.assertIn("Integrity", res.json()["error"])
        file.refresh_from_db()
        self.assertNotEqual(file.upload_state, FileRecord.UploadState.COMMITTED)

    @patch("files.services.upload_service.QuotaService")
    @patch("files.services.upload_service.StorageGateway")
    def test_commit_requires_integrity_fingerprint_even_if_object_exists(self, mock_storage_cls, mock_quota):
        # storage.exists() says the object is there, but the DB never
        # recorded a fingerprint for it (as would happen if store_integrity()
        # was somehow bypassed) -- commit() must not proceed on top of that
        # desync, since integrity_sha256 is what every future download pins
        # against for this file's whole lifetime.
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = True
        mock_quota.consume.return_value = True

        file = self._make_uploading_file()  # with_integrity_hash left False
        res = self.client.post(f"/file/{file.id}/commit/")

        self.assertEqual(res.status_code, 400)
        file.refresh_from_db()
        self.assertNotEqual(file.upload_state, FileRecord.UploadState.COMMITTED)

    @patch("files.services.upload_service.QuotaService")
    @patch("files.services.upload_service.StorageGateway")
    def test_commit_sets_manifest_sha256_matching_uploaded_bytes(self, mock_storage_cls, mock_quota):
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = True
        mock_quota.consume.return_value = True

        file = self._make_uploading_file(with_integrity_hash=True)
        self.client.post(f"/file/{file.id}/commit/")

        # Find the manifest.json write among the upload_bytes calls and
        # confirm the fingerprint recorded on the row matches those exact
        # bytes -- this is the same content download_manifest() will later
        # pin against.
        manifest_call = next(
            call for call in storage.upload_bytes.call_args_list
            if call.args[1].endswith("/manifest.json")
        )
        manifest_bytes = manifest_call.args[0]

        file.refresh_from_db()
        self.assertEqual(file.manifest_sha256, hashlib.sha256(manifest_bytes).hexdigest())

    @patch("files.services.upload_service.QuotaService")
    @patch("files.services.upload_service.StorageGateway")
    def test_commit_succeeds_with_integrity(self, mock_storage_cls, mock_quota):
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = True  # integrity manifest present
        mock_quota.consume.return_value = True

        file = self._make_uploading_file(with_integrity_hash=True)
        res = self.client.post(f"/file/{file.id}/commit/")

        self.assertEqual(res.status_code, 200)
        file.refresh_from_db()
        self.assertEqual(file.upload_state, FileRecord.UploadState.COMMITTED)

    @patch("files.services.upload_service.QuotaService")
    @patch("files.services.upload_service.StorageGateway")
    def test_commit_marks_integrity_established(self, mock_storage_cls, mock_quota):
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = True
        mock_quota.consume.return_value = True

        file = self._make_uploading_file(with_integrity_hash=True)
        self.client.post(f"/file/{file.id}/commit/")

        file.refresh_from_db()
        self.assertTrue(file.integrity_established)

    @patch("files.services.upload_service.QuotaService")
    @patch("files.services.upload_service.StorageGateway")
    def test_failed_commit_does_not_set_integrity_established(self, mock_storage_cls, mock_quota):
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = False  # gate fails
        file = self._make_uploading_file()
        self.client.post(f"/file/{file.id}/commit/")

        file.refresh_from_db()
        self.assertFalse(file.integrity_established)

    @patch("files.services.upload_service.StorageGateway")
    def test_recommitting_already_committed_file_does_not_backfill(self, mock_storage_cls):
        """Guards the no-retroactive-rewriting decision directly."""
        file = self._make_uploading_file()
        file.upload_state = FileRecord.UploadState.COMMITTED
        file.integrity_established = False  # simulates a pre-migration row
        file.save(update_fields=["upload_state", "integrity_established"])

        res = self.client.post(f"/file/{file.id}/commit/")

        self.assertEqual(res.json()["status"], "already_committed")
        file.refresh_from_db()
        self.assertFalse(file.integrity_established)


class IntegrityDownloadTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice_itg", password="password123")
        self.bob = User.objects.create_user(username="bob_itg", password="password123")
        self.file = FileRecord.objects.create(
            id=uuid.uuid4(),
            owner=self.alice,
            tenant=self.alice.tenant,
            filename_ciphertext=b"abc",
            filename_nonce=b"123",
            filename_mac=b"456",
            size=100,
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.COMMITTED,
        )

    @patch("files.views.StorageGateway")
    def test_owner_downloads_integrity(self, mock_storage_cls):
        storage = mock_storage_cls.return_value
        storage.exists.return_value = True
        storage.download_bytes.return_value = b"opaque-blob"

        self.client.force_authenticate(self.alice)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"opaque-blob")

    @patch("files.views.StorageGateway")
    def test_download_integrity_rejects_content_that_does_not_match_fingerprint(self, mock_storage_cls):
        # Simulates a compromised/malicious server (or a storage-only backup
        # restore) substituting different bytes at the same, deterministic
        # key after commit -- storage.exists() alone would happily serve
        # this, which is exactly the gap this fingerprint check closes.
        self.file.integrity_sha256 = hashlib.sha256(b"the-real-committed-blob").hexdigest()
        self.file.save(update_fields=["integrity_sha256"])
        storage = mock_storage_cls.return_value
        storage.exists.return_value = True
        storage.download_bytes.return_value = b"a-different-rolled-back-blob"

        self.client.force_authenticate(self.alice)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")

        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.json()["integrity_established"])

    @patch("files.views.StorageGateway")
    def test_download_integrity_accepts_content_matching_fingerprint(self, mock_storage_cls):
        blob = b"the-real-committed-blob"
        self.file.integrity_sha256 = hashlib.sha256(blob).hexdigest()
        self.file.save(update_fields=["integrity_sha256"])
        storage = mock_storage_cls.return_value
        storage.exists.return_value = True
        storage.download_bytes.return_value = blob

        self.client.force_authenticate(self.alice)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, blob)

    @patch("files.views.StorageGateway")
    def test_download_integrity_with_no_recorded_fingerprint_skips_check(self, mock_storage_cls):
        # Files committed before this field existed have integrity_sha256 =
        # None and are never backfilled (see the field's docstring) -- they
        # must keep today's existence-only behavior rather than suddenly
        # start failing closed on a fingerprint that was never recorded.
        self.assertIsNone(self.file.integrity_sha256)
        storage = mock_storage_cls.return_value
        storage.exists.return_value = True
        storage.download_bytes.return_value = b"whatever-legacy-content"

        self.client.force_authenticate(self.alice)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")

        self.assertEqual(res.status_code, 200)

    @patch("files.views.StorageGateway")
    def test_missing_integrity_is_404(self, mock_storage_cls):
        storage = mock_storage_cls.return_value
        storage.exists.return_value = False

        self.client.force_authenticate(self.alice)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")
        self.assertEqual(res.status_code, 404)

    @patch("files.views.StorageGateway")
    def test_missing_integrity_after_being_established_fails_closed(self, mock_storage_cls):
        self.file.integrity_established = True
        self.file.save(update_fields=["integrity_established"])
        storage = mock_storage_cls.return_value
        storage.exists.return_value = False

        self.client.force_authenticate(self.alice)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")

        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.json()["integrity_established"])

    @patch("files.views.StorageGateway")
    def test_other_tenant_cannot_fetch_integrity(self, mock_storage_cls):
        # Bob must never reach Alice's integrity manifest (no IDOR).
        self.client.force_authenticate(self.bob)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")
        self.assertEqual(res.status_code, 404)

    @patch("files.services.upload_service.StorageGateway")
    def test_other_tenant_cannot_store_integrity(self, mock_storage_cls):
        self.client.force_authenticate(self.bob)
        res = self.client.post(
            f"/file/{self.file.id}/integrity/",
            data=b"evil",
            content_type="application/octet-stream",
        )
        self.assertEqual(res.status_code, 404)


class ManifestDownloadRollbackTests(APITestCase):
    """manifest.json gets the same content-fingerprint pinning as
    integrity.bin (see download_manifest()/download_integrity() in
    files/views.py) -- a compromised server substituting an old manifest at
    the same deterministic key must be caught, not silently served."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice_mft", password="password123")
        self.file = FileRecord.objects.create(
            id=uuid.uuid4(),
            owner=self.alice,
            tenant=self.alice.tenant,
            filename_ciphertext=b"abc",
            filename_nonce=b"123",
            filename_mac=b"456",
            size=100,
            manifest_path="some/manifest.json",
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.COMMITTED,
        )
        self.client.force_authenticate(self.alice)

    @patch("files.views.StorageGateway")
    def test_rejects_content_that_does_not_match_fingerprint(self, mock_storage_cls):
        real_manifest = b'{"v":1,"chunks":[]}'
        self.file.manifest_sha256 = hashlib.sha256(real_manifest).hexdigest()
        self.file.save(update_fields=["manifest_sha256"])
        storage = mock_storage_cls.return_value
        storage.download_bytes.return_value = b'{"v":1,"chunks":["rolled-back"]}'

        res = self.client.get(f"/download/file/{self.file.id}/manifest/")

        self.assertEqual(res.status_code, 409)

    @patch("files.views.StorageGateway")
    def test_accepts_content_matching_fingerprint(self, mock_storage_cls):
        real_manifest = b'{"v":1,"chunks":[]}'
        self.file.manifest_sha256 = hashlib.sha256(real_manifest).hexdigest()
        self.file.save(update_fields=["manifest_sha256"])
        storage = mock_storage_cls.return_value
        storage.download_bytes.return_value = real_manifest

        res = self.client.get(f"/download/file/{self.file.id}/manifest/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, real_manifest)

    @patch("files.views.StorageGateway")
    def test_with_no_recorded_fingerprint_skips_check(self, mock_storage_cls):
        # Pre-existing files (manifest_sha256 never backfilled) keep today's
        # behavior: whatever is at manifest_path is served as-is.
        self.assertIsNone(self.file.manifest_sha256)
        storage = mock_storage_cls.return_value
        storage.download_bytes.return_value = b"legacy-manifest-bytes"

        res = self.client.get(f"/download/file/{self.file.id}/manifest/")

        self.assertEqual(res.status_code, 200)
