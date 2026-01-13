# # files/urls.py
# from django.http import JsonResponse
# from django.urls import path
# from .views_r2_test import r2_test_upload
# from . import views

# urlpatterns = [
#     path("start/", views.start_upload, name="upload-start"),
#     path("resume/<uuid:upload_id>/", views.resume_upload, name="upload-resume"),
#     path("chunk/<uuid:upload_id>/<int:index>/", views.upload_chunk_xchacha, name="upload-chunk"),
#     path("finish/<uuid:upload_id>/", views.finish_upload, name="upload-finish"),
#     path("reset/", views.reset_uploads, name="upload-reset"),

#     path("files/", views.list_files, name="upload-list-files"),
#     path("download/<uuid:file_id>/", views.download_file, name="upload-download"),
#     # path("preview/<uuid:file_id>/", views.preview_file, name="upload-preview"),
#     path("preview/<uuid:file_id>/",lambda request, file_id: JsonResponse({"error": "not implemented"},status=403), name="upload-preview"),

#     path("file/<uuid:upload_id>/", views.delete_upload, name="delete-upload"),

#     #
    # path("file/<uuid:file_id>/manifest/", views.fetch_manifest),
    # path("file/<uuid:file_id>/data/", views.fetch_encrypted_data),
#     # R2 test
#     path("r2-test/", r2_test_upload, name="r2-test-upload"),
    
#     path("trash/", views.list_trash),
#     path("restore/<uuid:file_id>/", views.restore_upload),
    
# ]



# from django.http import JsonResponse
# from django.urls import path
# from .views_r2_test import r2_test_upload
# from . import views

# urlpatterns = [



# path('file/start/', views.start_upload),

# path("file/<uuid:file_id>/resume/", views.resume_upload),

# path("file/<uuid:file_id>/chunk/<int:index>/", views.upload_chunk),

# path("file/<uuid:file_id>/finish/",views.finish_upload),

# path("files/", views.list_files, name="upload-list-files"),

# path('quota/', views.get_storage_quota),

# path("file/<uuid:file_id>/manifest/", views.FileManifestView.as_view()),

# path("file/<uuid:file_id>/data/", views.FileDataView.as_view()),

#     path("file/<uuid:file_id>/delete/", views.delete_file),
#     path("trash/", views.list_trash),
#     path("trash/<uuid:file_id>/restore/", views.restore_upload),
# ]

# files/urls.py

# files/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Upload
    path("file/start/", views.start_upload),
    path("file/<uuid:file_id>/resume/", views.resume_upload),
    path("file/<uuid:file_id>/chunk/<int:index>/", views.upload_chunk),
    path("file/<uuid:file_id>/finish/", views.finish_upload),

    # Preview
    path("file/<uuid:file_id>/manifest/", views.FileManifestView.as_view()),
    path("file/<uuid:file_id>/data/", views.FileDataView.as_view()),

    # Files
    path("quota/", views.get_storage_quota),
    path("files/", views.list_files),

    # Trash lifecycle
    path("file/<uuid:file_id>/delete/", views.delete_file),
    path("trash/", views.list_trash),
    path("file/<uuid:file_id>/restore/", views.restore_upload),
]

