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
from django.http import HttpResponse, HttpResponseRedirect
from django.templatetags.static import static as static_url
from django.utils import timezone
from .healthcheck import healthcheck
from .legal import PrivacyPolicyView, TermsOfServiceView
from .pages import LandingView
from .admin_tools import send_tester_switch_email
from billing.views import billing_checkout_page

# SimpleJWT views
from rest_framework_simplejwt.views import TokenRefreshView
from users.views import ThrottledTokenObtainPairView


def robots_txt(request):
    content = """User-agent: *

# Public — the marketing and legal pages
Allow: /
Allow: /privacy/
Allow: /terms/

# Everything else is either an API, an authenticated app surface, or a
# checkout link that only works with a signed token in the query string —
# none of it is meant to be crawled or indexed.
Disallow: /api/
Disallow: /admin/
Disallow: /admin-tools/
Disallow: /billing/checkout/

Sitemap: https://silvora.cloud/sitemap.xml"""
    return HttpResponse(content, content_type='text/plain')


def sitemap_xml(request):
    # Public, indexable pages only. lastmod is generated fresh on every
    # request rather than hand-typed, so it never goes stale.
    today = timezone.localdate().isoformat()
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://silvora.cloud/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://silvora.cloud/privacy/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.3</priority>
  </url>
  <url>
    <loc>https://silvora.cloud/terms/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.3</priority>
  </url>
</urlset>"""
    return HttpResponse(content, content_type='application/xml')


def app_ads_txt(request):
    # Empty on purpose, not a placeholder to fill in later: this app has no
    # ad monetization, so the correct app-ads.txt is an explicit "nobody is
    # authorized to sell ads for this app" (per the IAB spec), not a 404.
    # An empty file states that; a missing file just leaves it ambiguous,
    # which is exactly the gap ad fraud relies on. Play Console's own
    # crawlers (AdsBot included) were re-checking for this on every visit.
    return HttpResponse("", content_type='text/plain')


def favicon_ico(request):
    return HttpResponseRedirect(static_url('favicon/favicon.ico'))


urlpatterns = [
    path('admin/', admin.site.urls),

    # Landing page
    path('', LandingView.as_view(), name='landing'),

    # SEO
    path('robots.txt', robots_txt),
    path('sitemap.xml', sitemap_xml),
    path('app-ads.txt', app_ads_txt),
    path('favicon.ico', favicon_ico),

    # auth/token endpoints
    path('api/auth/token/', ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Register & masterkey: these views are defined in users.views
    path('api/auth/', include('users.urls')),

    # Files endpoints
    path('', include('files.urls')),
        # NEW: master key endpoints

    # Billing (Razorpay subscriptions)
    path('api/billing/', include('billing.urls')),
    path('billing/checkout/', billing_checkout_page, name='billing_checkout'),


    # Healthcheck endpoint
    path('healthz/', healthcheck, name='healthcheck'),

    # Legal
    path('privacy/', PrivacyPolicyView.as_view(), name='privacy_policy'),
    path('terms/', TermsOfServiceView.as_view(), name='terms_of_service'),

    # Internal, staff-only tools
    path('admin-tools/send-tester-email/', send_tester_switch_email, name='send_tester_switch_email'),
]

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
