import os
import shutil
from django.conf import settings

class StorageGateway:

    def __init__(self):
        # Local mock of S3/R2 for Development Environment
        self.local_dir = os.path.join(settings.BASE_DIR, "local_r2_storage")
        os.makedirs(self.local_dir, exist_ok=True)

    def upload_bytes(self, data: bytes, key: str):
        full_path = os.path.join(self.local_dir, key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(data)

    def list_chunks(self, base_path: str):
        prefix_dir = os.path.join(self.local_dir, f"{base_path}/chunks/")
        if not os.path.exists(prefix_dir):
            return []

        indices = []
        for filename in os.listdir(prefix_dir):
            if filename.endswith(".bin"):
                try:
                    idx = int(filename.split("chunk_")[1].replace(".bin", ""))
                    indices.append(idx)
                except ValueError:
                    pass

        return sorted(indices)

    def exists(self, key: str):
        full_path = os.path.join(self.local_dir, key)
        return os.path.exists(full_path)

    def calculate_total_chunk_size(self, base_path: str):
        prefix_dir = os.path.join(self.local_dir, f"{base_path}/chunks/")
        if not os.path.exists(prefix_dir):
            return 0

        total = 0
        for filename in os.listdir(prefix_dir):
            if filename.endswith(".bin"):
                total += os.path.getsize(os.path.join(prefix_dir, filename))

        return total

    def download_bytes(self, key: str) -> bytes:
        full_path = os.path.join(self.local_dir, key)
        with open(full_path, "rb") as f:
            return f.read()

    def list_chunk_objects(self, base_path: str):
        prefix_dir = os.path.join(self.local_dir, f"{base_path}/chunks/")
        if not os.path.exists(prefix_dir):
            return []

        objects = []
        for filename in os.listdir(prefix_dir):
            if filename.endswith(".bin"):
                try:
                    full_path = os.path.join(prefix_dir, filename)
                    idx = int(filename.split("chunk_")[1].replace(".bin", ""))
                    size = os.path.getsize(full_path)
                    objects.append((idx, f"{base_path}/chunks/{filename}", size))
                except ValueError:
                    pass

        return sorted(objects, key=lambda x: x[0])

    def delete_recursive(self, key_prefix: str):
        full_path = os.path.join(self.local_dir, key_prefix)
        if os.path.exists(full_path):
            shutil.rmtree(full_path)