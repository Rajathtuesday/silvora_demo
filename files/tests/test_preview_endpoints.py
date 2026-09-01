import os
import uuid
import tempfile
import json
import gc

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from files.models import FileRecord
from files.services.storage_gateway import StorageGateway
from tenants.models import Tenant

User = get_user_model()


class PreviewEndpointsTest(TestCase):
    def setUp(self):
        # -----------------------------
        # User
        # -----------------------------
        self.tenant = Tenant.objects.create(name="test", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(
            username="alice",
            password="password123",
            tenant=self.tenant
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # -----------------------------
        # Temp encrypted file
        # -----------------------------
        self.tmp_dir = tempfile.mkdtemp()
        self.encrypted_path = os.path.join(
            self.tmp_dir,
            "encrypted.bin",
        )

        with open(self.encrypted_path, "wb") as f:
            f.write(os.urandom(1024))

        # -----------------------------
        # Manifest
        # -----------------------------
        self.manifest = {
            "version": 1,
            "filename": "test.pdf",
            "file_size": 1024,
            "chunk_size": 512,
            "security_mode": "standard",
            "chunks": [
                {
                    "index": 0,
                    "offset": 0,
                    "ciphertext_size": 512,
                    "nonce_b64": "bm9uY2U=",
                    "mac_b64": "bWFj",
                },
                {
                    "index": 1,
                    "offset": 512,
                    "ciphertext_size": 512,
                    "nonce_b64": "bm9uY2U=",
                    "mac_b64": "bWFj",
                },
            ],
        }

        # Real records only ever get a relative, R2-style manifest_path (see
        # UploadService.commit()) -- write this one through the real
        # StorageGateway at a relative key instead of an absolute tmp path,
        # so it actually resolves the way production data does.
        self.manifest_key = f"test-legacy/{uuid.uuid4()}/manifest.json"
        self.storage = StorageGateway()
        self.storage.upload_bytes(
            json.dumps(self.manifest).encode("utf-8"), self.manifest_key,
        )
        self.manifest_path = self.manifest_key

        # -----------------------------
        # FileRecord
        # -----------------------------
        self.file = FileRecord.objects.create(
            id=uuid.uuid4(),
            owner=self.user,
            tenant=self.user.tenant,
            filename_ciphertext=b"abc",
            filename_nonce=b"123",
            filename_mac=b"456",
            size=1024,
            final_path=self.encrypted_path,
            manifest_path=self.manifest_path,
            security_mode=FileRecord.SECURITY_STANDARD,
            upload_state=FileRecord.UploadState.COMMITTED,
        )

    # ==================================================
    # MANIFEST
    # ==================================================
    def test_manifest_endpoint(self):
        res = self.client.get(
            f"/download/file/{self.file.id}/manifest/"
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/json")

        data = res.json()
        self.assertEqual(data["file_size"], 1024)
        self.assertIn("chunks", data)

    # ==================================================
    # ACCESS CONTROL
    # ==================================================
    def test_access_control(self):
        anon = APIClient()

        res1 = anon.get(
            f"/download/file/{self.file.id}/manifest/"
        )

        self.assertEqual(res1.status_code, 401)

    # ==================================================
    # CLEANUP
    # ==================================================
    def tearDown(self):
        gc.collect()
        try:
            os.remove(self.storage._safe_join(self.manifest_key))
            os.remove(self.encrypted_path)
            os.rmdir(self.tmp_dir)
        except Exception:
            pass
