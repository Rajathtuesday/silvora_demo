

# # from datetime import timedelta
# # import os
# # import json
# # import math
# # import hashlib
# # import shutil
# # from time import timezone
# # from django.conf import settings
# # from django.http import JsonResponse, StreamingHttpResponse, Http404
# # from django.db import transaction
# # from django.shortcuts import get_object_or_404
# # from django.utils.http import http_date
# # from rest_framework.decorators import api_view, permission_classes
# # from rest_framework.permissions import IsAuthenticated
# # from rest_framework.views import APIView
# # from rest_framework.response import Response
# # from rest_framework_simplejwt.authentication import JWTAuthentication
# # from wsgiref.util import FileWrapper

# # from files.models import FileRecord, UserQuota
# # from files.r2_storage import R2Storage


# # # ============================================================
# # # HELPERS
# # # ============================================================

# # def r2_base(user_id, file_id):
# #     return f"Silvora/users/{user_id}/files/{file_id}"


# # def compute_manifest_hash(manifest: dict) -> str:
# #     raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
# #     return hashlib.sha256(raw).hexdigest()


# # def get_or_create_quota(user):
# #     quota, _ = UserQuota.objects.get_or_create(
# #         user=user,
# #         defaults={"limit_bytes": 1 * 1024 * 1024 * 1024},
# #     )
# #     return quota


# # # ============================================================
# # # START UPLOAD
# # # ============================================================

# # @api_view(["POST"])
# # @permission_classes([IsAuthenticated])
# # def start_upload(request):
# #     filename = request.data.get("filename")
# #     size = int(request.data.get("size", 0))
# #     chunk_size = int(request.data.get("chunk_size", 2 * 1024 * 1024))
# #     security_mode = request.data.get("security_mode")

# #     if not filename or size <= 0:
# #         return JsonResponse({"error": "Invalid input"}, status=400)

# #     quota = get_or_create_quota(request.user)
# #     if not quota.can_store(size):
# #         return JsonResponse({"error": "Quota exceeded"}, status=403)

# #     file = FileRecord.objects.create(
# #         owner=request.user,
# #         filename=filename,
# #         size=0,
# #         security_mode=security_mode,
# #         storage_type=FileRecord.STORAGE_R2,
# #     )

# #     manifest = {
# #         "version": 1,
# #         "file_id": str(file.id),
# #         "filename": filename,
# #         "file_size": size,
# #         "chunk_size": chunk_size,
# #         "security_mode": security_mode,
# #         "chunks": [],
# #     }
# #     manifest["server_hash"] = compute_manifest_hash(manifest)

# #     r2 = R2Storage()
# #     r2.upload_json(manifest, f"{r2_base(request.user.id, file.id)}/manifest.json")

# #     file.manifest_path = f"{r2_base(request.user.id, file.id)}/manifest.json"
# #     file.final_path = f"{r2_base(request.user.id, file.id)}/final.bin"
# #     file.save(update_fields=["manifest_path", "final_path"])

# #     return JsonResponse({"file_id": str(file.id)})


# # # ============================================================
# # # RESUME UPLOAD
# # # ============================================================

# # @api_view(["GET"])
# # @permission_classes([IsAuthenticated])
# # def resume_upload(request, file_id):
# #     r2 = R2Storage()
# #     key = f"{r2_base(request.user.id, file_id)}/manifest.json"

# #     try:
# #         manifest = r2.get_json(key)
# #     except Exception:
# #         return JsonResponse({"uploaded_indices": []})

# #     return JsonResponse({
# #         "uploaded_indices": [c["index"] for c in manifest["chunks"]],
# #         "chunk_size": manifest["chunk_size"],
# #         "total_chunks": math.ceil(
# #             manifest["file_size"] / manifest["chunk_size"]
# #         ),
# #     })


# # # ============================================================
# # # UPLOAD CHUNK
# # # ============================================================

# # @api_view(["POST"])
# # @permission_classes([IsAuthenticated])
# # def upload_chunk(request, file_id, index):
# #     blob = request.FILES.get("chunk")
# #     nonce = request.headers.get("X-Chunk-Nonce")
# #     mac = request.headers.get("X-Chunk-Mac")

