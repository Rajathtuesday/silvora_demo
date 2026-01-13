
# # files/views.py

# import os
# import math
# import json
# import uuid
# import shutil
# import hashlib
# from datetime import timedelta

# from django.conf import settings
# from django.http import JsonResponse, FileResponse
# from django.shortcuts import get_object_or_404
# from django.views.decorators.csrf import csrf_exempt
# from django.views.decorators.http import require_http_methods
# from django.utils import timezone

# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework_simplejwt.authentication import JWTAuthentication

# from .models import FileRecord

# from .r2_storage import R2Storage


# # ============================================================
# # CONFIG
# # ============================================================

# TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 30)

# # ============================================================
# # HELPERS
# # ============================================================

# def upload_base_dir(user_id: str, upload_id: str) -> str:
#     return os.path.join(settings.MEDIA_ROOT, "uploads", user_id, upload_id)

# def trash_base_dir(user_id: str, upload_id: str) -> str:
#     return os.path.join(settings.MEDIA_ROOT, "trash", user_id, upload_id)

# def compute_manifest_server_hash(manifest: dict) -> str:
#     raw = json.dumps(
#         manifest,
#         sort_keys=True,
#         separators=(",", ":"),
#         ensure_ascii=False,
#     ).encode("utf-8")
#     return hashlib.sha256(raw).hexdigest()

# def get_authenticated_user(request):
#     auth = JWTAuthentication()
#     try:
#         res = auth.authenticate(request)
#         if not res:
#             return None
#         user, _ = res
#         return user
#     except Exception:
#         return None

# def _purge_file_record(record: FileRecord):
#     user_id = str(record.owner_id)
#     upload_id = str(record.upload_id)

#     shutil.rmtree(upload_base_dir(user_id, upload_id), ignore_errors=True)
#     shutil.rmtree(trash_base_dir(user_id, upload_id), ignore_errors=True)

#     record.delete()

# def _auto_purge_trash_for_user(user):
#     cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
#     old = FileRecord.objects.filter(owner=user, deleted_at__lt=cutoff)
#     for r in old:
#         _purge_file_record(r)

# # ============================================================
# # START UPLOAD
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def start_upload(request):
#     body = request.data or {}

#     filename = body.get("filename")
#     size = body.get("size")
#     chunk_size = body.get("chunk_size", 2 * 1024 * 1024)
#     security_mode = body.get("security_mode", FileRecord.SECURITY_STANDARD)

#     if not filename or not size:
#         return JsonResponse({"error": "filename and size required"}, status=400)

#     if security_mode not in (
#         FileRecord.SECURITY_STANDARD,
#         FileRecord.SECURITY_ZERO,
#     ):
#         return JsonResponse({"error": "invalid security_mode"}, status=400)

#     upload_id = str(uuid.uuid4())
#     user_id = str(request.user.id)

#     base_dir = upload_base_dir(user_id, upload_id)
#     os.makedirs(os.path.join(base_dir, "chunks"), exist_ok=True)

#     manifest = {
#         "manifest_version": 1,
#         "filename": filename,
#         "file_size": size,
#         "chunk_size": chunk_size,
#         "owner": user_id,
#         "security_mode": security_mode,
#         "encryption": "client_side",
#         "aead_algorithm": "XCHACHA20_POLY1305",
#         "chunks": [],
#     }

#     manifest["server_hash"] = compute_manifest_server_hash(
#         {k: v for k, v in manifest.items() if k != "server_hash"}
#     )

#     with open(os.path.join(base_dir, "manifest.json"), "w") as f:
#         json.dump(manifest, f, indent=2)

#     return JsonResponse({
#         "status": 1,
#         "upload_id": upload_id,
#         "manifest": manifest,
#     })

# # ============================================================
# # RESUME UPLOAD
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def resume_upload(request, upload_id):
#     user_id = str(request.user.id)
#     base_dir = upload_base_dir(user_id, str(upload_id))
#     manifest_path = os.path.join(base_dir, "manifest.json")

#     if not os.path.exists(manifest_path):
#         return JsonResponse({
#             "uploaded_indices": [],
#         })

#     with open(manifest_path) as f:
#         manifest = json.load(f)

#     uploaded = [c["index"] for c in manifest.get("chunks", [])]

#     total_chunks = math.ceil(
#         manifest["file_size"] / manifest["chunk_size"]
#     )

#     return JsonResponse({
#         "uploaded_indices": sorted(uploaded),
#         "total_chunks": total_chunks,
#         "chunk_size": manifest["chunk_size"],
#         "security_mode": manifest["security_mode"],
#     })

# # ============================================================
# # UPLOAD CHUNK (OPAQUE ENCRYPTED)
# # ============================================================

# @csrf_exempt
# @require_http_methods(["POST"])
# def upload_chunk_xchacha(request, upload_id, index):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "auth required"}, status=401)

#     base_dir = upload_base_dir(str(user.id), str(upload_id))
#     manifest_path = os.path.join(base_dir, "manifest.json")

#     if not os.path.exists(manifest_path):
#         return JsonResponse({"error": "manifest missing"}, status=404)

#     blob = request.FILES.get("chunk")
#     if not blob:
#         return JsonResponse({"error": "missing chunk"}, status=400)

#     nonce_b64 = request.headers.get("X-Chunk-Nonce")
#     mac_b64 = request.headers.get("X-Chunk-Mac")

#     if not nonce_b64 or not mac_b64:
#         return JsonResponse({"error": "missing crypto headers"}, status=400)

#     data = blob.read()
#     sha = hashlib.sha256(data).hexdigest()

#     chunk_path = os.path.join(base_dir, "chunks", f"chunk_{index}.bin")
#     with open(chunk_path, "wb") as f:
#         f.write(data)

#     with open(manifest_path) as f:
#         manifest = json.load(f)

#     manifest["chunks"] = [
#         c for c in manifest["chunks"] if c["index"] != index
#     ] + [{
#         "index": index,
#         "ciphertext_size": len(data),
#         "ciphertext_sha256": sha,
#         "nonce_b64": nonce_b64,
#         "mac_b64": mac_b64,
#     }]

#     manifest["chunks"].sort(key=lambda c: c["index"])
#     manifest["server_hash"] = compute_manifest_server_hash(
#         {k: v for k, v in manifest.items() if k != "server_hash"}
#     )

#     with open(manifest_path, "w") as f:
#         json.dump(manifest, f, indent=2)

#     return JsonResponse({"stored": True, "index": index})

# # ============================================================
# # FINISH UPLOAD (VERIFY + SEAL)
# # ============================================================
# # @csrf_exempt
# # @require_http_methods(["POST"])
# # def finish_upload(request, upload_id):
# #     user = get_authenticated_user(request)
# #     if not user:
# #         return JsonResponse({"error": "auth required"}, status=401)

# #     user_id = str(user.id)
# #     upload_id = str(upload_id)
# #     base_dir = upload_base_dir(user_id, upload_id)

# #     manifest_path = os.path.join(base_dir, "manifest.json")
# #     if not os.path.exists(manifest_path):
# #         return JsonResponse({"error": "manifest missing"}, status=404)

# #     # --------------------------------------------------
# #     # Load manifest
# #     # --------------------------------------------------
# #     with open(manifest_path, "r", encoding="utf-8") as f:
# #         manifest = json.load(f)

# #     if not manifest.get("chunks"):
# #         return JsonResponse({"error": "no chunks uploaded"}, status=400)

# #     # --------------------------------------------------
# #     # Assemble final.bin + compute offsets
# #     # --------------------------------------------------
# #     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])
# #     final_path = os.path.join(base_dir, "final.bin")

# #     offset = 0
# #     with open(final_path, "wb") as out:
# #         for c in chunks:
# #             chunk_file = os.path.join(
# #                 base_dir, "chunks", f"chunk_{c['index']}.bin"
# #             )

# #             if not os.path.exists(chunk_file):
# #                 return JsonResponse(
# #                     {"error": f"missing chunk {c['index']}"},
# #                     status=400,
# #                 )

# #             with open(chunk_file, "rb") as cf:
# #                 data = cf.read()

# #             # --------------------------------------------------
# #             # Integrity check (ciphertext)
# #             # --------------------------------------------------
# #             if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
# #                 return JsonResponse(
# #                     {
# #                         "error": "chunk integrity failure",
# #                         "index": c["index"],
# #                     },
# #                     status=400,
# #                 )

# #             # --------------------------------------------------
# #             # Persist offset (CRITICAL FOR PREVIEW)
# #             # --------------------------------------------------
# #             c["offset"] = offset
# #             offset += len(data)

# #             out.write(data)

# #     # --------------------------------------------------
# #     # Persist updated manifest (with offsets)
# #     # --------------------------------------------------
# #     manifest["chunks"] = chunks
# #     manifest["server_hash"] = compute_manifest_server_hash(
# #         {k: v for k, v in manifest.items() if k != "server_hash"}
# #     )

# #     with open(manifest_path, "w", encoding="utf-8") as f:
# #         json.dump(manifest, f, indent=2)

# #     # --------------------------------------------------
# #     # Create / update FileRecord
# #     # --------------------------------------------------
# #     record, _ = FileRecord.objects.update_or_create(
# #         upload_id=upload_id,
# #         owner=user,
# #         defaults={
# #             "filename": manifest["filename"],
# #             "size": sum(c["ciphertext_size"] for c in chunks),
# #             "final_path": final_path,
# #             "storage_type": FileRecord.STORAGE_LOCAL,
# #             "security_mode": manifest["security_mode"],
# #             "deleted_at": None,
# #         },
# #     )

