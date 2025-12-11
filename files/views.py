# files/views.py
import os
import math
import json
import uuid
import base64
import shutil
import hashlib
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse, FileResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.contrib.auth.models import AnonymousUser

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import FileRecord, FileChunk

# Optional storage abstraction
try:
    from .storage import LocalStorage as StorageClass
except Exception:
    from .storage import LocalStorage as StorageClass

storage = StorageClass()

# -------------------------------------------------
# Config
# -------------------------------------------------

# Default: 30 days in Trash before auto-purge
TRASH_RETENTION_DAYS = getattr(settings, "SILVORA_TRASH_RETENTION_DAYS", 30)


# -------------------------------------------------
# Helpers
# -------------------------------------------------

def upload_base_dir(user_id: str, upload_id: str) -> str:
    """
    Per-user upload root:
    MEDIA_ROOT/uploads/<user_id>/<upload_id>/
    """
    return os.path.join(
        settings.MEDIA_ROOT,
        "uploads",
        str(user_id),
        str(upload_id),
    )


def trash_base_dir(user_id: str, upload_id: str) -> str:
    """
    Per-user trash root:
    MEDIA_ROOT/trash/<user_id>/<upload_id>/
    """
    return os.path.join(
        settings.MEDIA_ROOT,
        "trash",
        str(user_id),
        str(upload_id),
    )


