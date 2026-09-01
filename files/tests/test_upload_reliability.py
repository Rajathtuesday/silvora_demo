import uuid
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from unittest.mock import patch

from files.models import FileRecord
from tenants.models import Tenant

User = get_user_model()


class UploadReliabilityTests(APITestCase):
    """Resume / commit guarantees that make dropped-network + app-kill safe."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="rel_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL
        )
        self.user = User.objects.create_user(
            username="reluser", password="password123", tenant=self.tenant
        )
        self.client.force_authenticate(self.user)

    def _file(self, state=FileRecord.UploadState.UPLOADING, expires=None):
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
            upload_state=state,
            upload_expires_at=expires or (timezone.now() + timedelta(hours=24)),
        )

    @patch("files.services.upload_service.StorageGateway")
    def test_resume_lists_uploaded_chunks_and_state(self, mock_storage_cls):
        storage = mock_storage_cls.return_value
        storage.list_chunks.return_value = [0, 1, 2]
        file = self._file()

        res = self.client.get(f"/file/{file.id}/resume/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["uploaded_indices"], [0, 1, 2])
        self.assertEqual(body["upload_state"], "uploading")

    @patch("files.services.upload_service.StorageGateway")
    def test_resume_reports_committed(self, mock_storage_cls):
        # The app uses this to finish cleanly if it was killed right after commit.
        storage = mock_storage_cls.return_value
        storage.list_chunks.return_value = [0, 1]
        file = self._file(state=FileRecord.UploadState.COMMITTED)

        res = self.client.get(f"/file/{file.id}/resume/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["upload_state"], "committed")

    @patch("files.services.upload_service.QuotaService")
    @patch("files.services.upload_service.StorageGateway")
    def test_commit_is_idempotent(self, mock_storage_cls, mock_quota):
        storage = mock_storage_cls.return_value
        storage.list_chunk_objects.return_value = [("0", "k0", 100)]
        storage.exists.return_value = True
        mock_quota.consume.return_value = True

        file = self._file()
        first = self.client.post(f"/file/{file.id}/commit/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "committed")

        second = self.client.post(f"/file/{file.id}/commit/")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "already_committed")

    @patch("files.services.upload_service.StorageGateway")
    def test_expired_upload_chunk_rejected(self, mock_storage_cls):
        file = self._file(expires=timezone.now() - timedelta(minutes=1))

        res = self.client.post(
            f"/file/{file.id}/chunk/0/",
            {"chunk": _tiny_upload()},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("expired", res.json()["error"].lower())
        file.refresh_from_db()
        self.assertEqual(file.upload_state, FileRecord.UploadState.FAILED)

    @patch("files.services.upload_service.StorageGateway")
    def test_chunk_rejected_after_commit(self, mock_storage_cls):
        file = self._file(state=FileRecord.UploadState.COMMITTED)
        res = self.client.post(
            f"/file/{file.id}/chunk/0/",
            {"chunk": _tiny_upload()},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)

    @patch("files.services.upload_service.StorageGateway")
    def test_oversized_single_chunk_rejected(self, mock_storage_cls):
        # Was: no per-chunk cap anywhere -- an arbitrarily large single POST
        # body would be written straight to storage.
        from files.services.upload_service import MAX_CHUNK_BYTES

        file = self._file()
        file.size = MAX_CHUNK_BYTES * 10  # declared size is not the problem here
        file.save(update_fields=["size"])
        storage = mock_storage_cls.return_value
        storage.calculate_total_chunk_size.return_value = 0

        oversized = SimpleUploadedFile(
            "chunk_0.bin", b"x" * (MAX_CHUNK_BYTES + 1), content_type="application/octet-stream",
        )
        res = self.client.post(f"/file/{file.id}/chunk/0/", {"chunk": oversized}, format="multipart")

        self.assertEqual(res.status_code, 413)
        storage.upload_bytes.assert_not_called()

    @patch("files.services.upload_service.StorageGateway")
    def test_cumulative_bytes_exceeding_declared_size_rejected(self, mock_storage_cls):
        # Was: a client could declare a tiny size at start() then upload
        # unlimited real chunk bytes -- nothing checked again until commit(),
        # by which point the storage cost was already incurred.
        file = self._file()
        file.size = 100  # declared a tiny file
        file.save(update_fields=["size"])
        storage = mock_storage_cls.return_value
        # Already-stored real bytes alone blow past size * 1.5 (150).
        storage.calculate_total_chunk_size.return_value = 200

        res = self.client.post(
            f"/file/{file.id}/chunk/1/", {"chunk": _tiny_upload()}, format="multipart",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("declared file size", res.json()["error"])
        storage.upload_bytes.assert_not_called()

    @patch("files.services.upload_service.StorageGateway")
    def test_upload_within_declared_size_still_succeeds(self, mock_storage_cls):
        file = self._file()
        file.size = 10_000
        file.save(update_fields=["size"])
        storage = mock_storage_cls.return_value
        storage.calculate_total_chunk_size.return_value = 0

        res = self.client.post(
            f"/file/{file.id}/chunk/0/", {"chunk": _tiny_upload()}, format="multipart",
        )

        self.assertEqual(res.status_code, 200)
        storage.upload_bytes.assert_called_once()


def _tiny_upload():
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile("chunk_0.bin", b"{}", content_type="application/octet-stream")
