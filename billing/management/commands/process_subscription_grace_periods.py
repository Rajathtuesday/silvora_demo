# billing/management/commands/process_subscription_grace_periods.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from billing.models import Subscription
from users.models import SubscriptionTier, UserQuota
from files.models import FileRecord
from files.services.quota_service import QuotaService
from files.services.storage_gateway import StorageGateway
from files.services.upload_service import r2_base


class Command(BaseCommand):
    """
    Intended to run once a day (Render Cron Job — see render.yaml).

    Cancelling a subscription doesn't downgrade anyone immediately
    (billing/views.py sets grace_ends_at/purge_at on the cancellation
    webhook instead). This command is what actually acts on those two
    dates:
      - grace_ends_at reached -> downgrade to Free (existing files stay,
        just can't add more while over the new limit).
      - purge_at reached, still over the Free limit -> permanently delete
        the oldest files until back under 1GB.
    Both checks skip a subscription if the user has since started a new
    active one — a resubscription already took precedence via the
    subscription.activated webhook.
    """
    help = "Downgrade/purge accounts whose cancelled subscription's grace period has elapsed."

    def handle(self, *args, **options):
        now = timezone.now()
        downgraded = self._process_downgrades(now)
        purged = self._process_purges(now)
        self.stdout.write(self.style.SUCCESS(
            f"Downgraded {downgraded} subscription(s), purged files for {purged} user(s)."
        ))

    def _has_active_subscription(self, user):
        return Subscription.objects.filter(user=user, status="active").exists()

    def _process_downgrades(self, now):
        count = 0
        qs = Subscription.objects.filter(
            grace_ends_at__isnull=False,
            grace_ends_at__lte=now,
        ).select_related("user")

        for sub in qs:
            user = sub.user
            sub.grace_ends_at = None  # acted on either way; never re-fires
            sub.save(update_fields=["grace_ends_at"])

            if self._has_active_subscription(user):
                continue

            quota, _ = UserQuota.objects.get_or_create(user=user)
            quota.set_tier(SubscriptionTier.FREE)
            count += 1

            if user.email:
                try:
                    send_mail(
                        subject="Your Silvora storage limit changed",
                        message=(
                            "Your Silvora subscription's grace period has ended, and "
                            "your account is now on the free 1GB tier.\n\n"
                            "Nothing has been deleted — all your existing files are "
                            "still there and downloadable. You just can't add more "
                            "until you're back under 1GB, or you resubscribe.\n\n"
                            "Files still over the 1GB limit in 23 more days (30 days "
                            "total since cancellation) will be permanently deleted, "
                            "oldest first, down to the limit. Resubscribe or free up "
                            "space any time before then to keep everything."
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass
        return count

    def _process_purges(self, now):
        count = 0
        qs = Subscription.objects.filter(
            purge_at__isnull=False,
            purge_at__lte=now,
        ).select_related("user")

        for sub in qs:
            user = sub.user
            sub.purge_at = None  # acted on either way; never re-fires
            sub.save(update_fields=["purge_at"])

            if self._has_active_subscription(user):
                continue

            quota, _ = UserQuota.objects.get_or_create(user=user)
            deleted_bytes = self._purge_excess_files(user, quota)
            if deleted_bytes <= 0:
                continue
            count += 1

            if user.email:
                try:
                    send_mail(
                        subject="Silvora: files over your storage limit were removed",
                        message=(
                            "Your Silvora account was still over its 1GB free limit 30 "
                            "days after your subscription ended, so your oldest files "
                            "beyond that limit have now been permanently deleted to "
                            "bring your account back under 1GB.\n\n"
                            "This is the action we warned about by email when your "
                            "subscription first ended."
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass
        return count

    @staticmethod
    def _purge_excess_files(user, quota):
        """Deletes the oldest non-trashed files until used_bytes fits the
        current limit. Returns total bytes actually deleted (0 if already
        within limit — the common case once someone's deleted enough or
        resubscribed without a fresh webhook landing first)."""
        if quota.used_bytes <= quota.limit_bytes:
            return 0

        storage = StorageGateway()
        deleted_bytes = 0
        files = FileRecord.objects.filter(
            owner=user, deleted_at__isnull=True
        ).order_by("created_at")

        for file in files:
            if quota.used_bytes - deleted_bytes <= quota.limit_bytes:
                break
            base = r2_base(file.tenant_id, file.owner_id, file.id)
            storage.delete_recursive(base)
            deleted_bytes += file.size
            file.delete()

        if deleted_bytes > 0:
            QuotaService.release(user, deleted_bytes)

        return deleted_bytes