def compute_manifest_server_hash(manifest_obj: dict) -> str:
    content = json.dumps(
        manifest_obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def get_authenticated_user(request):
    """
    Manual JWT auth for non-DRF views (chunk/finish).
    """
    auth = JWTAuthentication()
    try:
        user_auth_tuple = auth.authenticate(request)
        if not user_auth_tuple:
            return None
        user, token = user_auth_tuple
        return user
    except Exception:
        return None


def _purge_file_record(record: FileRecord):
    """
    Hard-delete: remove disk data + DB row.
    Used for manual purge + auto-purge.
    """
    user_id = str(record.owner_id)
    upload_id = str(record.upload_id)

    # Both possible locations (active or trash)
    active_dir = upload_base_dir(user_id, upload_id)
    trash_dir = trash_base_dir(user_id, upload_id)

    # Remove directories if they exist
    shutil.rmtree(active_dir, ignore_errors=True)
    shutil.rmtree(trash_dir, ignore_errors=True)

    # Just in case final_path points somewhere else
    if record.final_path and os.path.exists(record.final_path):
        try:
            os.remove(record.final_path)
        except Exception:
            pass

    # DB row removed; related chunks should cascade
    record.delete()


def _auto_purge_trash_for_user(user):
    """
    Auto-purge trash items older than TRASH_RETENTION_DAYS.
    Called opportunistically when user hits list APIs.
    """
    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    old_records = FileRecord.objects.filter(
        owner=user,
        deleted_at__lt=cutoff,
    )

    for rec in old_records:
        _purge_file_record(rec)


# -------------------------------------------------
# Start upload
# POST /upload/start/
# -------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def start_upload(request):
    """
    Create a new upload session and per-user folder:
    MEDIA_ROOT/uploads/<user_id>/<upload_id>/

    Payload JSON: { filename, size, chunk_size }
    """
    body = request.data or {}

    filename = body.get("filename")
    size = body.get("size")
    chunk_size = body.get("chunk_size", 1024 * 1024)

    if not filename or not size:
        return JsonResponse({"error": "filename and size required"}, status=400)

    user = request.user
    if not user or isinstance(user, AnonymousUser):
        return JsonResponse({"error": "Auth required"}, status=401)

    upload_id = str(uuid.uuid4())
    user_id = str(user.id)

    upload_dir = upload_base_dir(user_id, upload_id)
    chunk_dir = os.path.join(upload_dir, "chunks")
    os.makedirs(chunk_dir, exist_ok=True)

    manifest_obj = {
        "manifest_version": 1,
        "encryption": "standard",          # logical profile
        "aead_algorithm": "aes-256-gcm",   # concrete AEAD
        "filename": filename,
        "file_size": size,
        "chunk_size": chunk_size,
        "chunks": [],
        "owner": user_id,
    }

    manifest_for_hash = {k: v for k, v in manifest_obj.items() if k != "server_hash"}
    manifest_obj["server_hash"] = compute_manifest_server_hash(manifest_for_hash)

    manifest_path = os.path.join(upload_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_obj, f, indent=2)

    return JsonResponse(
        {
            "status": 1,
            "message": "Upload session created",
            "upload_id": upload_id,
            "filename": filename,
            "size": size,
            "chunk_size": chunk_size,
            "manifest": manifest_obj,
        }
    )


# -------------------------------------------------
# Resume upload
# GET /upload/resume/<upload_id>/
# -------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resume_upload(request, upload_id):
    user = request.user
    user_id = str(user.id)
    upload_id_str = str(upload_id)

    try:
        file_record = FileRecord.objects.get(
            upload_id=upload_id_str,
            owner=user,
            deleted_at__isnull=True,
        )
    except FileRecord.DoesNotExist:
        return JsonResponse({"error": "upload not found or not allowed"}, status=404)

    upload_dir = upload_base_dir(user_id, upload_id_str)
    manifest_path = os.path.join(upload_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return JsonResponse({"error": "manifest missing"}, status=500)

    with open(manifest_path, "r", encoding="utf-8") as mf:
        manifest = json.load(mf)

    chunk_size = manifest.get("chunk_size")
    file_size = manifest.get("file_size")
    total_chunks = math.ceil(file_size / chunk_size)

    uploaded_indices = list(
        FileChunk.objects.filter(file=file_record).values_list("index", flat=True)
    )

    return JsonResponse(
        {
            "upload_id": upload_id_str,
            "uploaded_indices": sorted(uploaded_indices),
            "total_chunks": total_chunks,
            "chunk_size": chunk_size,
            "file_size": file_size,
        }
    )


# -------------------------------------------------
# Chunk upload (XChaCha ciphertext, server is opaque)
# POST /upload/chunk/<upload_id>/<index>/
# Form-data: chunk
# -------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def upload_chunk_xchacha(request, upload_id, index):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({"error": "Auth required"}, status=401)

    user_id = str(user.id)
    upload_id_str = str(upload_id)

    upload_dir = upload_base_dir(user_id, upload_id_str)
    chunk_dir = os.path.join(upload_dir, "chunks")
    manifest_path = os.path.join(upload_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        return JsonResponse({"error": "manifest missing"}, status=404)

    with open(manifest_path, "r", encoding="utf-8") as mf:
        manifest = json.load(mf)

    if str(manifest.get("owner")) != user_id:
        return JsonResponse({"error": "ownership mismatch"}, status=403)

    os.makedirs(chunk_dir, exist_ok=True)

    encrypted_file = request.FILES.get("chunk")
    if not encrypted_file:
        return JsonResponse({"error": "Missing file part 'chunk'"}, status=400)

    ciphertext = encrypted_file.read()
    if not ciphertext:
        return JsonResponse({"error": "empty chunk"}, status=400)

    nonce_b64 = request.META.get("HTTP_X_CHUNK_NONCE")
    if not nonce_b64:
        # TEMP: allow missing nonce while client evolves
        nonce_b64 = base64.b64encode(os.urandom(24)).decode("ascii")

    try:
        nonce = base64.b64decode(nonce_b64)
        if len(nonce) != 24:
            return JsonResponse({"error": "Nonce must be 24 bytes"}, status=400)
    except Exception:
        return JsonResponse({"error": "invalid nonce base64"}, status=400)

    client_sha = request.META.get("HTTP_X_CHUNK_CIPHERTEXT_SHA256")
    server_sha = hashlib.sha256(ciphertext).hexdigest()
    if client_sha and client_sha.lower() != server_sha.lower():
        return JsonResponse({"error": "SHA mismatch"}, status=400)

    # Save chunk
    idx_int = int(index)
    chunk_path = os.path.join(chunk_dir, f"chunk_{idx_int}.bin")
    with open(chunk_path, "wb") as f:
        f.write(ciphertext)

    # Update manifest chunks
    chunks = [
        c for c in manifest.get("chunks", [])
        if int(c.get("index", -1)) != idx_int
    ]
    chunks.append(
        {
            "index": idx_int,
            "ciphertext_size": len(ciphertext),
            "nonce_b64": nonce_b64,
            "ciphertext_sha256": server_sha,
        }
    )
    chunks.sort(key=lambda c: c["index"])
    manifest["chunks"] = chunks

    manifest_no_hash = {k: v for k, v in manifest.items() if k != "server_hash"}
    manifest["server_hash"] = compute_manifest_server_hash(manifest_no_hash)

    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)

    return JsonResponse(
        {
            "stored": 1,
            "upload_id": upload_id_str,
            "index": idx_int,
            "size": len(ciphertext),
        },
        status=201,
    )


# -------------------------------------------------
# Finish upload: assemble final.bin (ciphertext)
# POST /upload/finish/<upload_id>/
# -------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def finish_upload(request, upload_id):
    user = get_authenticated_user(request)
    if not user or isinstance(user, AnonymousUser):
        return JsonResponse({"error": "Auth required"}, status=401)

    user_id = str(user.id)
    upload_id_str = str(upload_id)

    upload_dir = upload_base_dir(user_id, upload_id_str)
    manifest_p = os.path.join(upload_dir, "manifest.json")

    if not os.path.exists(manifest_p):
        return JsonResponse({"error": "manifest missing"}, status=400)

    try:
        with open(manifest_p, "r", encoding="utf-8") as mf:
            manifest = json.load(mf)
    except Exception:
        return JsonResponse({"error": "failed to load manifest"}, status=500)

    if str(manifest.get("owner")) != user_id:
        return JsonResponse({"error": "finish forbidden for this user"}, status=403)

    chunks_meta = manifest.get("chunks", [])
    if not chunks_meta:
        return JsonResponse({"error": "manifest has no chunks"}, status=400)

    chunk_dir = os.path.join(upload_dir, "chunks")

    for m in chunks_meta:
        m["index"] = int(m.get("index", 0))
    chunks_meta.sort(key=lambda m: m["index"])

    expected_indices = list(range(len(chunks_meta)))
    actual_indices = [m["index"] for m in chunks_meta]
    if expected_indices != actual_indices:
        return JsonResponse(
            {
                "error": "chunk sequence incomplete",
                "expected_indices": expected_indices,
                "received_indices": actual_indices,
            },
            status=400,
        )

    # Validate files + sha256
    for meta in chunks_meta:
        idx = meta["index"]
        expected_size = int(meta.get("ciphertext_size", 0))
        expected_sha = meta.get("ciphertext_sha256")

        chunk_path = os.path.join(chunk_dir, f"chunk_{idx}.bin")
        if not os.path.exists(chunk_path):
            return JsonResponse({"error": f"chunk_{idx}.bin missing"}, status=400)

        ciphertext = open(chunk_path, "rb").read()
        real_size = len(ciphertext)
        if expected_size and real_size != expected_size:
            return JsonResponse(
                {
                    "error": f"chunk_{idx}.bin size mismatch "
                             f"(expected {expected_size}, got {real_size})"
                },
                status=400,
            )

        sha = hashlib.sha256(ciphertext).hexdigest()
        if expected_sha and expected_sha.lower() != sha.lower():
            return JsonResponse(
                {
                    "error": f"chunk_{idx}.bin sha mismatch",
                    "expected_sha": expected_sha,
                    "actual_sha": sha,
                    "index": idx,
                },
                status=400,
            )

    # Recompute manifest hash
    manifest_no_hash = {k: v for k, v in manifest.items() if k != "server_hash"}
    manifest["server_hash"] = compute_manifest_server_hash(manifest_no_hash)
    with open(manifest_p, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)

    # Assemble final.bin
    final_path = os.path.join(upload_dir, "final.bin")
    try:
        with open(final_path, "wb") as out:
            for meta in chunks_meta:
                idx = meta["index"]
                chunk_path = os.path.join(chunk_dir, f"chunk_{idx}.bin")
                with open(chunk_path, "rb") as cf:
                    out.write(cf.read())
    except Exception as e:
        return JsonResponse(
            {"error": f"failed to assemble final file: {e}"}, status=500
        )

    # DB entry
    try:
        record, created = FileRecord.objects.get_or_create(
            upload_id=upload_id_str,
            owner=user,
            defaults={
                "filename": manifest.get("filename", f"{upload_id_str}_file"),
                "size": os.path.getsize(final_path),
                "final_path": final_path,
            },
        )
        if not created:
            record.filename = manifest.get("filename", record.filename)
            record.size = os.path.getsize(final_path)
            record.final_path = final_path
            record.deleted_at = None  # in case it was restored
            record.save()
        file_id = str(record.id)
    except Exception:
        file_id = None

    return JsonResponse(
        {
            "status": 1,
            "message": "file assembled",
            "file_id": file_id,
            "upload_id": upload_id_str,
            "final_path": final_path,
            "chunks": len(chunks_meta),
        }
    )


# -------------------------------------------------
# Reset uploads (dev only)
# POST /upload/reset/
# -------------------------------------------------

@csrf_exempt
@require_http_methods(["POST", "GET"])
def reset_uploads(request):
    base_upload_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
    base_trash_dir = os.path.join(settings.MEDIA_ROOT, "trash")
    shutil.rmtree(base_upload_dir, ignore_errors=True)
    shutil.rmtree(base_trash_dir, ignore_errors=True)

    FileRecord.objects.all().delete()
    FileChunk.objects.all().delete()

    return JsonResponse({"message": "All uploads and trash have been reset", "status": 1})


# -------------------------------------------------
# List active files (non-deleted)
# GET /upload/files/
# -------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_files(request):
    user = request.user

    # Auto-purge old trash opportunistically
    _auto_purge_trash_for_user(user)

    files_qs = FileRecord.objects.filter(
        owner=user,
        deleted_at__isnull=True,
    ).order_by("-created_at")

    data = []
    for f in files_qs:
        data.append(
            {
                "file_id": str(f.id),
                "filename": f.filename,
                "size": f.size,
                "upload_id": str(f.upload_id),
                "path": f.final_path,
                "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return JsonResponse(data, safe=False)


# -------------------------------------------------
# List trash files
# GET /upload/trash/
# -------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_trash_files(request):
    user = request.user

    # Auto-purge old trash here as well
    _auto_purge_trash_for_user(user)

    qs = FileRecord.objects.filter(
        owner=user,
        deleted_at__isnull=False,
    ).order_by("-deleted_at")

    data = []
    for f in qs:
        data.append(
            {
                "file_id": str(f.id),
                "filename": f.filename,
                "size": f.size,
                "upload_id": str(f.upload_id),
                "path": f.final_path,
                "deleted_at": f.deleted_at.strftime("%Y-%m-%d %H:%M:%S")
                if f.deleted_at
                else None,
            }
        )
    return JsonResponse(data, safe=False)


# -------------------------------------------------
# Download active file
# GET /upload/download/<file_id>/
# -------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_file(request, file_id):
    try:
        file = FileRecord.objects.get(
            id=file_id,
            owner=request.user,
            deleted_at__isnull=True,
        )
    except FileRecord.DoesNotExist:
        return JsonResponse({"error": "file not found or not accessible"}, status=404)

    if not file.final_path or not os.path.exists(file.final_path):
        return JsonResponse({"error": "file missing on disk"}, status=404)

    try:
        return FileResponse(open(file.final_path, "rb"), as_attachment=True, filename=file.filename)
    except Exception:
        return JsonResponse({"error": "could not open file"}, status=500)


# -------------------------------------------------
# Soft delete (move to Trash)
# DELETE /upload/file/<upload_id>/
# -------------------------------------------------

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_upload(request, upload_id):
    """
    Soft-delete:
      - move folder to MEDIA_ROOT/trash/<user_id>/<upload_id>/
      - set deleted_at on FileRecord
    """
    user = request.user
    user_id = str(user.id)
    upload_id_str = str(upload_id)

    try:
        record = FileRecord.objects.get(
            upload_id=upload_id_str,
            owner=user,
            deleted_at__isnull=True,
        )
    except FileRecord.DoesNotExist:
        return JsonResponse({"error": "file not found"}, status=404)

    src_dir = upload_base_dir(user_id, upload_id_str)
    dst_dir = trash_base_dir(user_id, upload_id_str)

    if os.path.exists(src_dir):
        os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
        try:
            shutil.move(src_dir, dst_dir)
        except Exception:
            # Worst case: keep DB state but folder might already be gone
            pass

    # Update final_path to trash location
    trash_final = os.path.join(dst_dir, "final.bin")
    record.final_path = trash_final if os.path.exists(trash_final) else ""
    record.deleted_at = timezone.now()
    record.save(update_fields=["final_path", "deleted_at"])

    return JsonResponse(
        {
            "status": 1,
            "message": "File moved to trash",
            "upload_id": upload_id_str,
        },
        status=200,
    )


# -------------------------------------------------
# Restore from Trash
# POST /upload/trash/<file_id>/restore/
# -------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def restore_upload(request, file_id):
    user = request.user
    user_id = str(user.id)

    try:
        record = FileRecord.objects.get(
            id=file_id,
            owner=user,
            deleted_at__isnull=False,
        )
    except FileRecord.DoesNotExist:
        return JsonResponse({"error": "trash file not found"}, status=404)

    upload_id_str = str(record.upload_id)
    src_dir = trash_base_dir(user_id, upload_id_str)
    dst_dir = upload_base_dir(user_id, upload_id_str)

    if os.path.exists(src_dir):
        os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
        try:
            shutil.move(src_dir, dst_dir)
        except Exception as e:
            return JsonResponse({"error": f"restore failed: {e}"}, status=500)

    final_path = os.path.join(dst_dir, "final.bin")
    record.final_path = final_path if os.path.exists(final_path) else ""
    record.deleted_at = None
    record.save(update_fields=["final_path", "deleted_at"])

    return JsonResponse(
        {
            "status": 1,
            "message": "File restored",
            "file_id": str(record.id),
            "upload_id": upload_id_str,
        }
    )


# -------------------------------------------------
# Purge (hard delete) from Trash
# DELETE /upload/trash/<file_id>/purge/
# -------------------------------------------------

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def purge_upload(request, file_id):
    user = request.user

    try:
        record = FileRecord.objects.get(
            id=file_id,
            owner=user,
        )
    except FileRecord.DoesNotExist:
        return JsonResponse({"error": "file not found"}, status=404)

    _purge_file_record(record)

    return JsonResponse(
        {
            "status": 1,
            "message": "File permanently deleted",
            "file_id": str(file_id),
        }
    )
