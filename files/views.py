# files/views.py

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction

from .models import FileRecord
from .services.upload_service import UploadService
from .services.quota_service import QuotaService


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
    data, status_code = service.upload_chunk(
        file_id,
        index,
        blob.read(),
    )

    return Response(data, status=status_code)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def commit_upload(request, file_id):
    service = UploadService(request.user)
    data, status_code = service.commit(file_id)
    return Response(data, status=status_code)


# ============================================================
# FILE LIST
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_files(request):

    files = FileRecord.objects.filter(
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    )

    return Response([
        {
            "file_id": str(f.id),
            "size": f.size,
            "upload_state": f.upload_state,
            "filename_ciphertext": f.filename_ciphertext.hex() if f.filename_ciphertext else None,
            "filename_nonce": f.filename_nonce.hex() if f.filename_nonce else None,
            "filename_mac": f.filename_mac.hex() if f.filename_mac else None,
            "created_at": f.created_at.isoformat(),
        }
        for f in files
    ])
# ============================================================
# STORAGE QUOTA
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_storage_quota(request):

    quota = QuotaService.get_or_create_user_quota(request.user)

    return Response({
        "used_bytes": quota.used_bytes,
        "limit_bytes": quota.limit_bytes,
    })


# ============================================================
# DELETE (SOFT DELETE → TRASH)
# ============================================================

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_file(request, file_id):

    with transaction.atomic():
        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=request.user,
            tenant=request.user.tenant,
            deleted_at__isnull=True,
        )

        QuotaService.release(request.user, file.size)
        file.mark_deleted()

    return Response({"status": "deleted"})


# ============================================================
# LIST TRASH
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_trash(request):

    trash = FileRecord.objects.filter(
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=False,
    )

    return Response([
    {
        "file_id": str(f.id),
        "size": f.size,
        "deleted_at": f.deleted_at,
        "filename_ciphertext": f.filename_ciphertext.hex(),
        "filename_nonce": f.filename_nonce.hex(),
        "filename_mac": f.filename_mac.hex(),
    }
    for f in trash
    ])


# ============================================================
# RESTORE FROM TRASH
# ============================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def restore_file(request, file_id):

    with transaction.atomic():

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=request.user,
            tenant=request.user.tenant,
            deleted_at__isnull=False,
        )

        success = QuotaService.consume(request.user, file.size)

        if not success:
            return Response({"error": "Quota exceeded"}, status=403)

        file.restore()

    return Response({"status": "restored"})


# ============================================================
# download
# ============================================================
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_file(request, file_id):

    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    )

    return Response({
        "manifest_path": file.manifest_path,
        "final_path": file.final_path,
    })
    
    
    
# ============================================================
# set filename (for testing - to be removed in future)
# ============================================================
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def set_filename_metadata(request, file_id):

    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=True,
    )

    cipher = request.data.get("filename_ciphertext")
    nonce = request.data.get("filename_nonce")
    mac = request.data.get("filename_mac")

    if not cipher or not nonce or not mac:
        return Response({"error": "Missing metadata"}, status=400)

    file.filename_ciphertext = bytes.fromhex(cipher)
    file.filename_nonce = bytes.fromhex(nonce)
    file.filename_mac = bytes.fromhex(mac)

    file.save(update_fields=[
        "filename_ciphertext",
        "filename_nonce",
        "filename_mac",
    ])

    return Response({"status": "metadata_set"})




# ==============================================================================
from django.http import HttpResponse
from .services.storage_gateway import StorageGateway
from .services.upload_service import r2_base


# ============================================================
# DOWNLOAD MANIFEST
# ============================================================
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_manifest(request, file_id):

    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    )

    storage = StorageGateway()

    manifest_bytes = storage.download_bytes(file.manifest_path)

    return HttpResponse(
        manifest_bytes,
        content_type="application/json",
    )


# ============================================================
# DOWNLOAD SINGLE CHUNK
# ============================================================
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_chunk(request, file_id, index):

    file = get_object_or_404(
        FileRecord,
        id=file_id,
        owner=request.user,
        tenant=request.user.tenant,
        deleted_at__isnull=True,
        upload_state=FileRecord.UploadState.COMMITTED,
    )

    storage = StorageGateway()

    base = r2_base(
        file.tenant_id,
        file.owner_id,
        file.id,
    )

    key = f"{base}/chunks/chunk_{index}.bin"

    if not storage.exists(key):
        return Response({"error": "Chunk not found"}, status=404)

    blob = storage.download_bytes(key)

    return HttpResponse(
        blob,
        content_type="application/octet-stream",
    )