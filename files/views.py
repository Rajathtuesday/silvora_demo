
# files/views.py

import os
import math
import json
import uuid
import shutil
import hashlib
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse, FileResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import FileRecord

from .r2_storage import R2Storage


# ============================================================
# CONFIG
# ============================================================

TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 30)

# ============================================================
# HELPERS
# ============================================================

def upload_base_dir(user_id: str, upload_id: str) -> str:
    return os.path.join(settings.MEDIA_ROOT, "uploads", user_id, upload_id)

def trash_base_dir(user_id: str, upload_id: str) -> str:
    return os.path.join(settings.MEDIA_ROOT, "trash", user_id, upload_id)

def compute_manifest_server_hash(manifest: dict) -> str:
    raw = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def get_authenticated_user(request):
    auth = JWTAuthentication()
    try:
        res = auth.authenticate(request)
        if not res:
            return None
        user, _ = res
        return user
    except Exception:
        return None

def _purge_file_record(record: FileRecord):
    user_id = str(record.owner_id)
    upload_id = str(record.upload_id)

    shutil.rmtree(upload_base_dir(user_id, upload_id), ignore_errors=True)
    shutil.rmtree(trash_base_dir(user_id, upload_id), ignore_errors=True)

    record.delete()

def _auto_purge_trash_for_user(user):
    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    old = FileRecord.objects.filter(owner=user, deleted_at__lt=cutoff)
    for r in old:
        _purge_file_record(r)

