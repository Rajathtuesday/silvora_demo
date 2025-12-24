# files/urls.py
from django.urls import path
from .views_r2_test import r2_test_upload
from . import views

urlpatterns = [
    path("start/", views.start_upload, name="upload-start"),
    path("resume/<uuid:upload_id>/", views.resume_upload, name="upload-resume"),
    path("chunk/<uuid:upload_id>/<int:index>/", views.upload_chunk_xchacha, name="upload-chunk"),
    path("finish/<uuid:upload_id>/", views.finish_upload, name="upload-finish"),
    path("reset/", views.reset_uploads, name="upload-reset"),

    path("files/", views.list_files, name="upload-list-files"),
    path("download/<uuid:file_id>/", views.download_file, name="upload-download"),
    path("preview/<uuid:file_id>/", views.preview_file, name="upload-preview"),

    path("file/<uuid:upload_id>/", views.delete_upload, name="delete-upload"),

    #
    path("file/<uuid:file_id>/manifest/", views.fetch_manifest),
    path("file/<uuid:file_id>/data/", views.fetch_encrypted_data),
    # R2 test
    path("r2-test/", r2_test_upload, name="r2-test-upload"),
    
    
]
