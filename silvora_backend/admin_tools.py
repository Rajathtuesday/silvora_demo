# silvora_backend/admin_tools.py
"""
Small internal, staff-only tools that don't warrant their own app.

The tester email tool: type a subject, a body and a list of addresses, and
send it to everyone, one email per person.

Sending happens in a background thread, not inside the request. A send-out to
thirty testers used to take longer than gunicorn's 30-second request limit, so
the page died with a server error halfway through and nobody could tell who had
received it. Now the request only records one row per recipient and returns at
once; the background thread sends them one by one, slowly enough for the mail
provider's rate limit, and writes each result to its row. The status page reads
those rows.

Nobody gets the same email twice: each row is claimed (pending -> sending)
with a single conditional UPDATE before it is sent, so a second thread, a
double click or a retry cannot send it again, and anyone who already received
this subject in an earlier send-out is skipped.
"""
import re
import threading
import time
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email
from django.db import close_old_connections, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_http_methods, require_POST

from feedback.models import TesterEmail, TesterEmailRecipient

Status = TesterEmailRecipient.Status

# Pause between two emails. Keeps a send-out under the mail provider's default
# rate limit (Resend allows a couple of requests per second).
PACE_SECONDS = 0.6

# A row stuck in "sending" for longer than this belongs to a thread that died
# (a deploy or restart). Retry puts it back in the queue.
STALE_CLAIM = timedelta(minutes=2)

# Starting point loaded into the form: editable, not locked in.
DEFAULT_SUBJECT = "Silvora is now on Google Play 🎉"