# #     return JsonResponse(
# #         {
# #             "status": 1,
# #             "file_id": str(record.id),
# #             "security_mode": record.security_mode,
# #         }
# #     )
# # @csrf_exempt
# # @require_http_methods(["POST"])
# # def finish_upload(request, upload_id):
# #     user = get_authenticated_user(request)
# #     if not user:
# #         return JsonResponse({"error": "auth required"}, status=401)

# #     user_id = str(user.id)
# #     upload_id = str(upload_id)
# #     base_dir = upload_base_dir(user_id, upload_id)

# #     manifest_path = os.path.join(base_dir, "manifest.json")
# #     if not os.path.exists(manifest_path):
# #         return JsonResponse({"error": "manifest missing"}, status=404)

# #     with open(manifest_path, "r", encoding="utf-8") as f:
# #         manifest = json.load(f)

# #     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

# #     final_path = os.path.join(base_dir, "final.bin")

# #     offset = 0
# #     with open(final_path, "wb") as out:
# #         for c in chunks:
# #             chunk_path = os.path.join(
# #                 base_dir, "chunks", f"chunk_{c['index']}.bin"
# #             )

# #             if not os.path.exists(chunk_path):
# #                 return JsonResponse(
# #                     {"error": f"missing chunk {c['index']}"},
# #                     status=400,
# #                 )

# #             with open(chunk_path, "rb") as cf:
# #                 data = cf.read()

# #             if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
# #                 return JsonResponse(
# #                     {"error": "chunk integrity failure"},
# #                     status=400,
# #                 )

# #             # 🔐 critical metadata
# #             c["offset"] = offset
# #             offset += len(data)

# #             out.write(data)

# #     # ✅ WRITE UPDATED MANIFEST BACK TO DISK
# #     manifest["server_hash"] = compute_manifest_server_hash(
# #         {k: v for k, v in manifest.items() if k != "server_hash"}
# #     )

# #     with open(manifest_path, "w", encoding="utf-8") as f:
# #         json.dump(manifest, f, indent=2)

# #     record, _ = FileRecord.objects.update_or_create(
# #         upload_id=upload_id,
# #         owner=user,
# #         defaults={
# #             "filename": manifest["filename"],
# #             "size": offset,
# #             "final_path": final_path,
# #             "storage_type": FileRecord.STORAGE_LOCAL,
# #             "security_mode": manifest["security_mode"],
# #             "deleted_at": None,
# #         },
# #     )

# #     return JsonResponse({
# #         "status": 1,
# #         "file_id": str(record.id),
# #         "security_mode": record.security_mode,
# #     })


# @csrf_exempt
# @require_http_methods(["POST"])
# def finish_upload(request, upload_id):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "auth required"}, status=401)

#     upload_uuid = uuid.UUID(str(upload_id))
#     user_id = str(user.id)

#     base_dir = upload_base_dir(user_id, str(upload_uuid))
#     manifest_path = os.path.join(base_dir, "manifest.json")

#     if not os.path.exists(manifest_path):
#         return JsonResponse({"error": "manifest missing"}, status=404)

#     with open(manifest_path, "r", encoding="utf-8") as f:
#         manifest = json.load(f)

#     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

#     final_path = os.path.join(base_dir, "final.bin")

#     offset = 0
#     with open(final_path, "wb") as out:
#         for c in chunks:
#             chunk_path = os.path.join(
#                 base_dir, "chunks", f"chunk_{c['index']}.bin"
#             )

#             with open(chunk_path, "rb") as cf:
#                 data = cf.read()

#             if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
#                 return JsonResponse(
#                     {"error": "chunk integrity failure"},
#                     status=400,
#                 )

#             c["offset"] = offset
#             offset += len(data)
#             out.write(data)

#     manifest["server_hash"] = compute_manifest_server_hash(
#         {k: v for k, v in manifest.items() if k != "server_hash"}
#     )

#     with open(manifest_path, "w", encoding="utf-8") as f:
#         json.dump(manifest, f, indent=2)

#     # --------------------------------------------------
#     # FileRecord.objects.update_or_create(
#     #     upload_id=upload_uuid,
#     #     owner=user,
#     #     defaults={
#     #         "filename": manifest["filename"],
#     #         "size": offset,
#     #         "final_path": final_path,
#     #         "storage_type": FileRecord.STORAGE_LOCAL,
#     #         "security_mode": manifest["security_mode"],
#     #         "deleted_at": None,
#     #     },
#     # )
#     # --------------------------------------------------
#     # ===============================
#     # UPLOAD FINAL.BIN TO R2
#     # ===============================
#     r2 = R2Storage()
#     r2_key = f"user/{user_id}/{upload_uuid}/final.bin"

#     r2.upload_file(final_path, r2_key)

#     # 🔥 REMOVE LOCAL FILE (Render disk is ephemeral anyway)
#     os.remove(final_path)

#     # ===============================
#     # SAVE FILE RECORD
#     # ===============================
#     FileRecord.objects.update_or_create(
#         upload_id=upload_uuid,
#         owner=user,
#         defaults={
#             "filename": manifest["filename"],
#             "size": offset,
#             "storage_type": FileRecord.STORAGE_R2,
#             # "storage_key": r2_key,
#             "final_path": r2_key,
#             "security_mode": manifest["security_mode"],
#             "deleted_at": None,
            
#         },
#     )


#     return JsonResponse({
#         "status": 1,
#         "upload_id": str(upload_uuid),
#     })




# # ============================================================
# # LIST FILES
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_files(request):
#     _auto_purge_trash_for_user(request.user)

#     files = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=True,
#     ).order_by("-created_at")

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "upload_id": str(f.upload_id),
#             "filename": f.filename,
#             "size": f.size,
#             "security_mode": f.security_mode,
#         }
#         for f in files
#     ], safe=False)

# # ============================================================
# # DOWNLOAD (ENCRYPTED ONLY)
# # ============================================================

# # @api_view(["GET"])
# # @permission_classes([IsAuthenticated])
# # def download_file(request, file_id):
# #     file = get_object_or_404(
# #         FileRecord,
# #         id=file_id,
# #         owner=request.user,
# #         deleted_at__isnull=True,
# #     )

# #     return FileResponse(
# #         open(file.final_path, "rb"),
# #         as_attachment=True,
# #         filename=file.filename,
# #     )

# from .r2_storage import R2Storage

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def download_file(request, file_id):
#     file = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     if file.storage_type == FileRecord.STORAGE_LOCAL:
#         return FileResponse(
#             open(file.final_path, "rb"),
#             as_attachment=True,
#             filename=file.filename,
#         )

#     # 🔐 R2 (encrypted only)
#     r2 = R2Storage()
#     stream, _ = r2.open_stream(file.storage_key)

#     return FileResponse(
#         stream,
#         as_attachment=True,
#         filename=file.filename,
#         content_type="application/octet-stream",
#     )


# # ============================================================
# # PREVIEW (DISABLED BY DESIGN)
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def preview_file(request, file_id):
#     return JsonResponse(
#         {"error": "Preview requires client-side decryption"},
#         status=403,
#     )

# # ============================================================
# # TRASH / DELETE / RESTORE / PURGE
# # ============================================================

# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated])
# def delete_upload(request, upload_id):
#     record = get_object_or_404(
#         FileRecord,
#         upload_id=upload_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     record.deleted_at = timezone.now()
#     record.save(update_fields=["deleted_at"])

#     return JsonResponse({"status": 1})

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def restore_upload(request, file_id):
#     record = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=False,
#     )

#     record.deleted_at = None
#     record.save(update_fields=["deleted_at"])

#     return JsonResponse({"status": 1})

# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated])
# def purge_upload(request, file_id):
#     record = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#     )

#     _purge_file_record(record)
#     return JsonResponse({"status": 1})

# # ============================================================
# # RESET UNFINISHED UPLOADS (DEV)
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def reset_uploads(request):
#     user_id = str(request.user.id)
#     base = os.path.join(settings.MEDIA_ROOT, "uploads", user_id)

#     if not os.path.exists(base):
#         return JsonResponse({"status": "nothing to reset"})

#     removed = 0
#     for name in os.listdir(base):
#         path = os.path.join(base, name)
#         if os.path.isdir(path):
#             shutil.rmtree(path, ignore_errors=True)
#             removed += 1

#     return JsonResponse({
#         "status": "ok",
#         "removed_uploads": removed,
#     })
    
    
# # ============================================================
# # fetch manifest
# # ============================================================
# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def fetch_manifest(request, file_id):
#     file = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     user_id = str(request.user.id)
#     upload_id = str(file.upload_id)

#     manifest_path = os.path.join(
#         settings.MEDIA_ROOT,
#         "uploads",
#         user_id,
#         upload_id,
#         "manifest.json",
#     )

#     if not os.path.exists(manifest_path):
#         return JsonResponse(
#             {"error": "manifest missing"},
#             status=404,
#         )

#     with open(manifest_path, "r", encoding="utf-8") as f:
#         manifest = json.load(f)

#     return JsonResponse(manifest, safe=False)




#     # # ============================================================

#     # # FETCH ENCRYPTED DATA Full File
#     # # ============================================================
#     # from django.views.decorators.http import require_GET

#     # @api_view(["GET"])
#     # @permission_classes([IsAuthenticated])
#     # def fetch_encrypted_data(request, file_id):
#     #     file = get_object_or_404(
#     #         FileRecord,
#     #         id=file_id,
#     #         owner=request.user,
#     #         deleted_at__isnull=True,
#     #     )

#     #     # 🔐 Server is blind: always encrypted
#     #     if file.storage_type == FileRecord.STORAGE_LOCAL:
#     #         if not os.path.exists(file.final_path):
#     #             return JsonResponse({"error": "file missing"}, status=404)

