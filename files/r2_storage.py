# files/r2_storage.py
import os
import boto3
from django.conf import settings


class R2Storage:
    """
    Minimal Cloudflare R2 uploader for MVP.
    Only uploads *final encrypted file*.
    """

    def __init__(self):
        self.bucket = settings.R2_BUCKET_NAME
        self.base_url = settings.R2_PUBLIC_URL

        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )

    def upload_final(self, local_path: str, remote_key: str) -> str:
        """
        Uploads final.bin to Cloudflare R2.
        Returns the public URL of the uploaded object.
        """

        if not os.path.exists(local_path):
            raise FileNotFoundError(local_path)

        with open(local_path, "rb") as f:
            self.client.upload_fileobj(
                f,
                self.bucket,
                remote_key,
                ExtraArgs={"ContentType": "application/octet-stream"},
            )

        # Return accessible URL (R2 public endpoint)
        return f"{self.base_url}/{remote_key}"

    def delete_local(self, path: str):
        try:
            os.remove(path)
        except Exception:
            pass
