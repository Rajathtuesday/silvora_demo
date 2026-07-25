# silvora_backend/admin_tools.py
"""
Small internal, staff-only tools that don't warrant their own app.

send_tester_switch_email view: lets Rajath type a subject/body and a list of
recipient email addresses into a form and send it in one click -- instead of
hardcoding a fixed message and list in a management command and redeploying
every time either changes.
"""
import re

from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import get_connection, EmailMultiAlternatives
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import render
from django.utils.html import escape
from django.views.decorators.http import require_http_methods

# Starting point loaded into the form -- editable, not locked in. Kept as the
# original tester-switch message since that's still the common case, but any
# subject/body typed into the form is what actually gets sent.
DEFAULT_SUBJECT = "Silvora: quick switch needed — moving from internal to closed testing"

DEFAULT_BODY = """Hi,

Thanks for testing Silvora as an internal tester so far -- really appreciate it. I've now moved into the closed testing stage on the Play Store, which means you'll need to switch over so you keep getting the latest build.

Quick steps:

1. First, opt out of the internal test (otherwise the store may keep showing you the old internal build instead of the new one): Play Store > profile icon > Manage apps & device > find Silvora > leave/opt out of the internal test if that option is there. If you don't see it, just move to step 2 -- opting into closed testing usually handles the switch.

2. Open this link on your phone, using the same Google account you use on that device, and tap "Become a tester":
https://play.google.com/apps/testing/cloud.silvora.app

3. Once you've joined, install/update the app from here:
https://play.google.com/store/apps/details?id=cloud.silvora.app

A couple of things:
- Use the same Google account for both steps.
- If the app doesn't update or shows as already installed, try uninstalling and reinstalling via the closed-test link above.

Same as before -- let me know if anything breaks or feels off. Thanks again for sticking with this!

Rajath
"""

_URL_RE = re.compile(r"(https?://[^\s<]+)")


def _body_to_html(text):
    """
    Turns typed plain text into the HTML alternative: escape first (so any
    stray '<'/'>' the sender typed can't break the markup), auto-link bare
    URLs, blank-line-separated paragraphs, single newlines as <br>.
    """
    escaped = escape(text)
    linked = _URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
    paragraphs = [p for p in linked.split("\n\n") if p.strip()]
    return "\n".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _parse_emails(raw):
    """Split on newlines and commas, strip, dedupe (case-insensitive), validate."""
    candidates = [
        piece.strip()
        for line in raw.splitlines()
        for piece in line.split(",")
    ]
    seen = set()
    valid, invalid = [], []
    for email in candidates:
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            validate_email(email)
            valid.append(email)
        except ValidationError:
            invalid.append(email)
    return valid, invalid


@staff_member_required
@require_http_methods(["GET", "POST"])
def send_tester_switch_email(request):
    context = {
        "subject": DEFAULT_SUBJECT,
        "body": DEFAULT_BODY,
        "raw_input": "",
    }

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "")
        raw = request.POST.get("emails", "")
        context["subject"] = subject
        context["body"] = body
        context["raw_input"] = raw

        valid_emails, invalid_emails = _parse_emails(raw)
        context["invalid_emails"] = invalid_emails

        if not subject or not body.strip():
            context["error"] = "Subject and body can't be empty."
            return render(request, "admin_tools/send_tester_email.html", context)

        if not valid_emails:
            context["error"] = "No valid email addresses found."
            return render(request, "admin_tools/send_tester_email.html", context)

        html_body = _body_to_html(body)

        connection = get_connection()
        connection.open()

        sent, failed = [], []
        for email in valid_emails:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=body,
                to=[email],  # one recipient per message -- no one sees the others
                connection=connection,
            )
            msg.attach_alternative(html_body, "text/html")
            try:
                msg.send()
                sent.append(email)
            except Exception as e:
                failed.append((email, str(e)))

        connection.close()

        context["sent"] = sent
        context["failed"] = failed
        context["result"] = True

    return render(request, "admin_tools/send_tester_email.html", context)