# #     if not blob or not nonce or not mac:
# #         return JsonResponse({"error": "Missing data"}, status=400)

# #     data = blob.read()
# #     sha = hashlib.sha256(data).hexdigest()

# #     r2 = R2Storage()
# #     base = r2_base(request.user.id, file_id)

# #     r2.upload_bytes(data, f"{base}/chunks/chunk_{index}.bin")

# #     manifest = r2.get_json(f"{base}/manifest.json")

# #     manifest["chunks"] = [
# #         c for c in manifest["chunks"] if c["index"] != index
# #     ] + [{
# #         "index": index,
# #         "ciphertext_size": len(data),
# #         "ciphertext_sha256": sha,
# #         "nonce": nonce,
# #         "mac": mac,
# #     }]

# #     manifest["chunks"].sort(key=lambda c: c["index"])
# #     manifest["server_hash"] = compute_manifest_hash(manifest)

# #     r2.upload_json(manifest, f"{base}/manifest.json")

# #     return JsonResponse({"stored": True})


# # # ============================================================
# # # FINISH UPLOAD
# # # ============================================================

# # @api_view(["POST"])
# # @permission_classes([IsAuthenticated])
# # def finish_upload(request, file_id):
# #     r2 = R2Storage()
# #     base = r2_base(request.user.id, file_id)

# #     manifest = r2.get_json(f"{base}/manifest.json")
# #     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

# #     final_data = bytearray()
# #     offset = 0

# #     for c in chunks:
# #         data = r2.client.get_object(
# #             Bucket=r2.bucket,
# #             Key=f"{base}/chunks/chunk_{c['index']}.bin",
# #         )["Body"].read()

# #         if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
# #             return JsonResponse({"error": "Integrity failure"}, status=400)

# #         c["offset"] = offset
# #         offset += len(data)
# #         final_data.extend(data)

# #     manifest["chunks"] = chunks
# #     manifest["server_hash"] = compute_manifest_hash(manifest)

# #     r2.upload_bytes(final_data, f"{base}/final.bin")
# #     r2.upload_json(manifest, f"{base}/manifest.json")

# #     with transaction.atomic():
# #         file = FileRecord.objects.select_for_update().get(
# #             id=file_id, owner=request.user
# #         )
# #         quota = UserQuota.objects.select_for_update().get(user=request.user)

# #         if not quota.can_store(len(final_data)):
# #             return JsonResponse({"error": "Quota exceeded"}, status=403)

# #         file.size = len(final_data)
# #         file.save(update_fields=["size"])
# #         quota.consume(len(final_data))

# #     return JsonResponse({"status": "complete"})


# # # ============================================================
# # # PREVIEW ENDPOINTS
# # # ============================================================

# # class FileManifestView(APIView):
# #     authentication_classes = [JWTAuthentication]
# #     permission_classes = [IsAuthenticated]

# #     def get(self, request, file_id):
# #         file = FileRecord.objects.get(id=file_id, owner=request.user)
# #         r2 = R2Storage()
# #         return Response(r2.get_json(file.manifest_path))


# # class FileDataView(APIView):
# #     authentication_classes = [JWTAuthentication]
# #     permission_classes = [IsAuthenticated]

# #     def get(self, request, file_id):
# #         file = FileRecord.objects.get(id=file_id, owner=request.user)
# #         r2 = R2Storage()
# #         stream, size = r2.open_stream(file.final_path)

# #         response = StreamingHttpResponse(
# #             FileWrapper(stream),
# #             content_type="application/octet-stream",
# #         )
# #         response["Content-Length"] = str(size)
# #         response["Cache-Control"] = "no-store"
# #         response["Last-Modified"] = http_date()
# #         return response


# # @api_view(["GET"])
# # @permission_classes([IsAuthenticated])
# # def list_files(request):
# #     files = FileRecord.objects.filter(
# #         owner=request.user,
# #         deleted_at__isnull=True,
# #     )

# #     return JsonResponse([
# #         {
# #             "file_id": str(f.id),
# #             "upload_id": str(f.upload_id),
# #             "filename": f.filename,
# #             "size": f.size,
# #         }
# #         for f in files
# #     ], safe=False)
    
    

# # # ============================================================
# # # QUOTA
# # # ============================================================

