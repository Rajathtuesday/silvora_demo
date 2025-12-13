# # files/urls.py
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

#     # Soft delete (used by your app now)
#     path("file/<uuid:upload_id>/", views.delete_upload, name="delete_upload"),

#     # Trash
#     path("trash/", views.list_trash_files, name="upload-trash-list"),
#     path("trash/<uuid:file_id>/restore/", views.restore_upload, name="upload-trash-restore"),
#     path("trash/<uuid:file_id>/purge/", views.purge_upload, name="upload-trash-purge"),
    
#     # r2 test endpoint
#     path("r2-test/",r2_test_upload, name="r2-test-upload"),
# ]



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

    path("file/<uuid:upload_id>/", views.delete_upload, name="delete_upload"),

    path("trash/", views.list_trash_files, name="upload-trash-list"),
    path("trash/<uuid:file_id>/restore/", views.restore_upload, name="upload-trash-restore"),
    path("trash/<uuid:file_id>/purge/", views.purge_upload, name="upload-trash-purge"),

    # R2 Test endpoint
     # R2 test endpoint
    path("r2-test/", r2_test_upload, name="r2-test-upload"),
]  
