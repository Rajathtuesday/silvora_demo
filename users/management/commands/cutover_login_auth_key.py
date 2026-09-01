"""
One-time cutover for the login/KEK-separation fix (2026-08-31).

Before this fix, User.password stored a hash of the user's actual vault
password -- the same value the client used to derive the KEK. After it, the
client sends login_auth_key = HKDF(KEK, "silvora-login-auth") instead, and
User.password stores a hash of THAT. Existing accounts' stored hash is under
the OLD scheme and will never match what an updated client now sends, so
this is a clean cutover, not a lazy/dual-scheme migration (decision made
2026-08-31, appropriate for a small/pre-launch user base):

  - Every existing account gets set_unusable_password() -- Django's built-in
    "no input can ever match this" marker. No login attempt succeeds with
    any password until the account resets one.
  - Recovery (the 24-word phrase flow) is completely unaffected by this
    change -- it already only ever sends a separately-derived
    recovery_auth_key, never the phrase or the password. Every account with
    recovery set up can self-serve a new password through the existing
    logged-out reset flow (RecoverCompleteView) with no new UI needed.
  - Accounts with NO recovery phrase configured (enc_master_key_recovery is
    null) have no self-service path back in and are reported separately --
    handle those manually (direct contact, since there's no other way to
    reach them without breaking zero-knowledge).

Usage:
    python manage.py cutover_login_auth_key            # apply
    python manage.py cutover_login_auth_key --dry-run   # report only, no writes
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from users.models import User, MasterKeyEnvelope


class Command(BaseCommand):
    help = "One-time cutover: invalidate all existing password hashes for the login/KEK-separation fix."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would happen without writing anything.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]

        users_with_recovery = set(
            MasterKeyEnvelope.objects
            .filter(enc_master_key_recovery__isnull=False)
            .values_list("user_id", flat=True)
        )

        all_users = list(User.objects.filter(is_active=True))
        with_recovery = [u for u in all_users if u.id in users_with_recovery]
        without_recovery = [u for u in all_users if u.id not in users_with_recovery]

        self.stdout.write(
            f"{len(all_users)} active accounts total: "
            f"{len(with_recovery)} have recovery set up (self-service reset works), "
            f"{len(without_recovery)} do NOT (need manual contact)."
        )

        if without_recovery:
            self.stdout.write(self.style.WARNING(
                "\nAccounts with no recovery phrase -- will be locked out with no "
                "self-service way back in once this runs:"
            ))
            for u in without_recovery:
                self.stdout.write(f"  {u.email or u.username} (id={u.id})")

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n--dry-run: would set_unusable_password() on {len(all_users)} accounts. "
                "No changes made."
            ))
            return

        with transaction.atomic():
            for u in all_users:
                u.set_unusable_password()
            User.objects.bulk_update(all_users, ["password"])

        self.stdout.write(self.style.SUCCESS(
            f"\nDone: {len(all_users)} accounts can no longer log in with their old "
            "password. Accounts with recovery set up can self-serve a new one via "
            "the existing recovery-phrase flow."
        ))