# # @api_view(["GET"])
# # @permission_classes([IsAuthenticated])
# # def get_storage_quota(request):
# #     quota = get_or_create_quota(request.user)

# #     return Response({
# #         "used_bytes": quota.used_bytes,
# #         "limit_bytes": quota.limit_bytes,
# #         "percent": int((quota.used_bytes / quota.limit_bytes) * 100)
# #         if quota.limit_bytes > 0 else 0,
# #     })
    
    
# # #========================================================
# # # Soft delete 
# # #========================================================
# # @api_view(["DELETE"])
# # @permission_classes([IsAuthenticated])
# # def delete_upload(request, upload_id):
# #     with transaction.atomic():
# #         f = get_object_or_404(
# #             FileRecord,
# #             upload_id=upload_id,
# #             owner=request.user,
# #             deleted_at__isnull=True,
# #         )

# #         quota = UserQuota.objects.select_for_update().get(user=request.user)
# #         quota.release(f.size)

# #         f.mark_deleted(retention_days=30)

# #     return JsonResponse({"status": 1})

# # def auto_purge_trash(user):
# #     raise NotImplementedError

# # # ============================================================
# # # # LIST TRASH
# # # # ============================================================

# # @api_view(["GET"])
# # @permission_classes([IsAuthenticated])
# # def list_trash(request):
# #     auto_purge_trash(request.user)

# #     trash = FileRecord.objects.filter(
# #         owner=request.user,
# #         deleted_at__isnull=False,
# #     )

# #     return JsonResponse([
# #         {
# #             "file_id": str(f.id),
# #             "filename": f.filename,
# #             "size": f.size,
# #             "deleted_at": f.deleted_at.isoformat(),
# #         }
# #         for f in trash
# #     ], safe=False)

# # # # ============================================================
# # # # DELETE (MOVE TO TRASH)
# # # # ============================================================

# # # @api_view(["DELETE"])
# # # @permission_classes([IsAuthenticated])
# # # def delete_upload(request, upload_id):
# # #     with transaction.atomic():
# # #         f = get_object_or_404(
# # #             FileRecord,
# # #             upload_id=upload_id,
# # #             owner=request.user,
# # #             deleted_at__isnull=True,
# # #         )

# # #         quota = get_or_create_quota(request.user)
# # #         quota.used_bytes = max(0, quota.used_bytes - f.size)
# # #         quota.save(update_fields=["used_bytes"])

# # #         f.deleted_at = timezone.now()
# # #         f.save(update_fields=["deleted_at"])

# # #     return JsonResponse({"status": 1})




# # # ============================================================
# # # CONFIG
# # # ============================================================

# # TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 7)

# # # ============================================================
# # # HELPERS
# # # ============================================================

# # def upload_base_dir(user_id: str, upload_id: str) -> str:
# #     return os.path.join(settings.MEDIA_ROOT, "uploads", user_id, upload_id)

# # def trash_base_dir(user_id: str, upload_id: str) -> str:
# #     return os.path.join(settings.MEDIA_ROOT, "trash", user_id, upload_id)

# # def compute_manifest_server_hash(manifest: dict) -> str:
# #     raw = json.dumps(
# #         manifest,
# #         sort_keys=True,
# #         separators=(",", ":"),
# #         ensure_ascii=False,
# #     ).encode("utf-8")
# #     return hashlib.sha256(raw).hexdigest()

# # def get_authenticated_user(request):
# #     auth = JWTAuthentication()
# #     try:
# #         res = auth.authenticate(request)
# #         if not res:
# #             return None
# #         user, _ = res
# #         return user
# #     except Exception:
# #         return None

# # def _purge_file_record(record: FileRecord):
# #     user_id = str(record.owner_id)
# #     upload_id = str(record.upload_id)

# #     shutil.rmtree(upload_base_dir(user_id, upload_id), ignore_errors=True)
# #     shutil.rmtree(trash_base_dir(user_id, upload_id), ignore_errors=True)

# #     record.delete()

# # def _auto_purge_trash_for_user(user):
# #     cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
# #     old = FileRecord.objects.filter(owner=user, deleted_at__lt=cutoff)
# #     for r in old:
# #         _purge_file_record(r)








# # # # ============================================================
# # # # RESTORE FROM TRASH
# # # # ============================================================

