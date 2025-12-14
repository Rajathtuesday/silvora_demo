import time
import requests
from django.conf import settings
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Keep Silvora awake by pinging health endpoint"

    def handle(self, *args, **kwargs):
        if not getattr(settings, "APP_URL", None):
            self.stdout.write("APP_URL not set, exiting")
            return

        while True:
            try:
                r = requests.get(f"{settings.APP_URL}/health/", timeout=5)
                self.stdout.write(f"Ping {r.status_code}")
            except Exception as e:
                self.stdout.write(f"Ping failed: {e}")

            time.sleep(300)
