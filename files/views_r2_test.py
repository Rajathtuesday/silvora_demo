# project_name/files/views_r2_test.py
import boto3
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
import uuid

@csrf_exempt
@require_http_methods(["POST"])
def r2_test_upload(request):

    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "Missing file"}, status=400)

    key = f"tests/{uuid.uuid4()}_{f.name}"

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )

    try:
        s3.upload_fileobj(f, settings.R2_BUCKET_NAME, key)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({
        "status": "ok",
        "bucket": settings.R2_BUCKET_NAME,
        "key": key,
        "url": f"{settings.R2_PUBLIC_BASE}/{key}"
    })
