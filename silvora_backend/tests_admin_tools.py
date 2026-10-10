"""The staff tester-email tool.

The old view sent every email inside the request. Thirty-one testers took
longer than gunicorn's 30-second limit, the page died with a server error and
the last tester never got the email. These tests pin the fix: the request only
records rows and returns, sending happens in deliver(), every result is stored,
nobody is emailed twice, and failures can be retried.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from feedback.models import TesterEmail, TesterEmailRecipient
from silvora_backend import admin_tools

Status = TesterEmailRecipient.Status

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    STORAGES=TEST_STORAGES,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class TesterEmailToolTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(username="boss", email="boss@example.com", password="x-pass-123", is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse("send_tester_switch_email")
        # never actually sleep between emails in tests
        patcher = mock.patch.object(admin_tools.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, emails, subject="Hello testers", already="", run=True):
        """Submit the form. With run=True, deliver() runs right away instead of in a thread."""
        started = []
        with mock.patch.object(admin_tools, "start_delivery", side_effect=lambda pk: started.append(pk)):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url, {
                    "subject": subject, "body": "Hi\n\nSee https://silvora.cloud",
                    "emails": "\n".join(emails), "already_received": already,
                })
        if run:
            for pk in started:
                admin_tools.deliver(pk)
        return response, started

    # -- the request itself

    def test_request_returns_without_sending_anything(self):
        response, started = self.post([f"t{i}@example.com" for i in range(31)], run=False)
        email_out = TesterEmail.objects.get()
        self.assertRedirects(response, reverse("send_tester_email_status", args=[email_out.pk]))
        self.assertEqual(len(mail.outbox), 0, "the request must not send email itself")
        self.assertEqual(started, [email_out.pk], "it must hand the work to the background")
        self.assertEqual(email_out.recipients.filter(status=Status.PENDING).count(), 31)

    def test_non_staff_cannot_use_it(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_invalid_addresses_are_recorded_not_sent(self):
        self.post(["good@example.com", "not-an-email"])
        email_out = TesterEmail.objects.get()
        self.assertEqual(email_out.invalid_addresses, "not-an-email")
        self.assertEqual([m.to for m in mail.outbox], [["good@example.com"]])

    # -- delivery

    def test_everyone_gets_one_email_of_their_own_and_rows_say_sent(self):
        self.post(["a@example.com", "b@example.com", "c@example.com"])
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["a@example.com", "b@example.com", "c@example.com"])
        self.assertTrue(all(len(m.to) == 1 for m in mail.outbox))
        self.assertEqual(TesterEmailRecipient.objects.filter(status=Status.SENT).count(), 3)
        self.assertIsNotNone(TesterEmail.objects.get().finished_at)

    def test_sends_are_paced(self):
        self.post(["a@example.com", "b@example.com", "c@example.com"])
        self.assertEqual(self.sleep.call_count, 2)
        self.sleep.assert_called_with(admin_tools.PACE_SECONDS)

    def test_one_failure_does_not_stop_the_rest(self):
        real_send = admin_tools.EmailMultiAlternatives.send

        def flaky(msg, *args, **kwargs):
            if msg.to == ["b@example.com"]:
                raise OSError("mailbox unavailable")
            return real_send(msg, *args, **kwargs)

        with mock.patch.object(admin_tools.EmailMultiAlternatives, "send", flaky):
            self.post(["a@example.com", "b@example.com", "c@example.com"])
        rows = {r.address: r for r in TesterEmailRecipient.objects.all()}
        self.assertEqual(rows["a@example.com"].status, Status.SENT)
        self.assertEqual(rows["c@example.com"].status, Status.SENT)
        self.assertEqual(rows["b@example.com"].status, Status.FAILED)
        self.assertIn("mailbox unavailable", rows["b@example.com"].note)

    def test_mail_server_down_marks_everyone_failed_without_crashing(self):
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.open", side_effect=OSError("connection refused"), create=True):
            self.post(["a@example.com", "b@example.com"])
        self.assertEqual(TesterEmailRecipient.objects.filter(status=Status.FAILED).count(), 2)
        self.assertIn("connection refused", TesterEmailRecipient.objects.first().note)
        self.assertIsNotNone(TesterEmail.objects.get().finished_at)

    def test_a_row_claimed_by_another_thread_is_not_sent_again(self):
        _, started = self.post(["a@example.com", "b@example.com"], run=False)
        TesterEmailRecipient.objects.filter(address="a@example.com").update(status=Status.SENDING, claimed_at=timezone.now())
        admin_tools.deliver(started[0])
        self.assertEqual([m.to for m in mail.outbox], [["b@example.com"]])

    # -- never twice

    def test_already_received_box_skips_those_addresses(self):
        self.post(["a@example.com", "b@example.com"], already="B@example.com")
        self.assertEqual([m.to for m in mail.outbox], [["a@example.com"]])
        skipped = TesterEmailRecipient.objects.get(address="b@example.com")
        self.assertEqual(skipped.status, Status.SKIPPED)

    def test_same_subject_sent_before_is_skipped(self):
        self.post(["a@example.com"])
        mail.outbox.clear()
        self.post(["a@example.com", "new@example.com"])
        self.assertEqual([m.to for m in mail.outbox], [["new@example.com"]])

    def test_different_subject_is_not_skipped(self):
        self.post(["a@example.com"])
        mail.outbox.clear()
        self.post(["a@example.com"], subject="A different update")
        self.assertEqual([m.to for m in mail.outbox], [["a@example.com"]])

    # -- status page and retry

    def test_status_page_lists_every_row(self):
        self.post(["a@example.com", "b@example.com"], already="b@example.com")
        response = self.client.get(reverse("send_tester_email_status", args=[TesterEmail.objects.get().pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "a@example.com")
        self.assertContains(response, "Skipped")
        self.assertNotContains(response, 'http-equiv="refresh"')  # finished, so no auto-refresh

    def test_retry_sends_only_failed_and_stale_rows(self):
        _, started = self.post(["a@example.com", "b@example.com", "c@example.com", "d@example.com"], run=False)
        pk = started[0]
        rows = TesterEmailRecipient.objects
        rows.filter(address="a@example.com").update(status=Status.SENT)
        rows.filter(address="b@example.com").update(status=Status.FAILED, note="timeout")
        rows.filter(address="c@example.com").update(status=Status.SENDING, claimed_at=timezone.now() - timedelta(minutes=10))
        rows.filter(address="d@example.com").update(status=Status.SENDING, claimed_at=timezone.now())  # still in flight
        TesterEmail.objects.filter(pk=pk).update(finished_at=timezone.now())

        retried = []
        with mock.patch.object(admin_tools, "start_delivery", side_effect=lambda p: retried.append(p)):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("retry_tester_email", args=[pk]))
        for p in retried:
            admin_tools.deliver(p)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["b@example.com", "c@example.com"])
