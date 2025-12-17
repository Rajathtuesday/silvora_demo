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
from django.contrib.auth.models import AnonymousUser

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import FileRecord, FileChunk


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
    chunk_size = body.get("chunk_size", 1024 * 1024)
    security_mode = body.get(
        "security_mode",
        FileRecord.SECURITY_STANDARD,
    )

    if not filename or not size:
        return JsonResponse({"error": "filename and size required"}, status=400)

    if security_mode not in (
        FileRecord.SECURITY_STANDARD,
        FileRecord.SECURITY_ZERO,
    ):
        return JsonResponse({"error": "invalid security_mode"}, status=400)

    user = request.user
    upload_id = str(uuid.uuid4())
    user_id = str(user.id)

    base_dir = upload_base_dir(user_id, upload_id)
    os.makedirs(os.path.join(base_dir, "chunks"), exist_ok=True)

    manifest = {
        "manifest_version": 1,
        "filename": filename,
        "file_size": size,
        "chunk_size": chunk_size,
        "chunks": [],
        "owner": user_id,
        "security_mode": security_mode,
        "encryption": "client_side",
        "aead_algorithm": "XCHACHA20_POLY1305",
    }

    manifest["server_hash"] = compute_manifest_server_hash(
        {k: v for k, v in manifest.items() if k != "server_hash"}
    )

    with open(os.path.join(base_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    return JsonResponse(
        {
            "status": 1,
            "upload_id": upload_id,
            "manifest": manifest,
        }
    )


# ============================================================
# RESUME UPLOAD
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resume_upload(request, upload_id):
    user = request.user
    user_id = str(user.id)
    upload_id = str(upload_id)

    record = get_object_or_404(
        FileRecord,
        upload_id=upload_id,
        owner=user,
        deleted_at__isnull=True,
    )

    manifest_path = os.path.join(
        upload_base_dir(user_id, upload_id),
        "manifest.json",
    )

    if not os.path.exists(manifest_path):
        return JsonResponse({"error": "manifest missing"}, status=404)

    with open(manifest_path) as f:
        manifest = json.load(f)

    chunk_size = manifest["chunk_size"]
    total_chunks = math.ceil(manifest["file_size"] / chunk_size)

    uploaded = list(
        FileChunk.objects.filter(file=record).values_list("index", flat=True)
    )

    return JsonResponse(
        {
            "upload_id": upload_id,
            "uploaded_indices": sorted(uploaded),
            "total_chunks": total_chunks,
            "chunk_size": chunk_size,
            "security_mode": manifest["security_mode"],
        }
    )


# ============================================================
# UPLOAD CHUNK (OPAQUE)
# ============================================================

@csrf_exempt
@require_http_methods(["POST"])
def upload_chunk_xchacha(request, upload_id, index):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({"error": "auth required"}, status=401)

    user_id = str(user.id)
    upload_id = str(upload_id)
    index = int(index)

    base_dir = upload_base_dir(user_id, upload_id)
    manifest_path = os.path.join(base_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        return JsonResponse({"error": "manifest missing"}, status=404)

    with open(manifest_path) as f:
        manifest = json.load(f)

    if manifest["owner"] != user_id:
        return JsonResponse({"error": "forbidden"}, status=403)

    blob = request.FILES.get("chunk")
    if not blob:
        return JsonResponse({"error": "missing chunk"}, status=400)

    data = blob.read()
    sha = hashlib.sha256(data).hexdigest()

    chunk_path = os.path.join(base_dir, "chunks", f"chunk_{index}.bin")
    with open(chunk_path, "wb") as f:
        f.write(data)

    manifest["chunks"] = [
        c for c in manifest["chunks"] if c["index"] != index
    ] + [{
        "index": index,
        "ciphertext_size": len(data),
        "ciphertext_sha256": sha,
    }]

    manifest["chunks"].sort(key=lambda x: x["index"])
    manifest["server_hash"] = compute_manifest_server_hash(
        {k: v for k, v in manifest.items() if k != "server_hash"}
    )

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return JsonResponse({"stored": 1, "index": index})


# ============================================================
# FINISH UPLOAD
# ============================================================

@csrf_exempt
@require_http_methods(["POST"])
def finish_upload(request, upload_id):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({"error": "auth required"}, status=401)

    user_id = str(user.id)
    upload_id = str(upload_id)
    base_dir = upload_base_dir(user_id, upload_id)

    with open(os.path.join(base_dir, "manifest.json")) as f:
        manifest = json.load(f)

    chunks = sorted(manifest["chunks"], key=lambda x: x["index"])
    final_path = os.path.join(base_dir, "final.bin")

    with open(final_path, "wb") as out:
        for c in chunks:
            with open(
                os.path.join(base_dir, "chunks", f"chunk_{c['index']}.bin"),
                "rb",
            ) as cf:
                shutil.copyfileobj(cf, out)

    from .r2_storage import R2Storage
    r2 = R2Storage()
    remote_key = f"{user_id}/{upload_id}/final.bin"

    try:
        r2.upload_final(final_path, remote_key)
        stored_path = remote_key
        storage_type = FileRecord.STORAGE_R2
    except Exception:
        stored_path = final_path
        storage_type = FileRecord.STORAGE_LOCAL

    size = sum(c["ciphertext_size"] for c in chunks)

    record, _ = FileRecord.objects.update_or_create(
        upload_id=upload_id,
        owner=user,
        defaults={
            "filename": manifest["filename"],
            "size": size,
            "final_path": stored_path,
            "storage_type": storage_type,
            "security_mode": manifest["security_mode"],
            "deleted_at": None,
        },
    )

    return JsonResponse(
        {
            "status": 1,
            "file_id": str(record.id),
            "security_mode": record.security_mode,
        }
    )


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

    return JsonResponse(
        [
            {
                "file_id": str(f.id),
                "filename": f.filename,
                "size": f.size,
                "security_mode": f.security_mode,
                "has_thumbnail": bool(f.thumbnail_key),
            }
            for f in files
        ],
        safe=False,
    )


# ============================================================
# DOWNLOAD
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_file(request, file_id):
    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        deleted_at__isnull=True,
    )

    if file.storage_type == FileRecord.STORAGE_R2:
        from .r2_storage import R2Storage
        r2 = R2Storage()
        stream, _ = r2.open_stream(file.final_path)
        return FileResponse(stream, as_attachment=True, filename=file.filename)

    return FileResponse(open(file.final_path, "rb"), as_attachment=True)


# ============================================================
# PREVIEW (STANDARD ONLY)
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def preview_file(request, file_id):
    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        deleted_at__isnull=True,
    )

    if file.security_mode == FileRecord.SECURITY_ZERO:
        return JsonResponse(
            {"error": "preview disabled for zero-knowledge files"},
            status=403,
        )

    from .r2_storage import R2Storage
    r2 = R2Storage()
    stream, content_type = r2.open_stream(file.final_path)

    return FileResponse(stream, as_attachment=False, content_type=content_type)


# ============================================================
# TRASH
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
# ZERO-KNOWLEDGE THUMBNAILS
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upload_encrypted_thumbnail(request, upload_id):
    user = request.user
    upload_id = str(upload_id)

    record = get_object_or_404(
        FileRecord,
        upload_id=upload_id,
        owner=user,
        deleted_at__isnull=True,
    )

    if record.security_mode != FileRecord.SECURITY_ZERO:
        return JsonResponse(
            {"error": "thumbnails only allowed in zero-knowledge mode"},
            status=400,
        )

    blob = request.FILES.get("thumbnail")
    if not blob:
        return JsonResponse({"error": "missing thumbnail"}, status=400)

    from .r2_storage import R2Storage
    r2 = R2Storage()

    thumb_key = f"{user.id}/{upload_id}/thumb.enc"

    r2.client.upload_fileobj(blob, r2.bucket_name, thumb_key)

    record.thumbnail_key = thumb_key
    record.save(update_fields=["thumbnail_key"])

    return JsonResponse({"status": 1})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def fetch_encrypted_thumbnail(request, file_id):
    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        deleted_at__isnull=True,
    )

    if file.security_mode != FileRecord.SECURITY_ZERO:
        return JsonResponse(
            {"error": "thumbnails only for zero-knowledge files"},
            status=403,
        )

    if not file.thumbnail_key:
        return JsonResponse({"error": "no thumbnail"}, status=404)

    from .r2_storage import R2Storage
    r2 = R2Storage()
    stream, _ = r2.open_stream(file.thumbnail_key)

    return FileResponse(stream, as_attachment=True)


# ---------------------------------------------------
# reset uploads
# ---------------------------------------------------


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reset_uploads(request):
    """
    ⚠️ DEV / ADMIN ONLY
    Clears unfinished upload directories for the current user.
    """
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
