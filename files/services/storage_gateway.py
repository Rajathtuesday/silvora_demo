# files/services/storage_gateway.py

import boto3
from django.conf import settings


class StorageGateway:

    def __init__(self):
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        self.bucket = settings.R2_BUCKET_NAME

    def upload_bytes(self, data: bytes, key: str):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
        )

    def list_chunks(self, base_path: str):

        prefix = f"{base_path}/chunks/"

        response = self.client.list_objects_v2(
            Bucket=self.bucket,
            Prefix=prefix,
        )

        if "Contents" not in response:
            return []

        indices = []

        for obj in response["Contents"]:
            key = obj["Key"]
            if key.endswith(".bin"):
                try:
                    idx = int(
                        key.split("chunk_")[1].replace(".bin", "")
                    )
                    indices.append(idx)
                except:
                    pass

        return sorted(indices)

    def exists(self, key: str):
        try:
            self.client.head_object(
                Bucket=self.bucket,
                Key=key,
            )
            return True
        except:
            return False

    def calculate_total_chunk_size(self, base_path: str):

        prefix = f"{base_path}/chunks/"

        response = self.client.list_objects_v2(
            Bucket=self.bucket,
            Prefix=prefix,
        )

        if "Contents" not in response:
            return 0

        total = 0

        for obj in response["Contents"]:
            if obj["Key"].endswith(".bin"):
                total += obj["Size"]

        return total
    
    import hashlib

    def download_bytes(self, key: str) -> bytes:
        response = self.client.get_object(
            Bucket=self.bucket,
            Key=key,
        )
        return response["Body"].read()

    def list_chunk_objects(self, base_path: str):
        prefix = f"{base_path}/chunks/"
        response = self.client.list_objects_v2(
            Bucket=self.bucket,
            Prefix=prefix,
        )

        if "Contents" not in response:
            return []

        objects = []

        for obj in response["Contents"]:
            key = obj["Key"]
            if key.endswith(".bin"):
                try:
                    idx = int(
                        key.split("chunk_")[1].replace(".bin", "")
                    )
                    objects.append((idx, key, obj["Size"]))
                except:
                    pass

        return sorted(objects, key=lambda x: x[0])