#     #         return FileResponse(
#     #             open(file.final_path, "rb"),
#     #             content_type="application/octet-stream",
#     #         )

#     #     # R2 / S3
#     #     from .r2_storage import R2Storage
#     #     r2 = R2Storage()

#     #     try:
#     #         stream, _ = r2.open_stream(file.final_path)
#     #     except Exception as e:
#     #         return JsonResponse(
#     #             {"error": f"storage error: {str(e)}"},
#     #             status=500,
#     #         )

#     #     return FileResponse(
#     #         stream,
#     #         content_type="application/octet-stream",
#     #     )
# # # ============================================================
# # FETCH ENCRYPTED DATA (RAW BINARY)
# from django.views.decorators.http import require_GET

# @require_GET
# def fetch_encrypted_data(request, file_id):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "auth required"}, status=401)

#     file = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=user,
#         deleted_at__isnull=True,
#     )

#     if file.storage_type == FileRecord.STORAGE_LOCAL:
#         if not os.path.exists(file.final_path):
#             return JsonResponse({"error": "file missing"}, status=404)

#         # 🔐 RAW BINARY — NO DRF
#         response = FileResponse(
#             open(file.final_path, "rb"),
#             as_attachment=False,
#         )
#         response["Content-Type"] = "application/octet-stream"
#         response["X-Content-Type-Options"] = "nosniff"
#         return response

#     # R2 / S3 (same logic)
#     from .r2_storage import R2Storage
#     r2 = R2Storage()

#     try:
#         stream, _ = r2.open_stream(file.final_path)
#     except Exception as e:
#         return JsonResponse(
#             {"error": f"storage error: {str(e)}"},
#             status=500,
#         )

#     response = FileResponse(stream)
#     response["Content-Type"] = "application/octet-stream"
#     response["X-Content-Type-Options"] = "nosniff"
#     return response
# ==========================================================
# files/views.py

# import os
# import math
# import json
# import uuid
# import shutil
# import hashlib
# from datetime import timedelta

# from django.conf import settings
# from django.http import JsonResponse, FileResponse
# from django.shortcuts import get_object_or_404
# from django.utils import timezone
# from django.views.decorators.csrf import csrf_exempt
# from django.views.decorators.http import require_http_methods, require_GET
# from django.db import transaction

# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework_simplejwt.authentication import JWTAuthentication

# from .models import FileRecord , UserQuota
# from .r2_storage import R2Storage


# # ============================================================
# # CONFIG
# # ============================================================

# TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 30)

# # ============================================================
# # PATH HELPERS (LOCAL TEMP STORAGE)
# # ============================================================

# def upload_base_dir(user_id: str, upload_id: str) -> str:
#     return os.path.join(settings.MEDIA_ROOT, "uploads", user_id, upload_id)

# def compute_manifest_hash(manifest: dict) -> str:
#     raw = json.dumps(
#         manifest,
#         sort_keys=True,
#         separators=(",", ":"),
#     ).encode()
#     return hashlib.sha256(raw).hexdigest()

# # ============================================================
# # AUTH (JWT without DRF)
# # ============================================================

# def get_authenticated_user(request):
#     try:
#         auth = JWTAuthentication()
#         result = auth.authenticate(request)
#         return result[0] if result else None
#     except Exception:
#         return None

# # ============================================================
# # QUOTA HELPERS
# # ============================================================


# from django.db import transaction
# from files.models import UserQuota

# DEFAULT_QUOTA_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB


# def get_or_create_quota(user):
#     """
#     Always returns a UserQuota row.
#     Never raises DoesNotExist.
#     """
#     quota, _ = UserQuota.objects.get_or_create(
#         user=user,
#         defaults={"limit_bytes": DEFAULT_QUOTA_BYTES},
#     )
#     return quota


# def assert_quota(user, incoming_size: int):
#     """
#     Enforce quota atomically.
#     Must be called before upload starts.
#     """
#     with transaction.atomic():
#         # 1️⃣ Ensure quota row exists
#         quota = get_or_create_quota(user)

#         # 2️⃣ Lock the row
#         quota = (
#             UserQuota.objects
#             .select_for_update()
#             .get(pk=quota.pk)
#         )

#         # 3️⃣ Enforce limit
#         if quota.used_bytes + incoming_size > quota.limit_bytes:
#             raise PermissionError("Storage quota exceeded")

# # ============================================================
# # STORAGE QUOTA / USAGE
# # ============================================================

# from rest_framework.response import Response
# from files.models import UserQuota


# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def get_storage_quota(request):
#     """
#     Return authoritative storage usage for the user.
#     """
#     quota, _ = UserQuota.objects.get_or_create(user=request.user)

#     used = quota.used_bytes
#     limit = quota.limit_bytes

#     percent = int((used / limit) * 100) if limit > 0 else 0

#     return Response({
#         "used_bytes": used,
#         "limit_bytes": limit,
#         "percent": percent,
#     })


# # ============================================================
# # TRASH AUTO PURGE
# # ============================================================

# def auto_purge_trash(user):
#     cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
#     expired = FileRecord.objects.filter(
#         owner=user,
#         deleted_at__lt=cutoff,
#     )

#     r2 = R2Storage()

#     for f in expired:
#         if f.storage_type == FileRecord.STORAGE_R2:
#             try:
#                 r2.delete_object(f.final_path)
#             except Exception:
#                 pass

#         shutil.rmtree(
#             upload_base_dir(str(user.id), str(f.upload_id)),
#             ignore_errors=True,
#         )
#         f.delete()

# # ============================================================
# # START UPLOAD
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def start_upload(request):
#     body = request.data

#     filename = body.get("filename")
#     size = int(body.get("size", 0))
#     chunk_size = int(body.get("chunk_size", 2 * 1024 * 1024))
#     security_mode = body.get("security_mode")

#     if not filename or size <= 0:
#         return JsonResponse({"error": "Invalid filename or size"}, status=400)

#     if security_mode not in (
#         FileRecord.SECURITY_STANDARD,
#         FileRecord.SECURITY_ZERO,
#     ):
#         return JsonResponse({"error": "Invalid security mode"}, status=400)

#     try:
#         assert_quota(request.user, size)
#     except PermissionError as e:
#         return JsonResponse({"error": str(e)}, status=403)

#     upload_id = uuid.uuid4()
#     base = upload_base_dir(str(request.user.id), str(upload_id))
#     os.makedirs(os.path.join(base, "chunks"), exist_ok=True)

#     manifest = {
#         "version": 1,
#         "filename": filename,
#         "file_size": size,
#         "chunk_size": chunk_size,
#         "security_mode": security_mode,
#         "chunks": [],
#     }
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     with open(os.path.join(base, "manifest.json"), "w") as f:
#         json.dump(manifest, f)

#     return JsonResponse({"upload_id": str(upload_id)})

# # ============================================================
# # RESUME UPLOAD
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def resume_upload(request, upload_id):
#     base = upload_base_dir(str(request.user.id), str(upload_id))
#     path = os.path.join(base, "manifest.json")

#     if not os.path.exists(path):
#         return JsonResponse({"uploaded_indices": []})

#     with open(path) as f:
#         manifest = json.load(f)

#     uploaded = [c["index"] for c in manifest["chunks"]]

#     return JsonResponse({
#         "uploaded_indices": uploaded,
#         "chunk_size": manifest["chunk_size"],
#         "total_chunks": math.ceil(
#             manifest["file_size"] / manifest["chunk_size"]
#         ),
#     })

# # ============================================================
# # UPLOAD CHUNK (OPAQUE)
# # ============================================================
# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def upload_chunk_xchacha(request, upload_id, index):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "Unauthorized"}, status=401)

#     blob = request.FILES.get("chunk") or request.data.get("chunk")  
#     if not blob:
#         return JsonResponse({"error": "Missing chunk"}, status=400)

#     nonce = request.headers.get("X-Chunk-Nonce")
#     mac = request.headers.get("X-Chunk-Mac")

#     if not nonce or not mac:
#         return JsonResponse({"error": "Missing crypto headers"}, status=400)

#     data = blob.read()
#     sha = hashlib.sha256(data).hexdigest()

#     r2 = R2Storage()

#     base_key = f"user/{user.id}/{upload_id}"

#     # 1️⃣ Upload chunk directly to R2
#     chunk_key = f"{base_key}/chunks/chunk_{index}.bin"
#     r2.upload_bytes(data, chunk_key)

#     # 2️⃣ Load manifest from R2
#     manifest_key = f"{base_key}/manifest.json"
#     try:
#         manifest = r2.get_json(manifest_key)
#     except Exception:
#         return JsonResponse({"error": "Manifest missing"}, status=404)

#     # 3️⃣ Update manifest entry
#     manifest["chunks"] = [
#         c for c in manifest["chunks"] if c["index"] != index
#     ] + [{
#         "index": index,
#         "ciphertext_size": len(data),
#         "ciphertext_sha256": sha,
#         "nonce": nonce,
#         "mac": mac,
#     }]

#     manifest["chunks"].sort(key=lambda c: c["index"])
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     # 4️⃣ Save manifest back to R2
#     r2.upload_json(manifest, manifest_key)

#     return JsonResponse({"stored": True, "index": index})

# # ============================================================
# # FINISH UPLOAD (ASSEMBLE + R2)
# # ============================================================
# @csrf_exempt
# @require_http_methods(["POST"])
# def finish_upload(request, upload_id):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "Unauthorized"}, status=401)

#     r2 = R2Storage()
#     base_key = f"user/{user.id}/{upload_id}"
#     manifest_key = f"{base_key}/manifest.json"