# # @api_view(["POST"])
# # @permission_classes([IsAuthenticated])
# # def restore_upload(request, file_id):
# #     with transaction.atomic():
# #         f = get_object_or_404(
# #             FileRecord,
# #             id=file_id,
# #             owner=request.user,
# #             deleted_at__isnull=False,
# #         )

# #         quota = get_or_create_quota(request.user)
# #         if quota.used_bytes + f.size > quota.limit_bytes:
# #             return JsonResponse(
# #                 {"error": "Not enough quota"},
# #                 status=403,
# #             )

# #         f.deleted_at = None
# #         f.save(update_fields=["deleted_at"])

# #         quota.used_bytes += f.size
# #         quota.save(update_fields=["used_bytes"])

# #     return JsonResponse({"status": 1})



# # # ============================================================
# # # TRASH
# # # ============================================================

# # @api_view(["DELETE"])
# # @permission_classes([IsAuthenticated])
# # def delete_file(request, file_id):
# #     with transaction.atomic():
# #         f = FileRecord.objects.select_for_update().get(
# #             id=file_id,
# #             owner=request.user,
# #             deleted_at__isnull=True,
# #         )

# #         quota = UserQuota.objects.select_for_update().get(user=request.user)
# #         quota.release(f.size)

# #         f.mark_deleted(retention_days=TRASH_RETENTION_DAYS)

# #     return JsonResponse({"status": 1})


# # @api_view(["GET"])
# # @permission_classes([IsAuthenticated])
# # def list_trash(request):
# #     auto_purge_trash(request.user)

# #     trash = FileRecord.objects.filter(
# #         owner=request.user,
# #         deleted_at__isnull=False,
# #     )

# #     return JsonResponse([
# #         {
# #             "file_id": str(f.id),
# #             "filename": f.filename,
# #             "size": f.size,
# #             "deleted_at": f.deleted_at.isoformat(),
# #         }
# #         for f in trash
# #     ], safe=False)


# # @api_view(["POST"])
# # @permission_classes([IsAuthenticated])
# # def restore_upload(request, file_id):
# #     with transaction.atomic():
# #         f = FileRecord.objects.select_for_update().get(
# #             id=file_id,
# #             owner=request.user,
# #             deleted_at__isnull=False,
# #         )

# #         quota = get_or_create_quota(request.user)
# #         if not quota.can_store(f.size):
# #             return JsonResponse({"error": "Quota exceeded"}, status=403)

# #         f.restore()
# #         quota.consume(f.size)

# #     return JsonResponse({"status": 1})

# # ================================================================================
# import json
# from unittest.mock import patch
# from django.contrib.auth.models import User
# from rest_framework.test import APITestCase
# from rest_framework_simplejwt.tokens import RefreshToken
# from files.models import FileRecord


# class FileAPITests(APITestCase):
#     def setUp(self):
#         self.user = User.objects.create_user(
#             username="tester",
#             password="pass1234"
#         )

#         refresh = RefreshToken.for_user(self.user)
#         self.client.credentials(
#             HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}"
#         )

#     # --------------------------------------------------
#     # START UPLOAD
#     # --------------------------------------------------
#     @patch("files.views.R2Storage")
#     def test_start_upload(self, MockR2):
#         res = self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "a.txt",
#                 "size": 100,
#                 "chunk_size": 50,
#                 "security_mode": "zero_knowledge",
#             },
#             format="json",
#         )

#         self.assertEqual(res.status_code, 200)
#         data = json.loads(res.content)
#         self.assertIn("file_id", data)

#     # --------------------------------------------------
#     # RESUME UPLOAD (EMPTY)
#     # --------------------------------------------------
#     @patch("files.views.R2Storage")
#     def test_resume_upload_empty(self, MockR2):
#         instance = MockR2.return_value
#         instance.get_json.side_effect = Exception("missing")

#         res = self.client.get(
#             "/upload/file/00000000-0000-0000-0000-000000000000/resume/"
#         )

#         self.assertEqual(res.status_code, 200)
#         data = json.loads(res.content)
#         self.assertEqual(data["uploaded_indices"], [])

