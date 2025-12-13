# files/r2_storage.py

import boto3
from django.conf import settings
import os

class R2Storage:

    def __init__(self):
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )

    def upload_final(self, local_path: str, remote_key: str) -> str:
        """Upload final.bin to R2 bucket."""
        self.client.upload_file(local_path, settings.R2_BUCKET_NAME, remote_key)
        return f"{settings.R2_PUBLIC_BASE}/{remote_key}"

    def delete_local(self, local_path: str):
        """Delete local final.bin (Render free tier cleanup)."""
        try:
            os.remove(local_path)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Error deleting local file {local_path}: {e}")