#     # 1️⃣ Load manifest
#     manifest = r2.get_json(manifest_key)
#     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

#     # 2️⃣ Assemble final.bin locally (temp)
#     temp_dir = upload_base_dir(str(user.id), str(upload_id))
#     os.makedirs(temp_dir, exist_ok=True)

#     final_path = os.path.join(temp_dir, "final.bin")

#     offset = 0
#     with open(final_path, "wb") as out:
#         for c in chunks:
#             chunk_key = f"{base_key}/chunks/chunk_{c['index']}.bin"
#             data = r2.client.get_object(
#                 Bucket=r2.bucket,
#                 Key=chunk_key,
#             )["Body"].read()

#             # Integrity check
#             if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
#                 return JsonResponse(
#                     {"error": f"Integrity failure at chunk {c['index']}"},
#                     status=400,
#                 )

#             c["offset"] = offset
#             offset += len(data)
#             out.write(data)

#     # 3️⃣ Update manifest with offsets
#     manifest["chunks"] = chunks
#     manifest["server_hash"] = compute_manifest_hash(manifest)
#     r2.upload_json(manifest, manifest_key)

#     # 4️⃣ Upload final.bin to R2
#     final_key = f"{base_key}/final.bin"
#     with open(final_path, "rb") as f:
#         r2.upload_bytes(f.read(), final_key)

#     # 5️⃣ Save DB + quota atomically
#     with transaction.atomic():
#         quota = get_or_create_quota(user)

#         if quota.used_bytes + offset > quota.limit_bytes:
#             return JsonResponse({"error": "Quota exceeded"}, status=403)

#         FileRecord.objects.update_or_create(
#             upload_id=upload_id,
#             owner=user,
#             defaults={
#                 "filename": manifest["filename"],
#                 "size": offset,
#                 "storage_type": FileRecord.STORAGE_R2,
#                 "final_path": final_key,
#                 "manifest_path": manifest_key,
#                 "security_mode": manifest["security_mode"],
#                 "deleted_at": None,
#             },
#         )

#         quota.used_bytes += offset
#         quota.save(update_fields=["used_bytes"])

#     return JsonResponse({"status": 1})
# # ============================================================
# # LIST FILES
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_files(request):
#     auto_purge_trash(request.user)

#     files = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "filename": f.filename,
#             "size": f.size,
#             "security_mode": f.security_mode,
#         }
#         for f in files
#     ], safe=False)

# # ============================================================
# # LIST TRASH
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_trash(request):
#     auto_purge_trash(request.user)

#     trash = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=False,
#     )

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "filename": f.filename,
#             "size": f.size,
#             "deleted_at": f.deleted_at.isoformat(),
#         }
#         for f in trash
#     ], safe=False)

# # ============================================================
# # DELETE (MOVE TO TRASH)
# # ============================================================

# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated])
# def delete_upload(request, upload_id):
#     with transaction.atomic():
#         f = get_object_or_404(
#             FileRecord,
#             upload_id=upload_id,
#             owner=request.user,
#             deleted_at__isnull=True,
#         )

#         quota = get_or_create_quota(request.user)
#         quota.used_bytes = max(0, quota.used_bytes - f.size)
#         quota.save(update_fields=["used_bytes"])

#         f.deleted_at = timezone.now()
#         f.save(update_fields=["deleted_at"])

#     return JsonResponse({"status": 1})

# # ============================================================
# # RESTORE FROM TRASH
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def restore_upload(request, file_id):
#     with transaction.atomic():
#         f = get_object_or_404(
#             FileRecord,
#             id=file_id,
#             owner=request.user,
#             deleted_at__isnull=False,
#         )

#         quota = get_or_create_quota(request.user)
#         if quota.used_bytes + f.size > quota.limit_bytes:
#             return JsonResponse(
#                 {"error": "Not enough quota"},
#                 status=403,
#             )

#         f.deleted_at = None
#         f.save(update_fields=["deleted_at"])

#         quota.used_bytes += f.size
#         quota.save(update_fields=["used_bytes"])

#     return JsonResponse({"status": 1})

# # ============================================================
# # DOWNLOAD (ENCRYPTED ONLY)
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def download_file(request, file_id):
#     f = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     r2 = R2Storage()
#     stream, _ = r2.open_stream(f.final_path)

#     return FileResponse(
#         stream,
#         as_attachment=True,
#         filename=f.filename,
#         content_type="application/octet-stream",
#     )

# # ============================================================
# # FETCH MANIFEST (FOR CLIENT DECRYPT)
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def fetch_manifest(request, file_id):
#     file = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     r2 = R2Storage()
#     manifest = r2.get_json(file.manifest_path)
#     return JsonResponse(manifest)
# # ============================================================
    
# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def reset_uploads(request):
#     """
#     DEV ONLY: Remove unfinished uploads from local disk.
#     """
#     user_id = str(request.user.id)
#     base = os.path.join(settings.MEDIA_ROOT, "uploads", user_id)

#     if not os.path.exists(base):
#         return JsonResponse({"status": "nothing to reset"})

#     removed = 0
#     for name in os.listdir(base):
#         path = os.path.join(base, name)
#         if os.path.isdir(path):
#             shutil.rmtree(path, ignore_errors=True)
#             removed += 1

#     return JsonResponse({
#         "status": "ok",
#         "removed_uploads": removed,
#     })

# #===========================================================
# # used to fetch _encrypted_data
# # ==========================================================
# from django.views.decorators.http import require_GET

# @require_GET
# def fetch_encrypted_data(request, file_id):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "auth required"}, status=401)

#     file = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=user,
#         deleted_at__isnull=True,
#     )

#     # Always encrypted
#     if file.storage_type == FileRecord.STORAGE_LOCAL:
#         return FileResponse(
#             open(file.final_path, "rb"),
#             content_type="application/octet-stream",
#         )

#     # R2
#     r2 = R2Storage()
#     stream, _ = r2.open_stream(file.final_path)

#     response = FileResponse(stream)
#     response["Content-Type"] = "application/octet-stream"
#     response["X-Content-Type-Options"] = "nosniff"
#     return response


# # ============================================================
# ============================================================
# project_name/users/views.py
# import os
# import json
# import uuid
# import math
# import hashlib
# from datetime import timedelta

# from django.conf import settings
# from django.http import JsonResponse, FileResponse
# from django.shortcuts import get_object_or_404
# from django.utils import timezone
# from django.db import transaction

# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework_simplejwt.authentication import JWTAuthentication

# from .models import FileRecord, UserQuota
# from .r2_storage import R2Storage


# #config 
# TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 30)
# DEFAULT_QUOTA_BYTES = 1 * 1024 * 1024 * 1024  # 1GB
# TMP_DIR = os.path.join(settings.MEDIA_ROOT, "tmp")
# os.makedirs(TMP_DIR, exist_ok=True)

# #auth with drf only jwt
# def get_authenticated_user(request):
#     try:
#         auth = JWTAuthentication()
#         result = auth.authenticate(request)
#         return result[0] if result else None
#     except Exception:
#         return None
# #Manifest hash
# def compute_manifest_hash(manifest: dict) -> str:
#     raw = json.dumps(
#         manifest,
#         sort_keys=True,
#         separators=(",", ":"),
#     ).encode()
#     return hashlib.sha256(raw).hexdigest()

# #quota
# def get_or_create_quota(user):
#     quota, _ = UserQuota.objects.get_or_create(
#         user=user,
#         defaults={
#             "limit_bytes": DEFAULT_QUOTA_BYTES,
#             "used_bytes": 0,
#             "reserved_bytes": 0,
#         },
#     )
#     return quota
# #reserve quota
# def reserve_quota(user, expected_size: int):
#     with transaction.atomic():
#         quota = UserQuota.objects.select_for_update().get(user=user)

#         if quota.used_bytes + quota.reserved_bytes + expected_size > quota.limit_bytes:
#             raise PermissionError("Storage quota exceeded")

#         quota.reserved_bytes += expected_size
#         quota.save(update_fields=["reserved_bytes"])
# #finalize quota 
# def finalize_quota(user, expected_size: int, actual_size: int):
#     with transaction.atomic():
#         quota = UserQuota.objects.select_for_update().get(user=user)

#         quota.reserved_bytes -= expected_size
#         quota.used_bytes += actual_size

#         quota.save(update_fields=["reserved_bytes", "used_bytes"])
# #releze quota
# def release_quota(user, expected_size: int):
#     with transaction.atomic():
#         quota = UserQuota.objects.select_for_update().get(user=user)
#         quota.reserved_bytes = max(0, quota.reserved_bytes - expected_size)
#         quota.save(update_fields=["reserved_bytes"])


# #quota APi
# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def get_storage_quota(request):
#     quota = get_or_create_quota(request.user)

#     return Response({
#         "used_bytes": quota.used_bytes,
#         "reserved_bytes": quota.reserved_bytes,
#         "limit_bytes": quota.limit_bytes,
#         "percent": int((quota.used_bytes / quota.limit_bytes) * 100)
#         if quota.limit_bytes else 0,
#     })

# #start upload
# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def start_upload(request):
#     body = request.data

#     filename = body.get("filename")
#     size = int(body.get("size", 0))
#     chunk_size = int(body.get("chunk_size", 2 * 1024 * 1024))
#     security_mode = body.get("security_mode")

#     if not filename or size <= 0:
#         return JsonResponse({"error": "Invalid file"}, status=400)

#     try:
#         reserve_quota(request.user, size)
#     except PermissionError as e:
#         return JsonResponse({"error": str(e)}, status=403)

