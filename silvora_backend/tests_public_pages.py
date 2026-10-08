"""The public site: every page is served, listed in the sitemap and allowed
in robots.txt, every download button uses the one Play Store setting, and the
FAQ structured data matches what visitors read.
"""
import html
import json
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import get_resolver

from .pages import PUBLIC_PAGES

TEMPLATES = Path(settings.BASE_DIR) / "templates"
NEW_PAGES = ["/security/", "/vs-google-drive/"]
TESTING_LINK = "play.google.com/apps/testing/"


# Pages link a favicon through {% static %}; tests don't run collectstatic,
# so use plain static storage instead of the manifest one.
@override_settings(STORAGES={
    **settings.STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class PublicPagesTest(TestCase):

    def test_every_public_page_is_served(self):
        for page in PUBLIC_PAGES:
            response = self.client.get(page.path)
            self.assertEqual(response.status_code, 200, page.path)

    def test_every_template_view_page_is_in_the_list(self):
        # A new TemplateView route that isn't in PUBLIC_PAGES would be left out
        # of the sitemap and robots.txt.
        listed = {page.path for page in PUBLIC_PAGES}
        for pattern in get_resolver().url_patterns:
            view = getattr(pattern.callback, "view_class", None)
            if view is not None and view.__module__ in ("silvora_backend.pages", "silvora_backend.legal"):
                self.assertIn("/" + str(pattern.pattern), listed, str(pattern.pattern))

    def test_sitemap_lists_every_page_once(self):
        body = self.client.get("/sitemap.xml").content.decode()
        locs = re.findall(r"<loc>(.*?)</loc>", body)
        self.assertEqual(sorted(locs), sorted(f"https://silvora.cloud{p.path}" for p in PUBLIC_PAGES))

    def test_robots_allows_every_page(self):
        body = self.client.get("/robots.txt").content.decode()
        for page in PUBLIC_PAGES:
            self.assertIn(f"Allow: {page.path}\n", body + "\n", page.path)

    def test_new_pages_name_themselves_as_canonical(self):
        for path in NEW_PAGES:
            body = self.client.get(path).content.decode()
            self.assertIn(f'<link rel="canonical" href="https://silvora.cloud{path}">', body)

    @override_settings(PLAY_STORE_URL="https://example.test/store")
    def test_every_download_button_uses_the_setting(self):
        for page in PUBLIC_PAGES:
            body = self.client.get(page.path).content.decode()
            self.assertNotIn(TESTING_LINK, body, page.path)
            self.assertIn('href="https://example.test/store"', body, page.path)

    def test_no_template_hard_codes_the_testing_link(self):
        for template in TEMPLATES.rglob("*.html"):
            self.assertNotIn(TESTING_LINK, template.read_text(encoding="utf-8"), template.name)

    def test_titles_and_descriptions_fit_search_results(self):
        for path in NEW_PAGES:
            body = self.client.get(path).content.decode()
            title = html.unescape(re.search(r"<title>(.*?)</title>", body).group(1))
            description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', body).group(1))
            self.assertLessEqual(len(title), 65, path)
            self.assertLessEqual(len(description), 160, path)

    def test_faq_schema_matches_the_visible_questions(self):
        for path in NEW_PAGES:
            body = self.client.get(path).content.decode()
            block = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.S).group(1)
            schema = json.loads(block)
            visible = [html.unescape(q) for q in re.findall(r"<summary>(.*?)</summary>", body)]
            self.assertEqual([q["name"] for q in schema["mainEntity"]], visible, path)
            answers = [html.unescape(re.sub(r"<[^>]+>", "", a)) for a in re.findall(
                r"</summary><p>(.*?)</p></details>", body)]
            self.assertEqual([q["acceptedAnswer"]["text"] for q in schema["mainEntity"]], answers, path)

    def test_no_em_dashes_on_the_new_pages(self):
        for path in NEW_PAGES:
            self.assertNotIn("—", self.client.get(path).content.decode(), path)
