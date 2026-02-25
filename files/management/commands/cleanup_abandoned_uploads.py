from django.core.management.base import BaseCommand
from django.utils import timezone
from files.models import FileRecord

class Command(BaseCommand):
    help = "Cleanup expired uploads"

    def handle(self, *args, **options):
        now = timezone.now()

        expired = FileRecord.objects.filter(
            upload_state__in=[
                FileRecord.STATE_INITIATED,
                FileRecord.STATE_UPLOADING,
                FileRecord.STATE_COMPLETED,
            ],
            upload_expires_at__lt=now,
        )

        count = expired.count()

        expired.update(upload_state=FileRecord.STATE_FAILED)

        self.stdout.write(
            self.style.SUCCESS(f"Marked {count} uploads as FAILED")
        )