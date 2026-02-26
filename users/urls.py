# users/urls.py

from django.urls import path
from .views import RegisterView
from .views_masterkey import (
    GetMasterKeyMetaView,
    SetupMasterKeyView,
)

urlpatterns = [
    path("register/", RegisterView.as_view()),
    path("master-key/", GetMasterKeyMetaView.as_view()),
    path("master-key/setup/", SetupMasterKeyView.as_view()),
]