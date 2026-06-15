# users/urls.py

from django.urls import path
from .views import RegisterView
from .views_masterkey import (
    GetMasterKeyMetaView,
    SetupMasterKeyView,
    ChangePasswordView,
    RecoveryStartView,
    RecoverCompleteView,
)
from .views_account import DeleteAccountView

urlpatterns = [
    path("register/", RegisterView.as_view()),
    path("master-key/", GetMasterKeyMetaView.as_view()),
    path("master-key/setup/", SetupMasterKeyView.as_view()),
    path("master-key/change-password/", ChangePasswordView.as_view()),
    path("recover/start/", RecoveryStartView.as_view()),
    path("recover/", RecoverCompleteView.as_view()),
    path("account/delete/", DeleteAccountView.as_view()),
]
