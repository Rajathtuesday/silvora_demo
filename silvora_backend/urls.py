"""
URL configuration for silvora_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# silvora_backend/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from .healthcheck import healthcheck
from .legal import PrivacyPolicyView
from .pages import LandingView

# SimpleJWT views
from rest_framework_simplejwt.views import TokenRefreshView
from users.views import ThrottledTokenObtainPairView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Landing page
    path('', LandingView.as_view(), name='landing'),

    # auth/token endpoints
    path('api/auth/token/', ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Register & masterkey: these views are defined in users.views
    path('api/auth/', include('users.urls')),

    # Files endpoints
    path('', include('files.urls')),
        # NEW: master key endpoints
        
    # Healthcheck endpoint
    path('healthz/', healthcheck, name='healthcheck'),

    # Legal
    path('privacy/', PrivacyPolicyView.as_view(), name='privacy_policy'),
]

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