#     upload_id = uuid.uuid4()
#     base_key = f"user/{request.user.id}/{upload_id}"

#     manifest = {
#         "version": 1,
#         "upload_id": str(upload_id),
#         "filename": filename,
#         "file_size": size,
#         "chunk_size": chunk_size,
#         "security_mode": security_mode,
#         "chunks": [],
#     }

#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2 = R2Storage()
#     r2.upload_json(manifest, f"{base_key}/manifest.json")

#     return JsonResponse({"upload_id": str(upload_id)})

# #resume upload
# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def start_upload(request):
#     body = request.data

#     filename = body.get("filename")
#     size = int(body.get("size", 0))
#     chunk_size = int(body.get("chunk_size", 2 * 1024 * 1024))
#     security_mode = body.get("security_mode")

#     if not filename or size <= 0:
#         return JsonResponse({"error": "Invalid file"}, status=400)

#     try:
#         reserve_quota(request.user, size)
#     except PermissionError as e:
#         return JsonResponse({"error": str(e)}, status=403)

#     upload_id = uuid.uuid4()
#     base_key = f"user/{request.user.id}/{upload_id}"

#     manifest = {
#         "version": 1,
#         "upload_id": str(upload_id),
#         "filename": filename,
#         "file_size": size,
#         "chunk_size": chunk_size,
#         "security_mode": security_mode,
#         "chunks": [],
#     }

#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2 = R2Storage()
#     r2.upload_json(manifest, f"{base_key}/manifest.json")

#     return JsonResponse({"upload_id": str(upload_id)})


# #upload chunk
# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def upload_chunk_xchacha(request, upload_id, index):
#     blob = request.FILES.get("chunk")
#     if not blob:
#         return JsonResponse({"error": "Missing chunk"}, status=400)

#     nonce = request.headers.get("X-Chunk-Nonce")
#     mac = request.headers.get("X-Chunk-Mac")
#     if not nonce or not mac:
#         return JsonResponse({"error": "Missing crypto headers"}, status=400)

#     data = blob.read()
#     sha = hashlib.sha256(data).hexdigest()

#     r2 = R2Storage()
#     base_key = f"user/{request.user.id}/{upload_id}"

#     r2.upload_bytes(data, f"{base_key}/chunks/chunk_{index}.bin")

#     manifest = r2.get_json(f"{base_key}/manifest.json")

#     manifest["chunks"] = [
#         c for c in manifest["chunks"] if c["index"] != index
#     ] + [{
#         "index": index,
#         "ciphertext_size": len(data),
#         "ciphertext_sha256": sha,
#         "nonce": nonce,
#         "mac": mac,
#     }]

#     manifest["chunks"].sort(key=lambda c: c["index"])
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2.upload_json(manifest, f"{base_key}/manifest.json")

#     return JsonResponse({"stored": True})

# #finish upload
# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def finish_upload(request, upload_id):
#     r2 = R2Storage()
#     base_key = f"user/{request.user.id}/{upload_id}"

#     manifest = r2.get_json(f"{base_key}/manifest.json")
#     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

#     tmp_dir = os.path.join(TMP_DIR, str(upload_id))
#     os.makedirs(tmp_dir, exist_ok=True)
#     final_path = os.path.join(tmp_dir, "final.bin")

#     actual_size = 0

#     try:
#         with open(final_path, "wb") as out:
#             for c in chunks:
#                 data = r2.open_stream(
#                     f"{base_key}/chunks/chunk_{c['index']}.bin"
#                 )[0].read()

#                 if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
#                     raise ValueError("Integrity failure")

#                 c["offset"] = actual_size
#                 actual_size += len(data)
#                 out.write(data)

#         manifest["chunks"] = chunks
#         manifest["server_hash"] = compute_manifest_hash(manifest)
#         r2.upload_json(manifest, f"{base_key}/manifest.json")

#         with open(final_path, "rb") as f:
#             r2.upload_bytes(f.read(), f"{base_key}/final.bin")

#         finalize_quota(
#             request.user,
#             manifest["file_size"],
#             actual_size,
#         )

#         FileRecord.objects.update_or_create(
#             upload_id=upload_id,
#             owner=request.user,
#             defaults={
#                 "filename": manifest["filename"],
#                 "size": actual_size,
#                 "storage_type": FileRecord.STORAGE_R2,
#                 "final_path": f"{base_key}/final.bin",
#                 "manifest_path": f"{base_key}/manifest.json",
#                 "security_mode": manifest["security_mode"],
#                 "deleted_at": None,
#             },
#         )

#     except Exception:
#         release_quota(request.user, manifest["file_size"])
#         raise

#     return JsonResponse({"status": "ok"})

# #list files
# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_files(request):
#     files = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "filename": f.filename,
#             "size": f.size,
#             "security_mode": f.security_mode,
#         }
#         for f in files
#     ], safe=False)

# #delete
# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated])
# def delete_upload(request, upload_id):
#     with transaction.atomic():
#         f = get_object_or_404(
#             FileRecord,
#             upload_id=upload_id,
#             owner=request.user,
#             deleted_at__isnull=True,
#         )

#         quota = get_or_create_quota(request.user)
#         quota.used_bytes = max(0, quota.used_bytes - f.size)
#         quota.save(update_fields=["used_bytes"])

#         f.deleted_at = timezone.now()
#         f.save(update_fields=["deleted_at"])

#     return JsonResponse({"status": 1})

# #fetch maniefest
# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def fetch_manifest(request, file_id):
#     f = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     r2 = R2Storage()
#     manifest = r2.get_json(f.manifest_path)
#     return JsonResponse(manifest)


# #fetch data
# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def fetch_encrypted_data(request, file_id):
#     f = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     r2 = R2Storage()
#     stream, _ = r2.open_stream(f.final_path)

#     response = FileResponse(stream)
#     response["Content-Type"] = "application/octet-stream"
#     response["X-Content-Type-Options"] = "nosniff"
#     return response


# files/views.py

# import os
# import json
# import math
# import uuid
# import hashlib
# import shutil
# from datetime import timedelta

# from django.conf import settings
# from django.http import JsonResponse, FileResponse
# from django.shortcuts import get_object_or_404
# from django.utils import timezone
# from django.db import transaction
# from django.views.decorators.http import require_http_methods, require_GET

# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework_simplejwt.authentication import JWTAuthentication
# from rest_framework.response import Response

# from .models import FileRecord, UserQuota
# from .r2_storage import R2Storage


# # ============================================================
# # CONFIG
# # ============================================================

# TRASH_RETENTION_DAYS = 30
# DEFAULT_QUOTA_BYTES = 1 * 1024 * 1024 * 1024  # 1GB


# # ============================================================
# # HELPERS
# # ============================================================

# def upload_tmp_dir(user_id: str, upload_id: str) -> str:
#     return os.path.join(settings.MEDIA_ROOT, "uploads", user_id, upload_id)


# def compute_manifest_hash(manifest: dict) -> str:
#     raw = json.dumps(
#         manifest,
#         sort_keys=True,
#         separators=(",", ":"),
#     ).encode()
#     return hashlib.sha256(raw).hexdigest()


# def get_authenticated_user(request):
#     auth = JWTAuthentication()
#     res = auth.authenticate(request)
#     return res[0] if res else None


# def get_or_create_quota(user) -> UserQuota:
#     quota, _ = UserQuota.objects.get_or_create(
#         user=user,
#         defaults={"limit_bytes": DEFAULT_QUOTA_BYTES},
#     )
#     return quota


# # ============================================================
# # QUOTA
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def get_storage_quota(request):
#     quota = get_or_create_quota(request.user)

#     return Response({
#         "used_bytes": quota.used_bytes,
#         "limit_bytes": quota.limit_bytes,
#         "percent": int((quota.used_bytes / quota.limit_bytes) * 100)
#         if quota.limit_bytes > 0 else 0,
#     })


# # ============================================================
# # START UPLOAD
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def start_upload(request):
#     filename = request.data.get("filename")
#     size = int(request.data.get("size", 0))
#     chunk_size = int(request.data.get("chunk_size", 2 * 1024 * 1024))
#     security_mode = request.data.get("security_mode")

#     if not filename or size <= 0:
#         return JsonResponse({"error": "Invalid input"}, status=400)

#     quota = get_or_create_quota(request.user)
#     if not quota.can_store(size):
#         return JsonResponse({"error": "Quota exceeded"}, status=403)

#     upload_id = uuid.uuid4()
#     base_key = f"user/{request.user.id}/{upload_id}"

#     manifest = {
#         "version": 1,
#         "filename": filename,
#         "file_size": size,
#         "chunk_size": chunk_size,
#         "security_mode": security_mode,
#         "chunks": [],
#     }
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2 = R2Storage()
#     r2.upload_json(manifest, f"{base_key}/manifest.json")

#     return JsonResponse({"upload_id": str(upload_id)})


# # ============================================================
# # RESUME UPLOAD
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def resume_upload(request, upload_id):
#     r2 = R2Storage()
#     key = f"user/{request.user.id}/{upload_id}/manifest.json"

#     try:
#         manifest = r2.get_json(key)
#     except Exception:
#         return JsonResponse({"uploaded_indices": []})

#     return JsonResponse({
#         "uploaded_indices": [c["index"] for c in manifest["chunks"]],
#         "chunk_size": manifest["chunk_size"],
#         "total_chunks": math.ceil(
#             manifest["file_size"] / manifest["chunk_size"]
#         ),
#     })


# # ============================================================
# # UPLOAD CHUNK
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def upload_chunk_xchacha(request, upload_id, index):
#     blob = request.FILES.get("chunk")
#     nonce = request.headers.get("X-Chunk-Nonce")
#     mac = request.headers.get("X-Chunk-Mac")

