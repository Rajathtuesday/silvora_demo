import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from files.models import FileRecord
from files.services.storage_gateway import StorageGateway
from files.services.upload_service import r2_base

logger = logging.getLogger("silvora.files")


class Command(BaseCommand):
    help = "Purge abandoned uploads: deletes orphaned R2 chunks and marks expired FileRecords as FAILED."

    def handle(self, *args, **options):
        now = timezone.now()

        expired = FileRecord.objects.filter(
            upload_state__in=[
                FileRecord.UploadState.INITIATED,
                FileRecord.UploadState.UPLOADING,
                FileRecord.UploadState.COMPLETED,
            ],
            upload_expires_at__lt=now,
        )

        storage = StorageGateway()
        count = 0
        purge_failures = 0

        # Iterate rather than bulk .update() so each file's R2 chunks are purged
        # before the DB row is flipped to FAILED — otherwise the chunks become
        # unreachable orphans (the whole bug we're fixing here).
        for file in expired.iterator():
            base = r2_base(file.tenant_id, file.owner_id, file.id)
            try:
                storage.delete_recursive(base)
            except Exception:
                purge_failures += 1
                logger.exception(
                    "Failed to purge R2 chunks for abandoned upload file_id=%s", file.id
                )
                continue  # leave upload_state alone; retry on next run

            file.upload_state = FileRecord.UploadState.FAILED
            file.save(update_fields=["upload_state"])
            count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {count} abandoned upload(s); {purge_failures} failed and will retry next run."
            )
        )
