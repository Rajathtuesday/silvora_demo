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

    def _make_uploading_file(self):
        return FileRecord.objects.create(
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
    def test_commit_succeeds_with_integrity(self, mock_storage_cls, mock_quota):
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = True  # integrity manifest present
        mock_quota.consume.return_value = True

        file = self._make_uploading_file()
        res = self.client.post(f"/file/{file.id}/commit/")

        self.assertEqual(res.status_code, 200)
        file.refresh_from_db()
        self.assertEqual(file.upload_state, FileRecord.UploadState.COMMITTED)


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
    def test_missing_integrity_is_404(self, mock_storage_cls):
        storage = mock_storage_cls.return_value
        storage.exists.return_value = False

        self.client.force_authenticate(self.alice)
        res = self.client.get(f"/download/file/{self.file.id}/integrity/")
        self.assertEqual(res.status_code, 404)

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