DEFAULT_BODY = """Hi everyone,

Thank you for testing Silvora. Your feedback during closed testing helped shape the app, and I'm happy to share some news: Silvora is now available on Google Play in early access. 🚀

📲 Get it here: https://play.google.com/store/apps/details?id=cloud.silvora.app

What this means for you:
- Already have Silvora installed? Just update it from the Play Store. Your vault and files stay as they are.
- New to it, or want to tell a friend? Anyone can now install it directly from the link above. No invite needed. 🙌
- This version (1.0.7) removes photo and video permissions the app did not need. Silvora only uses the files you pick yourself. 🔒

💬 Found a bug or have an idea? Tell me here: https://silvora.cloud/beta-feedback/
Every message is read, and many of your earlier suggestions are already in the app.

⭐ One small request: if Silvora has been useful to you, a short review on the Play Store would mean a lot. It helps other people find a private alternative to the usual cloud storage.

Thank you again for being here from the start. I'll keep you posted by email as new updates arrive. 💙

Warm regards,
Rajath
Silvora · https://silvora.cloud
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


# ---------------------------------------------------------------- delivery

def _reconnect(connection):
    """After a failed send the SMTP session may be dead; start a fresh one."""
    try:
        connection.close()
    except Exception:
        pass
    try:
        connection.open()
    except Exception:
        pass


def deliver(email_out_id):
    """Send every waiting recipient of one send-out. Runs in a background thread."""
    close_old_connections()
    try:
        email_out = TesterEmail.objects.get(pk=email_out_id)
        html_body = _body_to_html(email_out.body)
        connection = get_connection()
        try:
            connection.open()
        except Exception as exc:
            TesterEmailRecipient.objects.filter(email_out=email_out, status=Status.PENDING).update(
                status=Status.FAILED, note=f"Could not connect to the mail server: {exc}"[:500]
            )
            return

        try:
            first = True
            waiting = list(email_out.recipients.filter(status=Status.PENDING).values_list("pk", "address"))
            for pk, address in waiting:
                # Claim the row. If another thread already did, leave it alone.
                claimed = TesterEmailRecipient.objects.filter(pk=pk, status=Status.PENDING).update(
                    status=Status.SENDING, claimed_at=timezone.now()
                )
                if not claimed:
                    continue
                if not first:
                    time.sleep(PACE_SECONDS)
                first = False

                msg = EmailMultiAlternatives(
                    subject=email_out.subject,
                    body=email_out.body,
                    to=[address],  # one recipient per message: no one sees the others
                    connection=connection,
                )
                msg.attach_alternative(html_body, "text/html")
                try:
                    msg.send()
                except Exception as exc:
                    TesterEmailRecipient.objects.filter(pk=pk).update(status=Status.FAILED, note=str(exc)[:500])
                    _reconnect(connection)
                else:
                    TesterEmailRecipient.objects.filter(pk=pk).update(
                        status=Status.SENT, sent_at=timezone.now(), note=""
                    )
        finally:
            try:
                connection.close()
            except Exception:
                pass
    finally:
        TesterEmail.objects.filter(pk=email_out_id).update(finished_at=timezone.now())
        close_old_connections()


def start_delivery(email_out_id):
    """Start sending in the background. Tests replace this to run it inline."""
    threading.Thread(
        target=deliver, args=(email_out_id,), daemon=True, name=f"tester-email-{email_out_id}"
    ).start()


# ---------------------------------------------------------------- views

@staff_member_required
@require_http_methods(["GET", "POST"])
def send_tester_switch_email(request):
    context = {
        "subject": DEFAULT_SUBJECT,
        "body": DEFAULT_BODY,
        "raw_input": "",
        "already_received": "",
        "history": TesterEmail.objects.all()[:10],
    }

    if request.method == "GET":
        return render(request, "admin_tools/send_tester_email.html", context)

    subject = request.POST.get("subject", "").strip()
    body = request.POST.get("body", "")
    raw = request.POST.get("emails", "")
    already_raw = request.POST.get("already_received", "")
    context.update(subject=subject, body=body, raw_input=raw, already_received=already_raw)

    valid_emails, invalid_emails = _parse_emails(raw)
    told_received, _ = _parse_emails(already_raw)

    if not subject or not body.strip():
        context["error"] = "Subject and body can't be empty."
        return render(request, "admin_tools/send_tester_email.html", context)
    if not valid_emails:
        context["error"] = "No valid email addresses found."
        return render(request, "admin_tools/send_tester_email.html", context)

    told = {e.lower() for e in told_received}
    sent_before = {
        a.lower()
        for a in TesterEmailRecipient.objects.filter(
            email_out__subject=subject, status=Status.SENT
        ).values_list("address", flat=True)
    }

    with transaction.atomic():
        email_out = TesterEmail.objects.create(
            subject=subject,
            body=body,
            invalid_addresses="\n".join(invalid_emails),
            created_by=request.user,
        )
        rows = []
        for address in valid_emails:
            key = address.lower()
            if key in told:
                rows.append(TesterEmailRecipient(
                    email_out=email_out, address=address, status=Status.SKIPPED,
                    note="Skipped: listed under 'already received'.",
                ))
            elif key in sent_before:
                rows.append(TesterEmailRecipient(
                    email_out=email_out, address=address, status=Status.SKIPPED,
                    note="Skipped: already sent this subject in an earlier send-out.",
                ))
            else:
                rows.append(TesterEmailRecipient(email_out=email_out, address=address))
        TesterEmailRecipient.objects.bulk_create(rows)

        if any(r.status == Status.PENDING for r in rows):
            transaction.on_commit(lambda: start_delivery(email_out.pk))
        else:
            email_out.finished_at = timezone.now()
            email_out.save(update_fields=["finished_at"])

    return redirect("send_tester_email_status", pk=email_out.pk)


@staff_member_required
@require_http_methods(["GET"])
def tester_email_status(request, pk):
    email_out = get_object_or_404(TesterEmail, pk=pk)
    recipients = list(email_out.recipients.all())
    counts = {s.value: 0 for s in Status}
    for r in recipients:
        counts[r.status] += 1
    still_going = email_out.finished_at is None or counts["pending"] or counts["sending"]
    retryable = counts["failed"] or (
        email_out.finished_at is not None and (counts["pending"] or counts["sending"])
    )
    return render(request, "admin_tools/send_tester_email_status.html", {
        "email_out": email_out,
        "recipients": recipients,
        "counts": counts,
        "invalid": [a for a in email_out.invalid_addresses.splitlines() if a.strip()],
        "still_going": still_going,
        "retryable": retryable,
    })


@staff_member_required
@require_POST
def retry_tester_email(request, pk):
    """Queue again the rows that failed, plus any left behind by a thread that died."""
    email_out = get_object_or_404(TesterEmail, pk=pk)
    with transaction.atomic():
        email_out.recipients.filter(status=Status.FAILED).update(status=Status.PENDING, note="")
        email_out.recipients.filter(
            status=Status.SENDING, claimed_at__lt=timezone.now() - STALE_CLAIM
        ).update(status=Status.PENDING, note="Requeued: the earlier attempt stopped part-way.")
        if email_out.recipients.filter(status=Status.PENDING).exists():
            TesterEmail.objects.filter(pk=pk).update(finished_at=None)
            transaction.on_commit(lambda: start_delivery(pk))
    return redirect("send_tester_email_status", pk=pk)