#     # --------------------------------------------------
#     # LIST FILES
#     # --------------------------------------------------
#     def test_list_files(self):
#         FileRecord.objects.create(
#             owner=self.user,
#             filename="x.bin",
#             size=123,
#             storage_type=FileRecord.STORAGE_R2,
#             security_mode="zero_knowledge",
#         )

#         res = self.client.get("/upload/files/")
#         self.assertEqual(res.status_code, 200)

#         data = json.loads(res.content)
#         self.assertEqual(len(data), 1)
#         self.assertEqual(data[0]["filename"], "x.bin")

#     # --------------------------------------------------
#     # QUOTA
#     # --------------------------------------------------
#     def test_quota(self):
#         res = self.client.get("/upload/quota/")
#         self.assertEqual(res.status_code, 200)

#         data = json.loads(res.content)
#         self.assertIn("used_bytes", data)
#         self.assertIn("limit_bytes", data)

#     # --------------------------------------------------
#     # DELETE → TRASH
#     # --------------------------------------------------
#     def test_delete_and_trash(self):
#         f = FileRecord.objects.create(
#             owner=self.user,
#             filename="trash.txt",
#             size=200,
#             storage_type=FileRecord.STORAGE_R2,
#             security_mode="zero_knowledge",
#         )

#         res = self.client.delete(f"/upload/file/{f.id}/delete/")
#         self.assertEqual(res.status_code, 200)

#         f.refresh_from_db()
#         self.assertIsNotNone(f.deleted_at)

#     # --------------------------------------------------
#     # LIST TRASH
#     # --------------------------------------------------
#     def test_list_trash(self):
#         # 1️⃣ Create a file
#         file = FileRecord.objects.create(
#             owner=self.user,
#             filename="trash_me.txt",
#             size=123,
#             storage_type=FileRecord.STORAGE_R2,
#         )

#         # 2️⃣ Delete it (move to trash)
#         res = self.client.delete(f"/upload/file/{file.id}/")
#         self.assertEqual(res.status_code, 200)

#         # 3️⃣ List trash
#         res = self.client.get("/upload/trash/")
#         self.assertEqual(res.status_code, 200)

#         data = res.json()
#         self.assertGreaterEqual(len(data), 1)


#     # --------------------------------------------------
#     # RESTORE
#     # --------------------------------------------------
#     def test_restore(self):
#         f = FileRecord.objects.create(
#             owner=self.user,
#             filename="restore.txt",
#             size=80,
#             deleted_at="2025-01-01T00:00:00Z",
#             storage_type=FileRecord.STORAGE_R2,
#             security_mode="zero_knowledge",
#         )

#         res = self.client.post(f"/upload/trash/{f.id}/restore/")
#         self.assertEqual(res.status_code, 200)

#         f.refresh_from_db()
#         self.assertIsNone(f.deleted_at)

#     def test_delete_and_restore_file_flow(self):
#         """
#         Full lifecycle test:
#         upload → delete → trash → restore → files
#         """

#         # --------------------------------------------------
#         # 1️⃣ Upload a file
#         # --------------------------------------------------
#         res = self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "restore_test.txt",
#                 "size": 1234,
#                 "chunk_size": 1024,
#                 "security_mode": "standard",
#             },
#             format="json",
#         )
#         self.assertEqual(res.status_code, 200)
#         file_id = res.json()["file_id"]

#         # Simulate finish (no chunks needed for this test)
#         self.client.post(f"/upload/file/{file_id}/finish/")

#         # --------------------------------------------------
#         # 2️⃣ Ensure file is visible
#         # --------------------------------------------------
#         res = self.client.get("/upload/files/")
#         self.assertEqual(res.status_code, 200)
#         files = res.json()
#         self.assertTrue(any(f["file_id"] == file_id for f in files))

#         # --------------------------------------------------
#         # 3️⃣ Delete (move to trash)
#         # --------------------------------------------------
#         res = self.client.delete(f"/upload/file/{file_id}/delete/")
#         self.assertEqual(res.status_code, 200)

#         # --------------------------------------------------
#         # 4️⃣ Verify file removed from files list
#         # --------------------------------------------------
#         res = self.client.get("/upload/files/")
#         files = res.json()
#         self.assertFalse(any(f["file_id"] == file_id for f in files))