# ============================================================
# START UPLOAD
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def start_upload(request):
    body = request.data or {}

    filename = body.get("filename")
    size = body.get("size")
    chunk_size = body.get("chunk_size", 2 * 1024 * 1024)
    security_mode = body.get("security_mode", FileRecord.SECURITY_STANDARD)

    if not filename or not size:
        return JsonResponse({"error": "filename and size required"}, status=400)

    if security_mode not in (
        FileRecord.SECURITY_STANDARD,
        FileRecord.SECURITY_ZERO,
    ):
        return JsonResponse({"error": "invalid security_mode"}, status=400)

    upload_id = str(uuid.uuid4())
    user_id = str(request.user.id)

    base_dir = upload_base_dir(user_id, upload_id)
    os.makedirs(os.path.join(base_dir, "chunks"), exist_ok=True)

    manifest = {
        "manifest_version": 1,
        "filename": filename,
        "file_size": size,
        "chunk_size": chunk_size,
        "owner": user_id,
        "security_mode": security_mode,
        "encryption": "client_side",
        "aead_algorithm": "XCHACHA20_POLY1305",
        "chunks": [],
    }

    manifest["server_hash"] = compute_manifest_server_hash(
        {k: v for k, v in manifest.items() if k != "server_hash"}
    )

    with open(os.path.join(base_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    return JsonResponse({
        "status": 1,
        "upload_id": upload_id,
        "manifest": manifest,
    })

# ============================================================
# RESUME UPLOAD
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resume_upload(request, upload_id):
    user_id = str(request.user.id)
    base_dir = upload_base_dir(user_id, str(upload_id))
    manifest_path = os.path.join(base_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        return JsonResponse({
            "uploaded_indices": [],
        })

    with open(manifest_path) as f:
        manifest = json.load(f)

    uploaded = [c["index"] for c in manifest.get("chunks", [])]

    total_chunks = math.ceil(
        manifest["file_size"] / manifest["chunk_size"]
    )

    return JsonResponse({
        "uploaded_indices": sorted(uploaded),
        "total_chunks": total_chunks,
        "chunk_size": manifest["chunk_size"],
        "security_mode": manifest["security_mode"],
    })

# ============================================================
# UPLOAD CHUNK (OPAQUE ENCRYPTED)
# ============================================================

@csrf_exempt
@require_http_methods(["POST"])
def upload_chunk_xchacha(request, upload_id, index):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({"error": "auth required"}, status=401)

    base_dir = upload_base_dir(str(user.id), str(upload_id))
    manifest_path = os.path.join(base_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        return JsonResponse({"error": "manifest missing"}, status=404)

    blob = request.FILES.get("chunk")
    if not blob:
        return JsonResponse({"error": "missing chunk"}, status=400)

    nonce_b64 = request.headers.get("X-Chunk-Nonce")
    mac_b64 = request.headers.get("X-Chunk-Mac")

    if not nonce_b64 or not mac_b64:
        return JsonResponse({"error": "missing crypto headers"}, status=400)

    data = blob.read()
    sha = hashlib.sha256(data).hexdigest()

    chunk_path = os.path.join(base_dir, "chunks", f"chunk_{index}.bin")
    with open(chunk_path, "wb") as f:
        f.write(data)

    with open(manifest_path) as f:
        manifest = json.load(f)

    manifest["chunks"] = [
        c for c in manifest["chunks"] if c["index"] != index
    ] + [{
        "index": index,
        "ciphertext_size": len(data),
        "ciphertext_sha256": sha,
        "nonce_b64": nonce_b64,
        "mac_b64": mac_b64,
    }]

    manifest["chunks"].sort(key=lambda c: c["index"])
    manifest["server_hash"] = compute_manifest_server_hash(
        {k: v for k, v in manifest.items() if k != "server_hash"}
    )

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return JsonResponse({"stored": True, "index": index})

# ============================================================
# FINISH UPLOAD (VERIFY + SEAL)
# ============================================================
# @csrf_exempt
# @require_http_methods(["POST"])
# def finish_upload(request, upload_id):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "auth required"}, status=401)

#     user_id = str(user.id)
#     upload_id = str(upload_id)
#     base_dir = upload_base_dir(user_id, upload_id)

#     manifest_path = os.path.join(base_dir, "manifest.json")
#     if not os.path.exists(manifest_path):
#         return JsonResponse({"error": "manifest missing"}, status=404)

#     # --------------------------------------------------
#     # Load manifest
#     # --------------------------------------------------
#     with open(manifest_path, "r", encoding="utf-8") as f:
#         manifest = json.load(f)

#     if not manifest.get("chunks"):
#         return JsonResponse({"error": "no chunks uploaded"}, status=400)

#     # --------------------------------------------------
#     # Assemble final.bin + compute offsets
#     # --------------------------------------------------
#     chunks = sorted(manifest["chunks"], key=lambda c: c["index"])
#     final_path = os.path.join(base_dir, "final.bin")

#     offset = 0
#     with open(final_path, "wb") as out:
#         for c in chunks:
#             chunk_file = os.path.join(
#                 base_dir, "chunks", f"chunk_{c['index']}.bin"
#             )

#             if not os.path.exists(chunk_file):
#                 return JsonResponse(
#                     {"error": f"missing chunk {c['index']}"},
#                     status=400,
#                 )

#             with open(chunk_file, "rb") as cf:
#                 data = cf.read()

#             # --------------------------------------------------
#             # Integrity check (ciphertext)
#             # --------------------------------------------------
#             if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
#                 return JsonResponse(
#                     {
#                         "error": "chunk integrity failure",
#                         "index": c["index"],
#                     },
#                     status=400,
#                 )

#             # --------------------------------------------------
#             # Persist offset (CRITICAL FOR PREVIEW)
#             # --------------------------------------------------
#             c["offset"] = offset
#             offset += len(data)

#             out.write(data)

#     # --------------------------------------------------
#     # Persist updated manifest (with offsets)
#     # --------------------------------------------------
#     manifest["chunks"] = chunks
#     manifest["server_hash"] = compute_manifest_server_hash(
#         {k: v for k, v in manifest.items() if k != "server_hash"}
#     )

#     with open(manifest_path, "w", encoding="utf-8") as f:
#         json.dump(manifest, f, indent=2)

#     # --------------------------------------------------
#     # Create / update FileRecord
#     # --------------------------------------------------
#     record, _ = FileRecord.objects.update_or_create(
#         upload_id=upload_id,
#         owner=user,
#         defaults={
#             "filename": manifest["filename"],
#             "size": sum(c["ciphertext_size"] for c in chunks),
#             "final_path": final_path,
#             "storage_type": FileRecord.STORAGE_LOCAL,
#             "security_mode": manifest["security_mode"],
#             "deleted_at": None,
#         },
#     )

#     return JsonResponse(
#         {
#             "status": 1,
#             "file_id": str(record.id),
#             "security_mode": record.security_mode,
#         }
#     )
# @csrf_exempt
# @require_http_methods(["POST"])
# def finish_upload(request, upload_id):
#     user = get_authenticated_user(request)
#     if not user:
#         return JsonResponse({"error": "auth required"}, status=401)

#     user_id = str(user.id)
#     upload_id = str(upload_id)
#     base_dir = upload_base_dir(user_id, upload_id)

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

#             if not os.path.exists(chunk_path):
#                 return JsonResponse(
#                     {"error": f"missing chunk {c['index']}"},
#                     status=400,
#                 )

#             with open(chunk_path, "rb") as cf:
#                 data = cf.read()

#             if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
#                 return JsonResponse(
#                     {"error": "chunk integrity failure"},
#                     status=400,
#                 )

#             # 🔐 critical metadata
#             c["offset"] = offset
#             offset += len(data)

#             out.write(data)

#     # ✅ WRITE UPDATED MANIFEST BACK TO DISK
#     manifest["server_hash"] = compute_manifest_server_hash(
#         {k: v for k, v in manifest.items() if k != "server_hash"}
#     )

#     with open(manifest_path, "w", encoding="utf-8") as f:
#         json.dump(manifest, f, indent=2)

#     record, _ = FileRecord.objects.update_or_create(
#         upload_id=upload_id,
#         owner=user,
#         defaults={
#             "filename": manifest["filename"],
#             "size": offset,
#             "final_path": final_path,
#             "storage_type": FileRecord.STORAGE_LOCAL,
#             "security_mode": manifest["security_mode"],
#             "deleted_at": None,
#         },
#     )

#     return JsonResponse({
#         "status": 1,
#         "file_id": str(record.id),
#         "security_mode": record.security_mode,
#     })


@csrf_exempt
@require_http_methods(["POST"])
def finish_upload(request, upload_id):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({"error": "auth required"}, status=401)

    upload_uuid = uuid.UUID(str(upload_id))
    user_id = str(user.id)

    base_dir = upload_base_dir(user_id, str(upload_uuid))
    manifest_path = os.path.join(base_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        return JsonResponse({"error": "manifest missing"}, status=404)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    chunks = sorted(manifest["chunks"], key=lambda c: c["index"])

    final_path = os.path.join(base_dir, "final.bin")

    offset = 0
    with open(final_path, "wb") as out:
        for c in chunks:
            chunk_path = os.path.join(
                base_dir, "chunks", f"chunk_{c['index']}.bin"
            )

            with open(chunk_path, "rb") as cf:
                data = cf.read()

            if hashlib.sha256(data).hexdigest() != c["ciphertext_sha256"]:
                return JsonResponse(
                    {"error": "chunk integrity failure"},
                    status=400,
                )

            c["offset"] = offset
            offset += len(data)
            out.write(data)

    manifest["server_hash"] = compute_manifest_server_hash(
        {k: v for k, v in manifest.items() if k != "server_hash"}
    )

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # --------------------------------------------------
    # FileRecord.objects.update_or_create(
    #     upload_id=upload_uuid,
    #     owner=user,
    #     defaults={
    #         "filename": manifest["filename"],
    #         "size": offset,
    #         "final_path": final_path,
    #         "storage_type": FileRecord.STORAGE_LOCAL,
    #         "security_mode": manifest["security_mode"],
    #         "deleted_at": None,
    #     },
    # )
    # --------------------------------------------------
    # ===============================
    # UPLOAD FINAL.BIN TO R2
    # ===============================
    r2 = R2Storage()
    r2_key = f"user/{user_id}/{upload_uuid}/final.bin"

    r2.upload_file(final_path, r2_key)

    # 🔥 REMOVE LOCAL FILE (Render disk is ephemeral anyway)
    os.remove(final_path)

    # ===============================
    # SAVE FILE RECORD
    # ===============================
    FileRecord.objects.update_or_create(
        upload_id=upload_uuid,
        owner=user,
        defaults={
            "filename": manifest["filename"],
            "size": offset,
            "storage_type": FileRecord.STORAGE_R2,
            "storage_key": r2_key,
            "security_mode": manifest["security_mode"],
            "deleted_at": None,
        },
    )


    return JsonResponse({
        "status": 1,
        "upload_id": str(upload_uuid),
    })




# ============================================================
# LIST FILES
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_files(request):
    _auto_purge_trash_for_user(request.user)

    files = FileRecord.objects.filter(
        owner=request.user,
        deleted_at__isnull=True,
    ).order_by("-created_at")

    return JsonResponse([
        {
            "file_id": str(f.id),
            "upload_id": str(f.upload_id),
            "filename": f.filename,
            "size": f.size,
            "security_mode": f.security_mode,
        }
        for f in files
    ], safe=False)

# ============================================================
# DOWNLOAD (ENCRYPTED ONLY)
# ============================================================

# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def download_file(request, file_id):
#     file = get_object_or_404(
#         FileRecord,
#         id=file_id,
#         owner=request.user,
#         deleted_at__isnull=True,
#     )

#     return FileResponse(
#         open(file.final_path, "rb"),
#         as_attachment=True,
#         filename=file.filename,
#     )

from .r2_storage import R2Storage

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_file(request, file_id):
    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        deleted_at__isnull=True,
    )

    if file.storage_type == FileRecord.STORAGE_LOCAL:
        return FileResponse(
            open(file.final_path, "rb"),
            as_attachment=True,
            filename=file.filename,
        )

    # 🔐 R2 (encrypted only)
    r2 = R2Storage()
    stream, _ = r2.open_stream(file.storage_key)

    return FileResponse(
        stream,
        as_attachment=True,
        filename=file.filename,
        content_type="application/octet-stream",
    )


# ============================================================
# PREVIEW (DISABLED BY DESIGN)
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def preview_file(request, file_id):
    return JsonResponse(
        {"error": "Preview requires client-side decryption"},
        status=403,
    )

# ============================================================
# TRASH / DELETE / RESTORE / PURGE
# ============================================================

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_upload(request, upload_id):
    record = get_object_or_404(
        FileRecord,
        upload_id=upload_id,
        owner=request.user,
        deleted_at__isnull=True,
    )

    record.deleted_at = timezone.now()
    record.save(update_fields=["deleted_at"])

    return JsonResponse({"status": 1})

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def restore_upload(request, file_id):
    record = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        deleted_at__isnull=False,
    )

    record.deleted_at = None
    record.save(update_fields=["deleted_at"])

    return JsonResponse({"status": 1})

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def purge_upload(request, file_id):
    record = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
    )

    _purge_file_record(record)
    return JsonResponse({"status": 1})

# ============================================================
# RESET UNFINISHED UPLOADS (DEV)
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reset_uploads(request):
    user_id = str(request.user.id)
    base = os.path.join(settings.MEDIA_ROOT, "uploads", user_id)

    if not os.path.exists(base):
        return JsonResponse({"status": "nothing to reset"})

    removed = 0
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1

    return JsonResponse({
        "status": "ok",
        "removed_uploads": removed,
    })
    
    
# ============================================================
# fetch manifest
# ============================================================
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def fetch_manifest(request, file_id):
    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        deleted_at__isnull=True,
    )

    user_id = str(request.user.id)
    upload_id = str(file.upload_id)

    manifest_path = os.path.join(
        settings.MEDIA_ROOT,
        "uploads",
        user_id,
        upload_id,
        "manifest.json",
    )

    if not os.path.exists(manifest_path):
        return JsonResponse(
            {"error": "manifest missing"},
            status=404,
        )

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    return JsonResponse(manifest, safe=False)




    # # ============================================================

    # # FETCH ENCRYPTED DATA Full File
    # # ============================================================
    # from django.views.decorators.http import require_GET

    # @api_view(["GET"])
    # @permission_classes([IsAuthenticated])
    # def fetch_encrypted_data(request, file_id):
    #     file = get_object_or_404(
    #         FileRecord,
    #         id=file_id,
    #         owner=request.user,
    #         deleted_at__isnull=True,
    #     )

    #     # 🔐 Server is blind: always encrypted
    #     if file.storage_type == FileRecord.STORAGE_LOCAL:
    #         if not os.path.exists(file.final_path):
    #             return JsonResponse({"error": "file missing"}, status=404)

    #         return FileResponse(
    #             open(file.final_path, "rb"),
    #             content_type="application/octet-stream",
    #         )

    #     # R2 / S3
    #     from .r2_storage import R2Storage
    #     r2 = R2Storage()

    #     try:
    #         stream, _ = r2.open_stream(file.final_path)
    #     except Exception as e:
    #         return JsonResponse(
    #             {"error": f"storage error: {str(e)}"},
    #             status=500,
    #         )

    #     return FileResponse(
    #         stream,
    #         content_type="application/octet-stream",
    #     )
# # ============================================================
# FETCH ENCRYPTED DATA (RAW BINARY)
from django.views.decorators.http import require_GET

@require_GET
def fetch_encrypted_data(request, file_id):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({"error": "auth required"}, status=401)

    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=user,
        deleted_at__isnull=True,
    )

    if file.storage_type == FileRecord.STORAGE_LOCAL:
        if not os.path.exists(file.final_path):
            return JsonResponse({"error": "file missing"}, status=404)

        # 🔐 RAW BINARY — NO DRF
        response = FileResponse(
            open(file.final_path, "rb"),
            as_attachment=False,
        )
        response["Content-Type"] = "application/octet-stream"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    # R2 / S3 (same logic)
    from .r2_storage import R2Storage
    r2 = R2Storage()

    try:
        stream, _ = r2.open_stream(file.final_path)
    except Exception as e:
        return JsonResponse(
            {"error": f"storage error: {str(e)}"},
            status=500,
        )

    response = FileResponse(stream)
    response["Content-Type"] = "application/octet-stream"
    response["X-Content-Type-Options"] = "nosniff"
    return response
