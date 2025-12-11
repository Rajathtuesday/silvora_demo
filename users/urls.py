# users/urls.py
from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from . import views
from .views_masterkey import get_master_key_meta, setup_master_key

urlpatterns = [
    # Auth
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # (Any existing register view, etc.)
    path("register/", views.RegisterView.as_view(), name="register"),

    # Master key endpoints
    path("master-key/meta/", get_master_key_meta, name="master-key-meta"),
    path("master-key/setup/", setup_master_key, name="master-key-setup"),
]
