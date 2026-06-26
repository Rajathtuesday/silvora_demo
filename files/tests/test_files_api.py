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

    def test_quota_surfaces_pending_grace_period(self):
        from datetime import timedelta
        from django.utils import timezone
        from billing.models import RazorpayPlan, Subscription

        plan = RazorpayPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            razorpay_plan_id="plan_quota_test", amount_paise=19900,
        )
        Subscription.objects.create(
            user=self.user, plan=plan, razorpay_subscription_id="sub_quota_test",
            status="cancelled",
            grace_ends_at=timezone.now() + timedelta(days=3),
            purge_at=timezone.now() + timedelta(days=26),
        )

        res = self.client.get("/quota/")
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.json()["grace_ends_at"])
        self.assertIsNotNone(res.json()["purge_at"])

    def test_quota_omits_grace_period_when_not_cancelled(self):
        res = self.client.get("/quota/")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("grace_ends_at", res.json())

    def test_rename_updates_the_encrypted_filename_fields(self):
        file = FileRecord.objects.create(
            id=uuid.uuid4(), owner=self.user, tenant=self.user.tenant,
            filename_ciphertext=b"old-cipher", filename_nonce=b"old-nonce", filename_mac=b"old-mac",
            size=10, security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2, upload_state=FileRecord.UploadState.COMMITTED,
        )

        res = self.client.post(f"/file/{file.id}/rename/", {
            "filename_ciphertext": "abcd1234",
            "filename_nonce": "ef567890",
            "filename_mac": "12ab34cd",
        }, format="json")

        self.assertEqual(res.status_code, 200)
        file.refresh_from_db()
        self.assertEqual(file.filename_ciphertext, bytes.fromhex("abcd1234"))
        self.assertEqual(file.filename_nonce, bytes.fromhex("ef567890"))
        self.assertEqual(file.filename_mac, bytes.fromhex("12ab34cd"))

    def test_rename_rejects_someone_elses_file(self):
        other_tenant = Tenant.objects.create(name="other_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        other_user = User.objects.create_user(username="otheruser", password="x", tenant=other_tenant)
        file = FileRecord.objects.create(
            id=uuid.uuid4(), owner=other_user, tenant=other_tenant,
            filename_ciphertext=b"a", filename_nonce=b"b", filename_mac=b"c",
            size=10, security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2, upload_state=FileRecord.UploadState.COMMITTED,
        )

        res = self.client.post(f"/file/{file.id}/rename/", {
            "filename_ciphertext": "aa", "filename_nonce": "bb", "filename_mac": "cc",
        }, format="json")
        self.assertEqual(res.status_code, 404)

    def test_rename_rejects_malformed_hex(self):
        file = FileRecord.objects.create(
            id=uuid.uuid4(), owner=self.user, tenant=self.user.tenant,
            filename_ciphertext=b"a", filename_nonce=b"b", filename_mac=b"c",
            size=10, security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2, upload_state=FileRecord.UploadState.COMMITTED,
        )

        res = self.client.post(f"/file/{file.id}/rename/", {
            "filename_ciphertext": "not-hex!!", "filename_nonce": "bb", "filename_mac": "cc",
        }, format="json")
        self.assertEqual(res.status_code, 400)

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


class TenantIsolationTests(APITestCase):
    """One user must never see or touch another tenant's files (no IDOR)."""

    def setUp(self):
        # A post_save signal auto-creates an individual tenant per user, so
        # alice and bob are isolated by construction (no explicit tenant needed).
        self.alice = User.objects.create_user(username="alice", password="password123")
        self.bob = User.objects.create_user(username="bob", password="password123")

        # Alice owns a committed file in her own (auto-created) tenant
        self.alice_file = FileRecord.objects.create(
            id=uuid.uuid4(),
            owner=self.alice,
            tenant=self.alice.tenant,
            filename_ciphertext=b"abc",
            filename_nonce=b"123",
            filename_mac=b"456",
            size=10,
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.COMMITTED,
        )

    def test_bob_cannot_list_alices_files(self):
        self.client.force_authenticate(self.bob)
        res = self.client.get("/files/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 0)

    def test_bob_cannot_delete_alices_file(self):
        self.client.force_authenticate(self.bob)
        res = self.client.delete(f"/file/{self.alice_file.id}/delete/")
        self.assertEqual(res.status_code, 404)
        self.alice_file.refresh_from_db()
        self.assertIsNone(self.alice_file.deleted_at)  # untouched

    def test_bob_cannot_fetch_alices_manifest(self):
        self.client.force_authenticate(self.bob)
        res = self.client.get(f"/download/file/{self.alice_file.id}/manifest/")
        self.assertEqual(res.status_code, 404)

    def test_alice_can_see_her_own_file(self):
        self.client.force_authenticate(self.alice)
        res = self.client.get("/files/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)
