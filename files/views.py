from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.http import HttpResponse

from .models import FileRecord
from .services.upload_service import UploadService, r2_base, integrity_key
from .services.quota_service import QuotaService
from .services.storage_gateway import StorageGateway

# ============================================================
# UPLOAD FLOW
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def start_upload(request):
    service = UploadService(request.user)
    data, status_code = service.start(request.data)
    return Response(data, status=status_code)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resume_upload(request, file_id):
    service = UploadService(request.user)
    data, status_code = service.resume(file_id)
    return Response(data, status=status_code)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upload_chunk(request, file_id, index):
    blob = request.FILES.get("chunk")
    if not blob:
        return Response({"error": "Missing chunk"}, status=400)

    service = UploadService(request.user)
    data, status_code = service.upload_chunk(file_id, index, blob.read())
    return Response(data, status=status_code)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
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
def commit_upload(request, file_id):
    service = UploadService(request.user)
    data, status_code = service.commit(file_id)
    return Response(data, status=status_code)

# ============================================================
# FILE LISTING
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
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
def delete_file(request, file_id):
    file = FileRecord.objects.filter(
        id=file_id,
        owner=request.user,
        tenant=request.user.tenant,
    ).first()

    if not file:
        return Response({"error": "File not found"}, status=404)

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
def restore_file(request, file_id):
    with transaction.atomic():
        file = get_object_or_404(
            FileRecord, id=file_id, owner=request.user,
            tenant=request.user.tenant, deleted_at__isnull=False,
        )
        QuotaService.get_or_create_user_quota(request.user)
        QuotaService.get_or_create_tenant_quota(request.user.tenant)
        if not QuotaService.consume(request.user, file.size):
            return Response({"error": "Quota exceeded"}, status=403)
        file.restore_record()
    return Response({"status": "restored"})

# ============================================================
# DOWNLOADS & QUOTA
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_storage_quota(request):
    quota = QuotaService.get_or_create_user_quota(request.user)
    return Response({
        "used_bytes": quota.used_bytes,
        "limit_bytes": quota.limit_bytes,
    })

@api_view(["GET"])
@permission_classes([IsAuthenticated])
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
        return Response({"error": "Integrity manifest not found"}, status=404)
    return HttpResponse(storage.download_bytes(key), content_type="application/octet-stream")

@api_view(["GET"])
@permission_classes([IsAuthenticated])
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