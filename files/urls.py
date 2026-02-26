# files/urls.py

from django.urls import path
from . import views

urlpatterns = [
    # Upload flow
    path("file/start/", views.start_upload),
    path("file/<uuid:file_id>/resume/", views.resume_upload),
    path("file/<uuid:file_id>/chunk/<int:index>/", views.upload_chunk),
    path("file/<uuid:file_id>/commit/", views.commit_upload),

    # Files
    path("files/", views.list_files),
    path("quota/", views.get_storage_quota),

    # Trash lifecycle
    path("file/<uuid:file_id>/delete/", views.delete_file),
    path("trash/", views.list_trash),
    path("file/<uuid:file_id>/restore/", views.restore_file),
]