#         # --------------------------------------------------
#         # 5️⃣ Verify file appears in trash
#         # --------------------------------------------------
#         res = self.client.get("/upload/trash/")
#         self.assertEqual(res.status_code, 200)
#         trash = res.json()
#         self.assertTrue(any(f["file_id"] == file_id for f in trash))

#         # --------------------------------------------------
#         # 6️⃣ Restore file
#         # --------------------------------------------------
#         res = self.client.post(f"/upload/file/{file_id}/restore/")
#         self.assertEqual(res.status_code, 200)

#         # --------------------------------------------------
#         # 7️⃣ File should reappear in files list
#         # --------------------------------------------------
#         res = self.client.get("/upload/files/")
#         files = res.json()
#         self.assertTrue(any(f["file_id"] == file_id for f in files))

#         # --------------------------------------------------
#         # 8️⃣ Trash should no longer contain file
#         # --------------------------------------------------
#         res = self.client.get("/upload/trash/")
#         trash = res.json()
#         self.assertFalse(any(f["file_id"] == file_id for f in trash))
# ===============================================================================
# files/tests/test_files_api.py

# import json
# from unittest.mock import patch, MagicMock
# from django.contrib.auth import get_user_model
# from rest_framework.test import APITestCase
# from rest_framework import status

# from files.models import FileRecord, UserQuota

# User = get_user_model()


# class FileAPITests(APITestCase):
#     def setUp(self):
#         self.user = User.objects.create_user(
#             username="testuser",
#             password="pass1234",
#         )
#         self.client.force_authenticate(self.user)

#         self.fake_manifest = {
#             "version": 1,
#             "file_id": "dummy",
#             "filename": "test.bin",
#             "file_size": 1024,
#             "chunk_size": 512,
#             "security_mode": "standard",
#             "chunks": [],
#             "server_hash": "abc",
#         }

#     # --------------------------------------------------
#     # START UPLOAD
#     # --------------------------------------------------
#     @patch("files.views.R2Storage")
#     def test_start_upload(self, mock_r2):
#         mock_r2.return_value.upload_json.return_value = None

#         res = self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "test.bin",
#                 "size": 1024,
#                 "chunk_size": 512,
#                 "security_mode": "standard",
#             },
#             format="json",
#         )

#         self.assertEqual(res.status_code, 200)
#         self.assertIn("file_id", res.json())

#         file_id = res.json()["file_id"]
#         self.assertTrue(
#             FileRecord.objects.filter(id=file_id).exists()
#         )

#     # --------------------------------------------------
#     # RESUME UPLOAD (EMPTY)
#     # --------------------------------------------------
#     @patch("files.views.R2Storage")
#     def test_resume_upload_empty(self, mock_r2):
#         mock_r2.return_value.get_json.side_effect = Exception("missing")

#         res = self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "x.bin",
#                 "size": 100,
#                 "chunk_size": 50,
#                 "security_mode": "standard",
#             },
#             format="json",
#         )

#         file_id = res.json()["file_id"]

#         res = self.client.get(f"/upload/file/{file_id}/resume/")
#         self.assertEqual(res.status_code, 200)
#         self.assertEqual(res.json()["uploaded_indices"], [])

#     # --------------------------------------------------
#     # LIST FILES
#     # --------------------------------------------------
#     def test_list_files(self):
#         self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "a.txt",
#                 "size": 10,
#                 "chunk_size": 5,
#                 "security_mode": "standard",
#             },
#             format="json",
#         )

#         res = self.client.get("/upload/files/")
#         self.assertEqual(res.status_code, 200)
#         self.assertEqual(len(res.json()), 1)


#     # --------------------------------------------------
#     # DELETE → TRASH → RESTORE FLOW
#     # --------------------------------------------------
#     def test_delete_and_restore_file_flow(self):
#         res = self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "trash.bin",
#                 "size": 100,
#                 "chunk_size": 50,
#                 "security_mode": "standard",
#             },
#             format="json",
#         )

#         file_id = res.json()["file_id"]
#         file = FileRecord.objects.get(id=file_id)

#         quota = UserQuota.objects.get(user=self.user)

#         # Delete
#         res = self.client.delete(f"/upload/file/{file_id}/delete/")
#         self.assertEqual(res.status_code, 200)

