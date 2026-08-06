# files/tests/test_trash_purge.py
"""Tests for purge_trashed_files -- the daily cron that makes the app's
"auto-purges in 7 days" promise (file_list_screen.dart / trash_screen.dart)
actually true. See files/management/commands/purge_trashed_files.py."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from files.models import FileRecord
from tenants.models import Tenant
from users.models import UserQuota

User = get_user_model()


class PurgeTrashedFilesTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="purge_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(username="purgeuser", password="password123", tenant=self.tenant)

    def _file(self, deleted_at=None, purge_after=None, size=100, owner=None, tenant=None):
        return FileRecord.objects.create(
            id=uuid.uuid4(), owner=owner or self.user, tenant=tenant or self.user.tenant,
            filename_ciphertext=b"x", filename_nonce=b"y", filename_mac=b"z",
            size=size, security_mode="zero_knowledge", storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.UploadState.COMMITTED,
            deleted_at=deleted_at, purge_after=purge_after,
        )

    @patch("files.management.commands.purge_trashed_files.StorageGateway")
    def test_file_past_purge_after_is_permanently_erased(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        mock_storage.delete_recursive.return_value = None
        now = timezone.now()
        f = self._file(deleted_at=now - timedelta(days=8), purge_after=now - timedelta(hours=1))

        call_command("purge_trashed_files")

        mock_storage.delete_recursive.assert_called_once()
        prefix_arg = mock_storage.delete_recursive.call_args[0][0]
        self.assertIn(str(f.tenant_id), prefix_arg)
        self.assertIn(str(f.id), prefix_arg)
        self.assertFalse(FileRecord.objects.filter(pk=f.pk).exists())

    @patch("files.management.commands.purge_trashed_files.StorageGateway")
    def test_file_not_yet_past_purge_after_is_left_alone(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        now = timezone.now()
        f = self._file(deleted_at=now - timedelta(days=1), purge_after=now + timedelta(days=6))

        call_command("purge_trashed_files")

        mock_storage.delete_recursive.assert_not_called()
        self.assertTrue(FileRecord.objects.filter(pk=f.pk).exists())

    @patch("files.management.commands.purge_trashed_files.StorageGateway")
    def test_non_trashed_file_is_never_touched(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        f = self._file(deleted_at=None, purge_after=None)

        call_command("purge_trashed_files")

        mock_storage.delete_recursive.assert_not_called()
        self.assertTrue(FileRecord.objects.filter(pk=f.pk).exists())

    @patch("files.management.commands.purge_trashed_files.StorageGateway")
    def test_purge_does_not_touch_quota(self, mock_storage_cls):
        """Core regression this command must never reintroduce: quota was
        already released at move-to-trash time (QuotaService.release()
        inside delete_file's SOFT DELETE branch) -- purging must not
        release it again."""
        mock_storage_cls.return_value.delete_recursive.return_value = None
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.used_bytes = 500
        quota.save(update_fields=["used_bytes"])
        now = timezone.now()
        self._file(size=100, deleted_at=now - timedelta(days=8), purge_after=now - timedelta(hours=1))

        call_command("purge_trashed_files")

        quota.refresh_from_db()
        self.assertEqual(quota.used_bytes, 500)  # unchanged

    @patch("files.management.commands.purge_trashed_files.StorageGateway")
    def test_storage_failure_leaves_record_alone_for_retry_next_run(self, mock_storage_cls):
        mock_storage_cls.return_value.delete_recursive.side_effect = Exception("R2 unreachable")
        now = timezone.now()
        f = self._file(deleted_at=now - timedelta(days=8), purge_after=now - timedelta(hours=1))

        call_command("purge_trashed_files")

        self.assertTrue(FileRecord.objects.filter(pk=f.pk).exists())
        f.refresh_from_db()
        self.assertIsNotNone(f.purge_after)  # untouched, will retry

    @patch("files.management.commands.purge_trashed_files.StorageGateway")
    def test_purges_across_multiple_users_in_one_run(self, mock_storage_cls):
        mock_storage_cls.return_value.delete_recursive.return_value = None
        other_tenant = Tenant.objects.create(name="purge_tenant_2", tenant_type=Tenant.TYPE_INDIVIDUAL)
        other_user = User.objects.create_user(username="purgeuser2", password="password123", tenant=other_tenant)
        now = timezone.now()
        f1 = self._file(deleted_at=now - timedelta(days=8), purge_after=now - timedelta(hours=1))
        f2 = self._file(deleted_at=now - timedelta(days=8), purge_after=now - timedelta(hours=1),
                         owner=other_user, tenant=other_tenant)

        call_command("purge_trashed_files")

        self.assertEqual(mock_storage_cls.return_value.delete_recursive.call_count, 2)
        self.assertFalse(FileRecord.objects.filter(pk=f1.pk).exists())
        self.assertFalse(FileRecord.objects.filter(pk=f2.pk).exists())
