import os
import uuid
import tempfile
import json
import gc

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from files.models import FileRecord

User = get_user_model()


class PreviewEndpointsTest(TestCase):
    def setUp(self):
        # -----------------------------
        # User
        # -----------------------------
        self.user = User.objects.create_user(
            username="alice",
            password="password123",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)  # ✅ FIX

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

        self.manifest_path = os.path.join(
            self.tmp_dir,
            "manifest.json",
        )

        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f)

        # -----------------------------
        # FileRecord
        # -----------------------------
        self.file = FileRecord.objects.create(
            id=uuid.uuid4(),
            upload_id=uuid.uuid4(),
            owner=self.user,
            filename="test.pdf",
            size=1024,
            final_path=self.encrypted_path,
            manifest_path=self.manifest_path,
            security_mode=FileRecord.SECURITY_STANDARD,
        )

    # ==================================================
    # MANIFEST
    # ==================================================
    def test_manifest_endpoint(self):
        res = self.client.get(
            f"/upload/file/{self.file.id}/manifest/"
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/json")

        data = res.json()
        self.assertEqual(data["file_size"], 1024)
        self.assertIn("chunks", data)

    # ==================================================
    # DATA (STREAM)
    # ==================================================
    def test_data_endpoint(self):
        res = self.client.get(
            f"/upload/file/{self.file.id}/data/"
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res["Content-Type"],
            "application/octet-stream",
        )

        first_chunk = next(res.streaming_content)

        self.assertIsInstance(first_chunk, (bytes, bytearray))
        self.assertFalse(first_chunk.startswith(b"<!DOCTYPE"))
        self.assertFalse(first_chunk.startswith(b"{"))

    # ==================================================
    # ACCESS CONTROL
    # ==================================================
    def test_access_control(self):
        anon = APIClient()

        res1 = anon.get(
            f"/upload/file/{self.file.id}/manifest/"
        )
        res2 = anon.get(
            f"/upload/file/{self.file.id}/data/"
        )

        self.assertEqual(res1.status_code, 401)
        self.assertEqual(res2.status_code, 401)

    # ==================================================
    # CLEANUP
    # ==================================================
    def tearDown(self):
        gc.collect()
        try:
            os.remove(self.encrypted_path)
            os.remove(self.manifest_path)
            os.rmdir(self.tmp_dir)
        except Exception:
            pass
