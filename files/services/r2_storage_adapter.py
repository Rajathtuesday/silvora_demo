# files/services/r2_storage_adapter.py
"""
Cloudflare R2 (S3-compatible) implementation of the storage gateway.

Mirrors the method surface of ``local_storage_gateway.StorageGateway`` so the
two are drop-in interchangeable (selected in ``storage_gateway.py`` based on
whether R2 is configured). The server only ever moves opaque encrypted bytes;
it never sees plaintext.
"""

import boto3
from botocore.exceptions import ClientError
from django.conf import settings


class R2StorageGateway:
    def __init__(self):
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        self.bucket = settings.R2_BUCKET_NAME

    # ------------------------------------------------------------------ write
    def upload_bytes(self, data: bytes, key: str):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
        )

    # ------------------------------------------------------------------- read
    def download_bytes(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    # ---------------------------------------------------------------- listing
    def _iter_objects(self, prefix: str):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj

    @staticmethod
    def _chunk_index(key: str):
        # key looks like ".../chunks/chunk_<idx>.bin"
        name = key.rsplit("/", 1)[-1]
        if not name.startswith("chunk_") or not name.endswith(".bin"):
            return None
        try:
            return int(name[len("chunk_"):-len(".bin")])
        except ValueError:
            return None

    def list_chunks(self, base_path: str):
        indices = []
        for obj in self._iter_objects(f"{base_path}/chunks/"):
            idx = self._chunk_index(obj["Key"])
            if idx is not None:
                indices.append(idx)
        return sorted(indices)

    def list_chunk_objects(self, base_path: str):
        objects = []
        for obj in self._iter_objects(f"{base_path}/chunks/"):
            idx = self._chunk_index(obj["Key"])
            if idx is not None:
                objects.append((idx, obj["Key"], obj["Size"]))
        return sorted(objects, key=lambda x: x[0])

    def calculate_total_chunk_size(self, base_path: str):
        return sum(
            obj["Size"]
            for obj in self._iter_objects(f"{base_path}/chunks/")
            if self._chunk_index(obj["Key"]) is not None
        )

    # ----------------------------------------------------------------- delete
    def delete_recursive(self, key_prefix: str):
        keys = [{"Key": obj["Key"]} for obj in self._iter_objects(key_prefix)]
        for i in range(0, len(keys), 1000):  # S3 delete_objects caps at 1000
            batch = keys[i:i + 1000]
            if batch:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": batch},
                )
