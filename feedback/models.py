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