#         file.refresh_from_db()
#         self.assertIsNotNone(file.deleted_at)

#         # Trash
#         res = self.client.get("/upload/trash/")
#         self.assertEqual(len(res.json()), 1)

#         # Restore
#         res = self.client.post(f"/upload/file/{file_id}/restore/")
#         self.assertEqual(res.status_code, 200)

#         file.refresh_from_db()
#         self.assertIsNone(file.deleted_at)

#     # --------------------------------------------------
#     # QUOTA
#     # --------------------------------------------------
#     def test_get_quota(self):
#         # Trigger quota creation
#         self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "x.bin",
#                 "size": 100,
#                 "chunk_size": 50,
#                 "security_mode": "standard",
#             },
#             format="json",
#         )

#         quota = UserQuota.objects.get(user=self.user)
#         quota.used_bytes = 500
#         quota.save()

#         res = self.client.get("/upload/quota/")
#         self.assertEqual(res.status_code, 200)
#         self.assertEqual(res.data["used_bytes"], 500)
# ==========================================================
# from django.test import TestCase
# from django.contrib.auth import get_user_model
# from django.utils import timezone
# from rest_framework.test import APIClient
# from unittest.mock import patch, MagicMock
# from io import BytesIO
# import json
# import uuid

# from files.models import FileRecord, UserQuota

# User = get_user_model()


# # ============================================================
# # TEST BASE
# # ============================================================

# class FileAPITests(TestCase):
#     """
#     End-to-end API tests for:
#     - upload lifecycle
#     - resume
#     - list files
#     - quota
#     - trash / restore
#     """

#     def setUp(self):
#         self.user = User.objects.create_user(
#             username="testuser",
#             password="password123",
#         )

#         self.client = APIClient()
#         self.client.force_authenticate(user=self.user)

#         # Ensure quota exists ONCE (important)
#         self.quota = UserQuota.objects.get_or_create(
#             user=self.user,
#             defaults={
#                 "used_bytes": 0,
#                 "limit_bytes": 10 * 1024 * 1024,
#             },
#         )[0]

#     # ============================================================
#     # HELPERS
#     # ============================================================

#     def _start_upload(self):
#         res = self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "test.txt",
#                 "size": 1024,
#                 "chunk_size": 512,
#                 "security_mode": "zero_knowledge",
#             },
#             format="json",
#         )
#         self.assertEqual(res.status_code, 200)
#         return res.json()["file_id"]

#     # ============================================================
#     # UPLOAD
#     # ============================================================

#     @patch("files.views.R2Storage.upload_json")
#     def test_start_upload(self, mock_upload_json):
#         res = self.client.post(
#             "/upload/file/start/",
#             {
#                 "filename": "hello.txt",
#                 "size": 1000,
#                 "chunk_size": 512,
#                 "security_mode": "zero_knowledge",
#             },
#             format="json",
#         )

#         self.assertEqual(res.status_code, 200)
#         self.assertIn("file_id", res.json())

#     @patch("files.views.R2Storage.get_json")
#     def test_resume_upload_empty(self, mock_get_json):
#         mock_get_json.side_effect = Exception("not found")

#         file_id = self._start_upload()

#         res = self.client.get(
#             f"/upload/file/{file_id}/resume/"
#         )

#         self.assertEqual(res.status_code, 200)
#         self.assertEqual(res.json()["uploaded_indices"], [])

#     # ============================================================
#     # FILE LIST
#     # ============================================================

#     def test_list_files(self):
#         FileRecord.objects.create(
#             owner=self.user,
#             filename="a.txt",
#             size=123,
#             security_mode="zero_knowledge",
#             storage_type=FileRecord.STORAGE_R2,
#         )

#         res = self.client.get("/upload/files/")
#         self.assertEqual(res.status_code, 200)

#         data = res.json()
#         self.assertEqual(len(data), 1)
#         self.assertEqual(data[0]["filename"], "a.txt")

#     # ============================================================
#     # QUOTA
#     # ============================================================

#     def test_get_quota(self):
#         self.quota.used_bytes = 500
#         self.quota.limit_bytes = 1000
#         self.quota.save()

#         res = self.client.get("/upload/quota/")
#         self.assertEqual(res.status_code, 200)

