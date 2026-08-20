from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.http import HttpResponse

from .models import FileRecord
from .services.upload_service import UploadService, r2_base, integrity_key
from .services.quota_service import QuotaService
from .services.storage_gateway import StorageGateway


# ============================================================
# THROTTLES
# ============================================================
# DRF's ScopedRateThrottle reads `view.throttle_scope` -- but the `api_view`
# decorator's WrappedAPIView never copies a `throttle_scope` attribute from
# the wrapped function (confirmed by reading its source: it only copies
# renderer/parser/authentication/throttle_classes/permission/content-negotiation/
# metadata/versioning/schema classes). Setting `request.throttle_scope` inside
# a view body does nothing -- throttle checks run in APIView.initial(),
# before the view function body ever executes, and they read the VIEW
# object's attribute, not the request's. That pattern was silently a no-op
# in every endpoint below until this fix.
#
# Fix: fixed-scope throttle subclasses. Each hardcodes its scope as a class
# attribute instead of trying to read it from the view at request time.
class _FileOpThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        ident = request.user.pk if request.user and request.user.is_authenticated else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class FileChunkThrottle(_FileOpThrottle):
    scope = "file_chunk"


class FileMutateThrottle(_FileOpThrottle):
    scope = "file_mutate"


class FileMetaThrottle(_FileOpThrottle):
    scope = "file_meta"


# ============================================================
# UPLOAD FLOW
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMutateThrottle])
def start_upload(request):
    service = UploadService(request.user)
    data, status_code = service.start(request.data)
    return Response(data, status=status_code)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMetaThrottle])
def resume_upload(request, file_id):
    service = UploadService(request.user)
    data, status_code = service.resume(file_id)
    return Response(data, status=status_code)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileChunkThrottle])
def upload_chunk(request, file_id, index):
    blob = request.FILES.get("chunk")
    if not blob:
        return Response({"error": "Missing chunk"}, status=400)

    service = UploadService(request.user)
    data, status_code = service.upload_chunk(file_id, index, blob.read())
    return Response(data, status=status_code)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMutateThrottle])
def upload_integrity(request, file_id):
    # The client posts the encrypted integrity manifest as raw bytes.
    blob = request.body
    if not blob:
        return Response({"error": "Missing integrity manifest"}, status=400)

    service = UploadService(request.user)
    data, status_code = service.store_integrity(file_id, blob)
    return Response(data, status=status_code)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMutateThrottle])
def commit_upload(request, file_id):
    service = UploadService(request.user)
    data, status_code = service.commit(file_id)
    return Response(data, status=status_code)

# ============================================================
# FILE LISTING
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMetaThrottle])
def list_files(request):
    files = FileRecord.objects.filter(
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    ).order_by('-created_at')

    return Response([
        {
            "file_id": str(f.id),
            "size": f.size,
            "filename_ciphertext": f.filename_ciphertext.hex() if f.filename_ciphertext else None,
            "filename_nonce": f.filename_nonce.hex() if f.filename_nonce else None,
            "filename_mac": f.filename_mac.hex() if f.filename_mac else None,
            "created_at": f.created_at.isoformat(),
        }
        for f in files
    ])

# ============================================================
# TRASH & RECOVERY
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMetaThrottle])
def list_trash(request):
    trash = FileRecord.objects.filter(
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=False,
    ).order_by('-deleted_at')

    return Response([
        {
            "file_id": str(f.id),
            "size": f.size,
            "deleted_at": f.deleted_at,
            "filename_ciphertext": f.filename_ciphertext.hex() if f.filename_ciphertext else None,
            "filename_nonce": f.filename_nonce.hex() if f.filename_nonce else None,
            "filename_mac": f.filename_mac.hex() if f.filename_mac else None,
        }
        for f in trash
    ])

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMutateThrottle])
def delete_file(request, file_id):
    file = FileRecord.objects.filter(
        id=file_id,
        owner=request.user,
        tenant=request.user.tenant,
    ).first()

    if not file:
        return Response({"error": "File not found"}, status=404)

    if file.upload_state != FileRecord.UploadState.COMMITTED:
        # Upload was never finished — nothing was ever added to used_bytes
        # (QuotaService.consume only runs at commit), so there's no quota to
        # release and no trash semantics apply. Just purge the abandoned
        # chunks and drop the record outright.
        with transaction.atomic():
            storage = StorageGateway()
            base = r2_base(file.tenant_id, file.owner_id, file.id)
            storage.delete_recursive(base)
            file.delete()
        return Response({"status": "abandoned_upload_removed"})

    if file.deleted_at is None:
        # SOFT DELETE
        with transaction.atomic():
            QuotaService.release(request.user, file.size)
            file.mark_deleted()
        return Response({"status": "moved_to_trash"})
    else:
        # HARD DELETE
        with transaction.atomic():
            storage = StorageGateway()
            base = r2_base(file.tenant_id, file.owner_id, file.id)
            storage.delete_recursive(base)
            file.delete()
        return Response({"status": "permanently_erased"})

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMutateThrottle])
def restore_file(request, file_id):
    with transaction.atomic():
        # select_for_update: closes a race against purge_trashed_files (the
        # daily cron). Without a row lock here, the cron could act on this
        # row between this transaction's read and its commit.
        file = get_object_or_404(
            FileRecord.objects.select_for_update(), id=file_id, owner=request.user,
            tenant=request.user.tenant, deleted_at__isnull=False,
        )
        QuotaService.get_or_create_user_quota(request.user)
        QuotaService.get_or_create_tenant_quota(request.user.tenant)
        if not QuotaService.consume(request.user, file.size):
            return Response({"error": "Quota exceeded"}, status=403)
        file.restore_record()
    return Response({"status": "restored"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMutateThrottle])