#     if not blob or not nonce or not mac:
#         return JsonResponse({"error": "Missing data"}, status=400)

#     data = blob.read()
#     sha = hashlib.sha256(data).hexdigest()

#     r2 = R2Storage()
#     base_key = f"user/{request.user.id}/{upload_id}"

#     # upload chunk
#     r2.upload_bytes(
#         data,
#         f"{base_key}/chunks/chunk_{index}.bin",
#     )

#     manifest = r2.get_json(f"{base_key}/manifest.json")

#     manifest["chunks"] = [
#         c for c in manifest["chunks"] if c["index"] != index
#     ] + [{
#         "index": index,
#         "ciphertext_size": len(data),
#         "ciphertext_sha256": sha,
#         "nonce": nonce,
#         "mac": mac,
#     }]

#     manifest["chunks"].sort(key=lambda c: c["index"])
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2.upload_json(manifest, f"{base_key}/manifest.json")

#     return JsonResponse({"stored": True})


# # ============================================================
# # FINISH UPLOAD
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def finish_upload(request, upload_id):
#     r2 = R2Storage()
#     base_key = f"user/{request.user.id}/{upload_id}"

#     manifest = r2.get_json(f"{base_key}/manifest.json")
#     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

#     tmp_dir = upload_tmp_dir(str(request.user.id), str(upload_id))
#     os.makedirs(tmp_dir, exist_ok=True)

#     final_path = os.path.join(tmp_dir, "final.bin")
#     offset = 0

#     with open(final_path, "wb") as out:
#         for c in chunks:
#             data = r2.client.get_object(
#                 Bucket=r2.bucket,
#                 Key=f"{base_key}/chunks/chunk_{c['index']}.bin",
#             )["Body"].read()

#             if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
#                 return JsonResponse({"error": "Integrity failure"}, status=400)

#             c["offset"] = offset
#             offset += len(data)
#             out.write(data)

#     manifest["chunks"] = chunks
#     manifest["server_hash"] = compute_manifest_hash(manifest)
#     r2.upload_json(manifest, f"{base_key}/manifest.json")

#     r2.upload_bytes(
#         open(final_path, "rb").read(),
#         f"{base_key}/final.bin",
#     )

#     with transaction.atomic():
#         quota = UserQuota.objects.select_for_update().get(user=request.user)
#         if not quota.can_store(offset):
#             return JsonResponse({"error": "Quota exceeded"}, status=403)

#         FileRecord.objects.create(
#             owner=request.user,
#             upload_id=upload_id,
#             filename=manifest["filename"],
#             size=offset,
#             storage_type=FileRecord.STORAGE_R2,
#             final_path=f"{base_key}/final.bin",
#             manifest_path=f"{base_key}/manifest.json",
#             security_mode=manifest["security_mode"],
#         )

#         quota.consume(offset)

#     shutil.rmtree(tmp_dir, ignore_errors=True)

#     return JsonResponse({"status": 1})


# # ============================================================
# # LIST FILES
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_files(request):
#     files = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "upload_id": str(f.upload_id),
#             "filename": f.filename,
#             "size": f.size,
#         }
#         for f in files
#     ], safe=False)


# # ============================================================
# # DELETE
# # ============================================================

# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated])
# def delete_upload(request, upload_id):
#     with transaction.atomic():
#         f = get_object_or_404(
#             FileRecord,
#             upload_id=upload_id,
#             owner=request.user,
#             deleted_at__isnull=True,
#         )

#         quota = UserQuota.objects.select_for_update().get(user=request.user)
#         quota.release(f.size)

#         f.deleted_at = timezone.now()
#         f.save(update_fields=["deleted_at"])

#     return JsonResponse({"status": 1})

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_trash(request):
#     files = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=False,
#     ).order_by("-deleted_at")

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "upload_id": str(f.upload_id),
#             "filename": f.filename,
#             "size": f.size,
#             "deleted_at": f.deleted_at.isoformat(),
#         }
#         for f in files
#     ], safe=False)

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def restore_upload(request, file_id):
#     with transaction.atomic():
#         f = get_object_or_404(
#             FileRecord,
#             id=file_id,
#             owner=request.user,
#             deleted_at__isnull=False,
#         )

#         quota = get_or_create_quota(request.user)

#         if quota.used_bytes + f.size > quota.limit_bytes:
#             return JsonResponse(
#                 {"error": "Not enough quota"},
#                 status=403,
#             )

#         f.deleted_at = None
#         f.save(update_fields=["deleted_at"])

#         quota.used_bytes += f.size
#         quota.save(update_fields=["used_bytes"])

#     return JsonResponse({"status": 1})



# from django.http import StreamingHttpResponse
# from django.utils.http import http_date
# from wsgiref.util import FileWrapper

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def stream_encrypted_file(request, file_id):
#     file = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     r2 = R2Storage()
#     stream, total_size = r2.open_stream(file.final_path)

#     response = StreamingHttpResponse(
#         FileWrapper(stream),
#         content_type="application/octet-stream",
#     )

#     response["Accept-Ranges"] = "bytes"
#     response["Content-Length"] = str(total_size)
#     response["Cache-Control"] = "no-store"
#     response["X-Content-Type-Options"] = "nosniff"
#     response["Last-Modified"] = http_date()

#     return response

# # import json
# # from pathlib import Path

# # from django.http import JsonResponse, FileResponse, Http404
# # from django.contrib.auth.decorators import login_required
# # from django.views.decorators.http import require_GET

# # from .models import FileRecord


# # # def _assert_file_access(request, file_id) -> FileRecord:
# # #     """
# # #     Centralized access check.
# # #     """
# # #     try:
# # #         return FileRecord.objects.get(
# # #             id=file_id,
# # #             owner=request.user,
# # #             deleted_at__isnull=True,
# # #         )
# # #     except FileRecord.DoesNotExist:
# # #         raise Http404("File not found")


# # # @require_GET
# # # @permission_classes([IsAuthenticated])
# # # def file_manifest(request, file_id):
# # #     """
# # #     Returns encrypted manifest as-is.
# # #     Server does NOT parse or modify crypto fields.
# # #     """
# # #     file = _assert_file_access(request, file_id)

# # #     if not file.manifest_path:
# # #         raise Http404("Manifest not available")

# # #     manifest_path = Path(file.manifest_path)

# # #     if not manifest_path.exists():
# # #         raise Http404("Manifest missing on disk")

# # #     with manifest_path.open("r", encoding="utf-8") as f:
# # #         manifest = json.load(f)

# # #     return JsonResponse(manifest, safe=True)


# # # @require_GET
# # # @permission_classes([IsAuthenticated])
# # # def file_data(request, file_id):
# # #     """
# # #     Streams encrypted final.bin.
# # #     """
# # #     file = _assert_file_access(request, file_id)

# # #     data_path = Path(file.final_path)

# # #     if not data_path.exists():
# # #         raise Http404("Encrypted data missing")

# # #     return FileResponse(
# # #         open(data_path, "rb"),
# # #         content_type="application/octet-stream",
# # #     )

# # ============================================================
# # PREVIEW ENDPOINTS (R2-AWARE)
# # ============================================================

# from rest_framework.views import APIView
# from rest_framework.permissions import IsAuthenticated
# from rest_framework_simplejwt.authentication import JWTAuthentication
# from django.http import Http404, StreamingHttpResponse
# from wsgiref.util import FileWrapper


# class FileManifestView(APIView):
#     authentication_classes = [JWTAuthentication]
#     permission_classes = [IsAuthenticated]

#     def get(self, request, file_id):
#         try:
#             file = FileRecord.objects.get(
#                 id=file_id,
#                 owner=request.user,
#                 deleted_at__isnull=True,
#             )
#         except FileRecord.DoesNotExist:
#             raise Http404("File not found")

#         if not file.manifest_path:
#             raise Http404("Manifest missing")

#         if file.storage_type == FileRecord.STORAGE_R2:
#             r2 = R2Storage()
#             try:
#                 manifest = r2.get_json(file.manifest_path)
#             except Exception:
#                 raise Http404("Manifest missing")
#             return Response(manifest)

#         raise Http404("Unsupported storage type")


# class FileDataView(APIView):
#     authentication_classes = [JWTAuthentication]
#     permission_classes = [IsAuthenticated]

#     def get(self, request, file_id):
#         try:
#             file = FileRecord.objects.get(
#                 id=file_id,
#                 owner=request.user,
#                 deleted_at__isnull=True,
#             )
#         except FileRecord.DoesNotExist:
#             raise Http404("File not found")

#         if not file.final_path:
#             raise Http404("Encrypted data missing")

#         if file.storage_type == FileRecord.STORAGE_R2:
#             r2 = R2Storage()
#             stream, total_size = r2.open_stream(file.final_path)

#             response = StreamingHttpResponse(
#                 FileWrapper(stream),
#                 content_type="application/octet-stream",
#             )
#             response["Content-Length"] = str(total_size)
#             response["Cache-Control"] = "no-store"
#             response["X-Content-Type-Options"] = "nosniff"
#             return response

#         raise Http404("Unsupported storage type")



# ==================================================================================================
# # files/views.py

# from datetime import timedelta
# import os
# import json
# import math
# import hashlib
# import shutil

# from django.conf import settings
# from django.http import JsonResponse, StreamingHttpResponse, Http404
# from django.db import transaction
# from django.shortcuts import get_object_or_404
# from django.utils.http import http_date
# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework_simplejwt.authentication import JWTAuthentication
# from wsgiref.util import FileWrapper
# from django.utils import timezone 