#         data = res.json()
#         self.assertEqual(data["used_bytes"], 500)
#         self.assertEqual(data["limit_bytes"], 1000)
#         self.assertEqual(data["percent"], 50)

#     # ============================================================
#     # DELETE + RESTORE
#     # ============================================================

#     def test_delete_and_restore_file_flow(self):
#         file = FileRecord.objects.create(
#             owner=self.user,
#             filename="trash.txt",
#             size=400,
#             security_mode="zero_knowledge",
#             storage_type=FileRecord.STORAGE_R2,
#         )

#         self.quota.used_bytes = 400
#         self.quota.save()

#         # DELETE
#         res = self.client.delete(
#             f"/upload/file/{file.id}/delete/"
#         )
#         self.assertEqual(res.status_code, 200)

#         file.refresh_from_db()
#         self.assertIsNotNone(file.deleted_at)

#         self.quota.refresh_from_db()
#         self.assertEqual(self.quota.used_bytes, 0)

#         # LIST TRASH
#         res = self.client.get("/upload/trash/")
#         self.assertEqual(res.status_code, 200)
#         self.assertEqual(len(res.json()), 1)

#         # RESTORE
#         res = self.client.post(
#             f"/upload/file/{file.id}/restore/"
#         )
#         self.assertEqual(res.status_code, 200)

#         file.refresh_from_db()
#         self.assertIsNone(file.deleted_at)

#         self.quota.refresh_from_db()
#         self.assertEqual(self.quota.used_bytes, 400)
# ===============================================================================
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from unittest.mock import patch, MagicMock

from files.models import FileRecord, UserQuota

User = get_user_model()


class FileAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="password123",
        )
        self.client.force_authenticate(self.user)

    # ---------------------------------------------------------
    # Helper
    # ---------------------------------------------------------
    @patch("files.views.R2Storage")
    def _start_upload(self, mock_r2_cls):
        mock_r2 = mock_r2_cls.return_value
        mock_r2.upload_json.return_value = None

        res = self.client.post(
            "/upload/file/start/",
            {
                "filename": "test.txt",
                "size": 123,
                "chunk_size": 100,
                "security_mode": "zero_knowledge",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        return res.json()["file_id"]

    # ---------------------------------------------------------
    # Tests
    # ---------------------------------------------------------

    @patch("files.views.R2Storage")
    def test_start_upload(self, mock_r2_cls):
        mock_r2 = mock_r2_cls.return_value
        mock_r2.upload_json.return_value = None

        res = self.client.post(
            "/upload/file/start/",
            {
                "filename": "hello.txt",
                "size": 100,
                "chunk_size": 50,
                "security_mode": "zero_knowledge",
            },
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.assertIn("file_id", res.json())

    @patch("files.views.R2Storage")
    def test_resume_upload_empty(self, mock_r2_cls):
        mock_r2 = mock_r2_cls.return_value
        mock_r2.upload_json.return_value = None
        mock_r2.get_json.side_effect = Exception("no manifest yet")

        file_id = self._start_upload()

        res = self.client.get(f"/upload/file/{file_id}/resume/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["uploaded_indices"], [])

    def test_list_files(self):
        FileRecord.objects.create(
            owner=self.user,
            filename="a.txt",
            size=10,
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
        )

        res = self.client.get("/upload/files/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

    def test_get_quota(self):
        quota = UserQuota.objects.get(user=self.user)
        quota.used_bytes = 200
        quota.limit_bytes = 1000
        quota.save()

        res = self.client.get("/upload/quota/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["used_bytes"], 200)

    def test_delete_and_restore_file_flow(self):
        file = FileRecord.objects.create(
            owner=self.user,
            filename="trash.txt",
            size=50,
            security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2,
        )

        quota = UserQuota.objects.get(user=self.user)
        quota.consume(50)

        # delete
        res = self.client.delete(f"/upload/file/{file.id}/delete/")
        self.assertEqual(res.status_code, 200)

        file.refresh_from_db()
        self.assertIsNotNone(file.deleted_at)

        # restore
        res = self.client.post(f"/upload/file/{file.id}/restore/")
        self.assertEqual(res.status_code, 200)

        file.refresh_from_db()
        self.assertIsNone(file.deleted_at)