def rename_file(request, file_id):
    """
    Stores a new encrypted filename. The server never sees the plaintext
    name — the client decrypts the old one, lets the user edit it, then
    re-encrypts under the same per-file key (derived from file_id, same as
    at upload time) and sends the new ciphertext/nonce/mac, same shape as
    start_upload. The server's job is just to swap these three fields.
    """
    file = get_object_or_404(
        FileRecord, id=file_id, owner=request.user, tenant=request.user.tenant,
        deleted_at__isnull=True,
    )

    cipher_hex = request.data.get("filename_ciphertext")
    nonce_hex = request.data.get("filename_nonce")
    mac_hex = request.data.get("filename_mac")
    if not cipher_hex or not nonce_hex or not mac_hex:
        return Response({"error": "Filename metadata required"}, status=400)

    try:
        file.filename_ciphertext = bytes.fromhex(cipher_hex)
        file.filename_nonce = bytes.fromhex(nonce_hex)
        file.filename_mac = bytes.fromhex(mac_hex)
    except ValueError:
        return Response({"error": "Invalid filename metadata"}, status=400)

    file.save(update_fields=["filename_ciphertext", "filename_nonce", "filename_mac"])
    return Response({"status": "renamed"})


# ============================================================
# DOWNLOADS & QUOTA
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMetaThrottle])
def get_storage_quota(request):
    quota = QuotaService.get_or_create_user_quota(request.user)

    data = {
        "used_bytes": quota.used_bytes,
        "limit_bytes": quota.limit_bytes,
        "tier": quota.tier,
    }

    # Surface an in-progress cancellation grace period, if any, so the app
    # can show a countdown warning. Cleared automatically once
    # process_subscription_grace_periods acts on it (grace_ends_at/purge_at
    # both go back to null), so this naturally disappears on its own.
    # Checks both providers (billing.services.cross_provider) rather than
    # only Razorpay -- a Play-only subscriber's countdown used to never show.
    from billing.services.cross_provider import pending_grace_or_purge
    pending = pending_grace_or_purge(request.user)
    if pending:
        data["grace_ends_at"] = pending.grace_ends_at
        data["purge_at"] = pending.purge_at

    return Response(data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMetaThrottle])
def download_manifest(request, file_id):
    file = get_object_or_404(
        FileRecord, id=file_id, owner=request.user,
        tenant=request.user.tenant, deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    )
    storage = StorageGateway()
    return HttpResponse(storage.download_bytes(file.manifest_path), content_type="application/json")

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileMetaThrottle])
def download_integrity(request, file_id):
    file = get_object_or_404(
        FileRecord, id=file_id, owner=request.user,
        tenant=request.user.tenant, deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    )
    storage = StorageGateway()
    base = r2_base(file.tenant_id, file.owner_id, file.id)
    key = integrity_key(base)
    if not storage.exists(key):
        if file.integrity_established:
            # UploadService.commit() proved a manifest existed for this file.
            # It's gone now -- not a legacy file, tamper/deletion after the
            # fact. Fail closed with a status the client can't confuse with
            # "never had one" (see IntegrityService.fetch on the client --
            # only 404 means legacy; anything else, including this 409, throws).
            return Response(
                {"error": "Integrity manifest missing", "integrity_established": True},
                status=409,
            )
        return Response({"error": "Integrity manifest not found"}, status=404)
    return HttpResponse(storage.download_bytes(key), content_type="application/octet-stream")

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([FileChunkThrottle])
def download_chunk(request, file_id, index):
    file = get_object_or_404(
        FileRecord, id=file_id, owner=request.user,
        tenant=request.user.tenant, deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    )
    storage = StorageGateway()
    base = r2_base(file.tenant_id, file.owner_id, file.id)
    key = f"{base}/chunks/chunk_{index}.bin"
    if not storage.exists(key):
        return Response({"error": "Chunk not found"}, status=404)
    return HttpResponse(storage.download_bytes(key), content_type="application/octet-stream")