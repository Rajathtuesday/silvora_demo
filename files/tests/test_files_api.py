import uuid
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from unittest.mock import patch, MagicMock

from files.models import FileRecord
from users.models import UserQuota, SubscriptionTier
from tenants.models import Tenant
from files.services.quota_service import QuotaService

User = get_user_model()


class FileAPITests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="test_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(
            username="testuser",
            password="password123",
            tenant=self.tenant
        )
        self.client.force_authenticate(self.user)

    # ---------------------------------------------------------
    # Helper
    # ---------------------------------------------------------
    @patch("files.services.upload_service.StorageGateway")
    def _start_upload(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        mock_storage.upload_bytes.return_value = None

        file_id = str(uuid.uuid4())
        res = self.client.post(
            "/file/start/",
            {
                "file_id": file_id,
                "size": 123,
                "security_mode": "zero_knowledge",
                "filename_ciphertext": "abcdabcdabcdabcdabcdabcdabcdabcdABCD",
                "filename_nonce": "123412341234123412341234123412341234123412341234",
                "filename_mac": "56785678567856785678567856785678",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        return res.json()["file_id"]

    # ---------------------------------------------------------
    # Tests
    # ---------------------------------------------------------

    @patch("files.services.upload_service.StorageGateway")
    def test_start_upload(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        mock_storage.upload_bytes.return_value = None

        file_id = str(uuid.uuid4())
        res = self.client.post(
            "/file/start/",
            {
                "file_id": file_id,
                "size": 100,
                "security_mode": "zero_knowledge",
                "filename_ciphertext": "abcdabcdabcdabcdABCD",
                "filename_nonce": "123412341234123412341234123412341234123412341234",
                "filename_mac": "56785678567856785678567856785678",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        self.assertIn("file_id", res.json())

    @patch("files.services.upload_service.StorageGateway")
    def test_resume_upload_empty(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        mock_storage.upload_bytes.return_value = None
        mock_storage.list_chunks.return_value = []

        file_id = self._start_upload()

        res = self.client.get(f"/file/{file_id}/resume/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["uploaded_indices"], [])

    def test_list_files(self):
        FileRecord.objects.create(
            id=uuid.uuid4(),
            owner=self.user,
            tenant=self.user.tenant,
            filename_ciphertext=b"abc",
            filename_nonce=b"123",
            filename_mac=b"456",
            size=10,
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.COMMITTED,
        )

        res = self.client.get("/files/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

    def test_get_quota(self):
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.used_bytes = 200
        quota.set_tier('pro') # 100GB
        quota.save()
        
        limit = 100 * 1024 * 1024 * 1024
        
        res = self.client.get("/quota/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["used_bytes"], 200)
        self.assertEqual(res.json()["limit_bytes"], limit)

    def test_delete_and_restore_file_flow(self):
        file = FileRecord.objects.create(
            id=uuid.uuid4(),
            owner=self.user,
            tenant=self.user.tenant,
            filename_ciphertext=b"abc",
            filename_nonce=b"123",
            filename_mac=b"456",
            size=50,
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.COMMITTED,
        )

        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        QuotaService.consume(self.user, 50)

        # delete
        res = self.client.delete(f"/file/{file.id}/delete/")
        self.assertEqual(res.status_code, 200)

        file.refresh_from_db()
        self.assertIsNotNone(file.deleted_at)

        # restore
        res = self.client.post(f"/file/{file.id}/restore/")
        self.assertEqual(res.status_code, 200)

        file.refresh_from_db()
        self.assertIsNone(file.deleted_at)
