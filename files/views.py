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