# from .models import FileRecord, UserQuota
# from .r2_storage import R2Storage


# # ============================================================
# # HELPERS
# # ============================================================

# def r2_base(user_id, file_id):
#     return f"Silvora/users/{user_id}/files/{file_id}"


# def compute_manifest_hash(manifest: dict) -> str:
#     raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
#     return hashlib.sha256(raw).hexdigest()


# def get_or_create_quota(user):
#     quota, _ = UserQuota.objects.get_or_create(
#         user=user,
#         defaults={"limit_bytes": 1 * 1024 * 1024 * 1024},
#     )
#     return quota

# def _auto_purge_trash_for_user(user):
#     cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
#     old = FileRecord.objects.filter(
#         owner=user,
#         deleted_at__lt=cutoff
#     )
#     for r in old:
#         _purge_file_record(r)

# def _purge_file_record(file_record):
#     file_record.delete()
    


# # ============================================================
# # START UPLOAD
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def start_upload(request):
#     filename = request.data.get("filename")
#     size = int(request.data.get("size", 0))
#     chunk_size = int(request.data.get("chunk_size", 2 * 1024 * 1024))
#     security_mode = request.data.get("security_mode")

#     if not filename or size <= 0:
#         return JsonResponse({"error": "Invalid input"}, status=400)

#     quota = get_or_create_quota(request.user)
#     if not quota.can_store(size):
#         return JsonResponse({"error": "Quota exceeded"}, status=403)

#     file = FileRecord.objects.create(
#         owner=request.user,
#         filename=filename,
#         size=0,
#         security_mode=security_mode,
#         storage_type=FileRecord.STORAGE_R2,
#     )

#     manifest = {
#         "version": 1,
#         "file_id": str(file.id),
#         "filename": filename,
#         "file_size": size,
#         "chunk_size": chunk_size,
#         "security_mode": security_mode,
#         "chunks": [],
#     }
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2 = R2Storage()
#     r2.upload_json(manifest, f"{r2_base(request.user.id, file.id)}/manifest.json")

#     file.manifest_path = f"{r2_base(request.user.id, file.id)}/manifest.json"
#     file.final_path = f"{r2_base(request.user.id, file.id)}/final.bin"
#     file.save(update_fields=["manifest_path", "final_path"])

#     return JsonResponse({"file_id": str(file.id)})


# # ============================================================
# # RESUME UPLOAD
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def resume_upload(request, file_id):
#     r2 = R2Storage()
#     key = f"{r2_base(request.user.id, file_id)}/manifest.json"

#     try:
#         manifest = r2.get_json(key)
#     except Exception:
#         return JsonResponse({"uploaded_indices": []})

#     return JsonResponse({
#         "uploaded_indices": [c["index"] for c in manifest["chunks"]],
#         "chunk_size": manifest["chunk_size"],
#         "total_chunks": math.ceil(
#             manifest["file_size"] / manifest["chunk_size"]
#         ),
#     })


# # ============================================================
# # UPLOAD CHUNK
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def upload_chunk(request, file_id, index):
#     blob = request.FILES.get("chunk")
#     nonce = request.headers.get("X-Chunk-Nonce")
#     mac = request.headers.get("X-Chunk-Mac")

#     if not blob or not nonce or not mac:
#         return JsonResponse({"error": "Missing data"}, status=400)

#     data = blob.read()
#     sha = hashlib.sha256(data).hexdigest()

#     r2 = R2Storage()
#     base = r2_base(request.user.id, file_id)

#     r2.upload_bytes(data, f"{base}/chunks/chunk_{index}.bin")

#     manifest = r2.get_json(f"{base}/manifest.json")

#     manifest["chunks"] = [
#         c for c in manifest["chunks"] if c["index"] != index
#     ] + [{
#         "index": index,
#         "ciphertext_size": len(data),
#         "ciphertext_sha256": sha,
#         "nonce": nonce,
#         "mac": mac,
#     }]

#     manifest["chunks"].sort(key=lambda c: c["index"])
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2.upload_json(manifest, f"{base}/manifest.json")

#     return JsonResponse({"stored": True})


# # ============================================================
# # FINISH UPLOAD
# # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def finish_upload(request, file_id):
#     r2 = R2Storage()
#     base = r2_base(request.user.id, file_id)

#     manifest = r2.get_json(f"{base}/manifest.json")
#     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

#     final_data = bytearray()
#     offset = 0

#     for c in chunks:
#         data = r2.client.get_object(
#             Bucket=r2.bucket,
#             Key=f"{base}/chunks/chunk_{c['index']}.bin",
#         )["Body"].read()

#         if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
#             return JsonResponse({"error": "Integrity failure"}, status=400)

#         c["offset"] = offset
#         offset += len(data)
#         final_data.extend(data)

#     manifest["chunks"] = chunks
#     manifest["server_hash"] = compute_manifest_hash(manifest)

#     r2.upload_bytes(final_data, f"{base}/final.bin")
#     r2.upload_json(manifest, f"{base}/manifest.json")

#     with transaction.atomic():
#         file = FileRecord.objects.select_for_update().get(
#             id=file_id, owner=request.user
#         )
#         quota = UserQuota.objects.select_for_update().get(user=request.user)

#         if not quota.can_store(len(final_data)):
#             return JsonResponse({"error": "Quota exceeded"}, status=403)

#         file.size = len(final_data)
#         file.save(update_fields=["size"])
#         quota.consume(len(final_data))

#     return JsonResponse({"status": "complete"})


# # ============================================================
# # PREVIEW ENDPOINTS
# # ============================================================

# class FileManifestView(APIView):
#     authentication_classes = [JWTAuthentication]
#     permission_classes = [IsAuthenticated]

#     def get(self, request, file_id):
#         file = FileRecord.objects.get(id=file_id, owner=request.user)
#         r2 = R2Storage()
#         return Response(r2.get_json(file.manifest_path))


# class FileDataView(APIView):
#     authentication_classes = [JWTAuthentication]
#     permission_classes = [IsAuthenticated]

#     def get(self, request, file_id):
#         file = FileRecord.objects.get(id=file_id, owner=request.user)
#         r2 = R2Storage()
#         stream, size = r2.open_stream(file.final_path)

#         response = StreamingHttpResponse(
#             FileWrapper(stream),
#             content_type="application/octet-stream",
#         )
#         response["Content-Length"] = str(size)
#         response["Cache-Control"] = "no-store"
#         response["Last-Modified"] = http_date()
#         return response


# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_files(request):
#     files = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "upload_id": str(f.upload_id),
#             "filename": f.filename,
#             "size": f.size,
#         }
#         for f in files
#     ], safe=False)
    
    

# # ============================================================
# # QUOTA
# # ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def get_storage_quota(request):
#     quota = get_or_create_quota(request.user)

#     return Response({
#         "used_bytes": quota.used_bytes,
#         "limit_bytes": quota.limit_bytes,
#         "percent": int((quota.used_bytes / quota.limit_bytes) * 100)
#         if quota.limit_bytes > 0 else 0,
#     })
    
    
# #========================================================
# # Soft delete 
# #========================================================
# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated])
# def delete_upload(request, upload_id):
#     with transaction.atomic():
#         f = get_object_or_404(
#             FileRecord,
#             upload_id=upload_id,
#             owner=request.user,
#             deleted_at__isnull=True,
#         )

#         quota = UserQuota.objects.select_for_update().get(user=request.user)
#         quota.release(f.size)

#         f.mark_deleted(retention_days=30)

#     return JsonResponse({"status": 1})

# # ============================================================
# # # LIST TRASH
# # # ============================================================

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

# # # ============================================================
# # # DELETE (MOVE TO TRASH)
# # # ============================================================

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

# #         quota = get_or_create_quota(request.user)
# #         quota.used_bytes = max(0, quota.used_bytes - f.size)
# #         quota.save(update_fields=["used_bytes"])

# #         f.deleted_at = timezone.now()
# #         f.save(update_fields=["deleted_at"])

# #     return JsonResponse({"status": 1})




# # ============================================================
# # CONFIG
# # ============================================================

# TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 7)

# # ============================================================
# # HELPERS
# # ============================================================

# def upload_base_dir(user_id: str, upload_id: str) -> str:
#     return os.path.join(settings.MEDIA_ROOT, "uploads", user_id, upload_id)

# def trash_base_dir(user_id: str, upload_id: str) -> str:
#     return os.path.join(settings.MEDIA_ROOT, "trash", user_id, upload_id)

# def compute_manifest_server_hash(manifest: dict) -> str:
#     raw = json.dumps(
#         manifest,
#         sort_keys=True,
#         separators=(",", ":"),
#         ensure_ascii=False,
#     ).encode("utf-8")
#     return hashlib.sha256(raw).hexdigest()

# def get_authenticated_user(request):
#     auth = JWTAuthentication()
#     try:
#         res = auth.authenticate(request)
#         if not res:
#             return None
#         user, _ = res
#         return user
#     except Exception:
#         return None

# def _purge_file_record(record: FileRecord):
#     user_id = str(record.owner_id)
#     upload_id = str(record.upload_id)

#     shutil.rmtree(upload_base_dir(user_id, upload_id), ignore_errors=True)
#     shutil.rmtree(trash_base_dir(user_id, upload_id), ignore_errors=True)

#     record.delete()

# def _auto_purge_trash_for_user(user):
#     cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
#     old = FileRecord.objects.filter(owner=user, deleted_at__lt=cutoff)
#     for r in old:
#         _purge_file_record(r)








