# files/management/commands/purge_trashed_files.py
import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from files.models import FileRecord
from files.services.storage_gateway import StorageGateway
from files.services.upload_service import r2_base

logger = logging.getLogger("silvora.files")


class Command(BaseCommand):
    """
    Intended to run once a day (Render Cron Job -- see render.yaml).

    mark_deleted() (files/models.py) sets deleted_at/purge_after when a file
    is moved to Trash, but until now nothing ever read purge_after -- trashed
    files sat in R2 indefinitely unless a human opened Trash and deleted the
    same file a second time by hand (files/views.py delete_file's HARD
    DELETE branch). This command is what makes the "auto-purges in 7 days"
    promise shown in the app (file_list_screen.dart's move-to-trash
    SnackBar, trash_screen.dart's empty state) actually true.

    Quota was already released back at the SOFT DELETE step (QuotaService
    .release() runs inside delete_file, before mark_deleted() is even
    called) -- this command must NOT touch quota again here, or a purged
    file would release the same bytes a second time.
    """
    help = "Permanently erase FileRecords whose Trash retention period has elapsed."

    def handle(self, *args, **options):
        now = timezone.now()
        storage = StorageGateway()

        qs = FileRecord.objects.filter(
            deleted_at__isnull=False,
            purge_after__isnull=False,
            purge_after__lte=now,
        )

        purged = 0
        purge_failures = 0

        # .iterator() (as cleanup_abandoned_uploads.py does) so a large
        # trash backlog doesn't load the whole queryset into memory at once.
        for file in qs.iterator():
            try:
                with transaction.atomic():
                    # Re-fetch under a row lock immediately before deleting --
                    # closes the race where a user restores this exact file
                    # (restore_file, also select_for_update as of this
                    # change) between the query above and this loop reaching
                    # it. Without this, a fresh restore could be silently
                    # undone: permanent data loss, plus a permanently leaked
                    # quota reservation, since restore_file's
                    # QuotaService.consume() already ran and nothing would
                    # ever release it again.
                    locked = FileRecord.objects.select_for_update().filter(
                        pk=file.pk, deleted_at__isnull=False, purge_after__lte=now,
                    ).first()
                    if locked is None:
                        continue  # restored (or otherwise changed) since the query above

                    base = r2_base(locked.tenant_id, locked.owner_id, locked.id)
                    storage.delete_recursive(base)
                    locked.delete()
            except Exception:
                purge_failures += 1
                logger.exception("Failed to purge trashed file_id=%s", file.id)
                continue  # leave deleted_at/purge_after alone; retry next run

            purged += 1

        self.stdout.write(self.style.SUCCESS(
            f"Permanently erased {purged} trashed file(s); {purge_failures} failed and will retry next run."
        ))
