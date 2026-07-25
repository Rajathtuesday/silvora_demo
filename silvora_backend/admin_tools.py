# silvora_backend/admin_tools.py
"""
Small internal, staff-only tools that don't warrant their own app.

send_tester_switch_email view: lets Rajath paste a list of internal-tester
email addresses into a form and send them the "switch to closed testing"
email in one click, instead of hardcoding the list in a management command
and redeploying every time the tester list changes.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import get_connection, EmailMultiAlternatives
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

SUBJECT = "Silvora: quick switch needed — moving from internal to closed testing"

TEXT_BODY = """Hi,

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

HTML_BODY = """
<p>Hi,</p>
<p>Thanks for testing Silvora as an internal tester so far &mdash; really appreciate it. I've now moved into the closed testing stage on the Play Store, which means you'll need to switch over so you keep getting the latest build.</p>
<p><strong>Quick steps:</strong></p>
<ol>
  <li>First, opt out of the internal test (otherwise the store may keep showing you the old internal build instead of the new one): Play Store &gt; profile icon &gt; Manage apps &amp; device &gt; find Silvora &gt; leave/opt out of the internal test if that option is there. If you don't see it, just move to step 2 &mdash; opting into closed testing usually handles the switch.</li>
  <li>Open this link on your phone, using the <strong>same Google account</strong> you use on that device, and tap "Become a tester":<br>
    <a href="https://play.google.com/apps/testing/cloud.silvora.app">https://play.google.com/apps/testing/cloud.silvora.app</a>
  </li>
  <li>Once you've joined, install/update the app from here:<br>
    <a href="https://play.google.com/store/apps/details?id=cloud.silvora.app">https://play.google.com/store/apps/details?id=cloud.silvora.app</a>
  </li>
</ol>
<p>A couple of things:</p>
<ul>
  <li>Use the same Google account for both steps.</li>
  <li>If the app doesn't update or shows as already installed, try uninstalling and reinstalling via the closed-test link above.</li>
</ul>
<p>Same as before &mdash; let me know if anything breaks or feels off. Thanks again for sticking with this!</p>
<p>Rajath</p>
"""


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
        "subject": SUBJECT,
        "html_preview": HTML_BODY,
        "raw_input": "",
    }

    if request.method == "POST":
        raw = request.POST.get("emails", "")
        context["raw_input"] = raw
        valid_emails, invalid_emails = _parse_emails(raw)
        context["invalid_emails"] = invalid_emails

        if not valid_emails:
            context["error"] = "No valid email addresses found."
            return render(request, "admin_tools/send_tester_email.html", context)

        connection = get_connection()
        connection.open()

        sent, failed = [], []
        for email in valid_emails:
            msg = EmailMultiAlternatives(
                subject=SUBJECT,
                body=TEXT_BODY,
                to=[email],  # one recipient per message -- no one sees the others
                connection=connection,
            )
            msg.attach_alternative(HTML_BODY, "text/html")
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