# # # ============================================================
# # # RESTORE FROM TRASH
# # # ============================================================

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def restore_upload(request, file_id):
#     with transaction.atomic():
#         f = get_object_or_404(
#             FileRecord,
#             id=file_id,
#             owner=request.user,
#             deleted_at__isnull=False,
#         )

#         quota = get_or_create_quota(request.user)
#         if quota.used_bytes + f.size > quota.limit_bytes:
#             return JsonResponse(
#                 {"error": "Not enough quota"},
#                 status=403,
#             )

#         f.deleted_at = None
#         f.save(update_fields=["deleted_at"])

#         quota.used_bytes += f.size
#         quota.save(update_fields=["used_bytes"])

#     return JsonResponse({"status": 1})



# # ============================================================
# # TRASH
# # ============================================================

# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated])
# def delete_file(request, file_id):
#     with transaction.atomic():
#         f = FileRecord.objects.select_for_update().get(
#             id=file_id,
#             owner=request.user,
#             deleted_at__isnull=True,
#         )

#         quota = UserQuota.objects.select_for_update().get(user=request.user)
#         quota.release(f.size)

#         f.mark_deleted(retention_days=TRASH_RETENTION_DAYS)

#     return JsonResponse({"status": 1})


# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def list_trash(request):
#     _auto_purge_trash_for_user(request.user)

#     trash = FileRecord.objects.filter(
#         owner=request.user,
#         deleted_at__isnull=False,
#     )

#     return JsonResponse([
#         {
#             "file_id": str(f.id),
#             "filename": f.filename,
#             "size": f.size,
#             "deleted_at": f.deleted_at.isoformat(),
#         }
#         for f in trash
#     ], safe=False)

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def restore_upload(request, file_id):
#     with transaction.atomic():
#         f = FileRecord.objects.select_for_update().get(
#             id=file_id,
#             owner=request.user,
#             deleted_at__isnull=False,
#         )

#         quota = get_or_create_quota(request.user)
#         if not quota.can_store(f.size):
#             return JsonResponse({"error": "Quota exceeded"}, status=403)

#         f.restore()
#         quota.consume(f.size)

#     return JsonResponse({"status": 1})




# ==============================================

from datetime import timedelta
import json
import math
import hashlib

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.http import http_date
from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from wsgiref.util import FileWrapper

from .models import FileRecord, UserQuota
from .r2_storage import R2Storage


# ============================================================
# CONFIG
# ============================================================

TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 7)


# ============================================================
# HELPERS
# ============================================================

def r2_base(user_id, file_id):
    return f"Silvora/users/{user_id}/files/{file_id}"


def compute_manifest_hash(manifest: dict) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def get_or_create_quota(user):
    quota, _ = UserQuota.objects.get_or_create(
        user=user,
        defaults={"limit_bytes": 1 * 1024 * 1024 * 1024},
    )
    return quota


def _purge_file_record(record: FileRecord):
    """
    Permanently remove file from DB.
    R2 cleanup can be added later (lifecycle rules recommended).
    """
    record.delete()


def _auto_purge_trash_for_user(user):
    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    old_files = FileRecord.objects.filter(
        owner=user,
        deleted_at__lt=cutoff,
    )
    for f in old_files:
        _purge_file_record(f)


# ============================================================
# UPLOAD FLOW
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def start_upload(request):
    filename = request.data.get("filename")
    size = int(request.data.get("size", 0))
    chunk_size = int(request.data.get("chunk_size", 2 * 1024 * 1024))
    security_mode = request.data.get("security_mode")

    if not filename or size <= 0:
        return JsonResponse({"error": "Invalid input"}, status=400)

    quota = get_or_create_quota(request.user)
    if not quota.can_store(size):
        return JsonResponse({"error": "Quota exceeded"}, status=403)

    file = FileRecord.objects.create(
        owner=request.user,
        filename=filename,
        size=0,
        security_mode=security_mode,
        storage_type=FileRecord.STORAGE_R2,
    )

    manifest = {
        "version": 1,
        "file_id": str(file.id),
        "filename": filename,
        "file_size": size,
        "chunk_size": chunk_size,
        "security_mode": security_mode,
        "chunks": [],
    }
    manifest["server_hash"] = compute_manifest_hash(manifest)

    r2 = R2Storage()
    base = r2_base(request.user.id, file.id)

    r2.upload_json(manifest, f"{base}/manifest.json")

    file.manifest_path = f"{base}/manifest.json"
    file.final_path = f"{base}/final.bin"
    file.save(update_fields=["manifest_path", "final_path"])

    return JsonResponse({"file_id": str(file.id)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resume_upload(request, file_id):
    r2 = R2Storage()
    key = f"{r2_base(request.user.id, file_id)}/manifest.json"

    try:
        manifest = r2.get_json(key)
    except Exception:
        return JsonResponse({"uploaded_indices": []})

    return JsonResponse({
        "uploaded_indices": [c["index"] for c in manifest["chunks"]],
        "chunk_size": manifest["chunk_size"],
        "total_chunks": math.ceil(
            manifest["file_size"] / manifest["chunk_size"]
        ),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upload_chunk(request, file_id, index):
    blob = request.FILES.get("chunk")
    nonce = request.headers.get("X-Chunk-Nonce")
    mac = request.headers.get("X-Chunk-Mac")

    if not blob or not nonce or not mac:
        return JsonResponse({"error": "Missing data"}, status=400)

    data = blob.read()
    sha = hashlib.sha256(data).hexdigest()

    r2 = R2Storage()
    base = r2_base(request.user.id, file_id)

    r2.upload_bytes(data, f"{base}/chunks/chunk_{index}.bin")

    manifest = r2.get_json(f"{base}/manifest.json")

    manifest["chunks"] = [
        c for c in manifest["chunks"] if c["index"] != index
    ] + [{
        "index": index,
        "ciphertext_size": len(data),
        "ciphertext_sha256": sha,
        "nonce": nonce,
        "mac": mac,
    }]

    manifest["chunks"].sort(key=lambda c: c["index"])
    manifest["server_hash"] = compute_manifest_hash(manifest)

    r2.upload_json(manifest, f"{base}/manifest.json")

    return JsonResponse({"stored": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def finish_upload(request, file_id):
    r2 = R2Storage()
    base = r2_base(request.user.id, file_id)

    manifest = r2.get_json(f"{base}/manifest.json")
    chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

    final_data = bytearray()
    offset = 0

    for c in chunks:
        data = r2.client.get_object(
            Bucket=r2.bucket,
            Key=f"{base}/chunks/chunk_{c['index']}.bin",
        )["Body"].read()

        if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
            return JsonResponse({"error": "Integrity failure"}, status=400)

        c["offset"] = offset
        offset += len(data)
        final_data.extend(data)

    manifest["chunks"] = chunks
    manifest["server_hash"] = compute_manifest_hash(manifest)

    r2.upload_bytes(final_data, f"{base}/final.bin")
    r2.upload_json(manifest, f"{base}/manifest.json")

    with transaction.atomic():
        file = get_object_or_404(FileRecord, id=file_id, owner=request.user)
        quota = UserQuota.objects.select_for_update().get(user=request.user)

        if not quota.can_store(len(final_data)):
            return JsonResponse({"error": "Quota exceeded"}, status=403)

        file.size = len(final_data)
        file.save(update_fields=["size"])
        quota.consume(len(final_data))

    return JsonResponse({"status": "complete"})


# ============================================================
# PREVIEW
# ============================================================

class FileManifestView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, file_id):
        file = get_object_or_404(FileRecord, id=file_id, owner=request.user)
        return Response(R2Storage().get_json(file.manifest_path))


class FileDataView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, file_id):
        file = get_object_or_404(FileRecord, id=file_id, owner=request.user)
        stream, size = R2Storage().open_stream(file.final_path)

        response = StreamingHttpResponse(
            FileWrapper(stream),
            content_type="application/octet-stream",
        )
        response["Content-Length"] = str(size)
        response["Cache-Control"] = "no-store"
        response["Last-Modified"] = http_date()
        return response


# ============================================================
# FILE LIST + QUOTA
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_files(request):
    files = FileRecord.objects.filter(
        owner=request.user,
        deleted_at__isnull=True,
    )

    return JsonResponse([
        {
            "file_id": str(f.id),
            "filename": f.filename,
            "size": f.size,
        }
        for f in files
    ], safe=False)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_storage_quota(request):
    quota = get_or_create_quota(request.user)
    return Response({
        "used_bytes": quota.used_bytes,
        "limit_bytes": quota.limit_bytes,
        "percent": int((quota.used_bytes / quota.limit_bytes) * 100),
    })


# ============================================================
# TRASH
# ============================================================

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_file(request, file_id):
    with transaction.atomic():
        f = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=request.user,
            deleted_at__isnull=True,
        )
        quota = UserQuota.objects.select_for_update().get(user=request.user)
        quota.release(f.size)
        f.mark_deleted(retention_days=TRASH_RETENTION_DAYS)

    return JsonResponse({"status": 1})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_trash(request):
    _auto_purge_trash_for_user(request.user)

    trash = FileRecord.objects.filter(
        owner=request.user,
        deleted_at__isnull=False,
    )

    return JsonResponse([
        {
            "file_id": str(f.id),
            "filename": f.filename,
            "size": f.size,
            "deleted_at": f.deleted_at.isoformat(),
        }
        for f in trash
    ], safe=False)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def restore_upload(request, file_id):
    with transaction.atomic():
        f = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=request.user,
            deleted_at__isnull=False,
        )

        quota = get_or_create_quota(request.user)
        if not quota.can_store(f.size):
            return JsonResponse({"error": "Quota exceeded"}, status=403)

        f.restore()
        quota.consume(f.size)

    return JsonResponse({"status": 1})
