from django.http import JsonResponse


def healthcheck(request):
    return JsonResponse({"status": "ok", "service": "silvora-api"}, status=200)   

