from collections import namedtuple

from django.views.generic import TemplateView

# The public pages, in one list. The sitemap and robots.txt (urls.py) both
# read it, so a new page is added once and can't be left out of either.
# silvora_backend/tests_public_pages.py fails if a page view isn't listed.
PublicPage = namedtuple("PublicPage", "path changefreq priority")

PUBLIC_PAGES = (
    PublicPage("/", "weekly", "1.0"),
    PublicPage("/security/", "monthly", "0.8"),
    PublicPage("/vs-google-drive/", "monthly", "0.8"),
    PublicPage("/privacy/", "yearly", "0.3"),
    PublicPage("/terms/", "yearly", "0.3"),
)


class LandingView(TemplateView):
    template_name = "landing/index.html"


class SecurityView(TemplateView):
    template_name = "landing/security.html"


class VsGoogleDriveView(TemplateView):
    template_name = "landing/vs_google_drive.html"
