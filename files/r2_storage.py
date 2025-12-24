# # files/r2_storage.py

# import boto3
# from django.conf import settings
# from botocore.exceptions import ClientError


# class R2Storage:
#     def __init__(self):
#         self.client = boto3.client(
#             "s3",
#             endpoint_url=settings.R2_ENDPOINT,
#             aws_access_key_id=settings.R2_ACCESS_KEY_ID,
#             aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
#             region_name="auto",
#         )
#         self.bucket = settings.R2_BUCKET_NAME

#     # -------------------------------------------------
#     # Upload
#     # -------------------------------------------------
#     def upload_final(self, local_path: str, remote_key: str, content_type=None):
#         """
#         Upload file to R2.
#         IMPORTANT:
#           - Returns NOTHING (store remote_key in DB)
#           - Does NOT expose public URLs
#         """
#         extra_args = {}
#         if content_type:
#             extra_args["ContentType"] = content_type

#         self.client.upload_file(
#             Filename=local_path,
#             Bucket=self.bucket,
#             Key=remote_key,
#             ExtraArgs=extra_args if extra_args else None,
#         )

#     # -------------------------------------------------
#     # Stream (preview / download)
#     # -------------------------------------------------
#     def open_stream(self, remote_key: str):
#         """
#         Returns:
#           - stream (StreamingBody)
#           - content_type (str or None)

#         NEVER loads full file into memory.
#         """
#         try:
#             obj = self.client.get_object(
#                 Bucket=self.bucket,
#                 Key=remote_key,
#             )
#             return obj["Body"], obj.get("ContentType")

#         except self.client.exceptions.NoSuchKey:
#             raise FileNotFoundError(f"R2 object not found: {remote_key}")

#         except ClientError as e:
#             raise RuntimeError(f"R2 open_stream error: {e}")

#     # -------------------------------------------------
#     # Delete (for purge)
#     # -------------------------------------------------
#     def delete_object(self, remote_key: str):
#         try:
#             self.client.delete_object(
#                 Bucket=self.bucket,
#                 Key=remote_key,
#             )
#         except ClientError as e:
#             raise RuntimeError(f"R2 delete failed: {e}")




import boto3
from django.conf import settings

class R2Storage:
    def __init__(self):
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        self.bucket = settings.R2_BUCKET_NAME

    def upload_file(self, local_path: str, key: str):
        with open(local_path, "rb") as f:
            self.client.upload_fileobj(
                f,
                self.bucket,
                key,
                ExtraArgs={"ContentType": "application/octet-stream"},
            )

    def open_stream(self, key: str):
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"], obj["ContentLength"]
import boto3
from django.conf import settings

class R2Storage:
    def __init__(self):
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        self.bucket = settings.R2_BUCKET_NAME

    def upload_file(self, local_path: str, key: str):
        with open(local_path, "rb") as f:
            self.client.upload_fileobj(
                f,
                self.bucket,
                key,
                ExtraArgs={"ContentType": "application/octet-stream"},
            )

    def open_stream(self, key: str):
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"], obj["ContentLength"]
