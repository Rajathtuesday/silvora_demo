from django.db import models


class TesterFeedback(models.Model):
    """One beta tester's response to the closed-testing feedback form.

    The days_used/used_for fields exist specifically because Google Play's
    production-access review checks for real tester engagement, not just
    feedback quality -- these double as that evidence.
    """

    class DaysUsed(models.TextChoices):
        NONE = "0", "0 days"
        LOW = "1-2", "1-2 days"
        MID = "3-5", "3-5 days"
        HIGH = "6+", "6+ days"

    days_used = models.CharField(max_length=3, choices=DaysUsed.choices)

    used_uploading = models.BooleanField(default=False)
    used_downloading = models.BooleanField(default=False)
    used_recovery_phrase = models.BooleanField(default=False)
    used_just_looking = models.BooleanField(default=False)
    used_other = models.CharField(max_length=200, blank=True)

    problems = models.TextField(blank=True)
    confusing = models.TextField(blank=True)
    would_keep_using = models.TextField(blank=True)
    rating = models.PositiveSmallIntegerField()
    other_comments = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feedback #{self.pk} ({self.rating}/5, {self.created_at:%Y-%m-%d})"


class TesterEmail(models.Model):
    """One send-out from the staff email tool: the message, and when it finished.

    Sending happens in the background, so every recipient gets a row in
    TesterEmailRecipient and the status page reads progress from there.
    """

    subject = models.CharField(max_length=300)
    body = models.TextField()
    invalid_addresses = models.TextField(blank=True)  # one per line, as typed
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.created_at:%Y-%m-%d %H:%M})"


class TesterEmailRecipient(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Waiting"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    email_out = models.ForeignKey(TesterEmail, on_delete=models.CASCADE, related_name="recipients")
    address = models.EmailField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=500, blank=True)  # failure reason or why it was skipped
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["email_out", "address"], name="one_row_per_address_per_send_out"),
        ]

    def __str__(self):
        return f"{self.address}: {self.status}"
