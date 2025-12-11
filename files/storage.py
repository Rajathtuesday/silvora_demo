# files/storage.py
import os
from django.conf import settings

class BaseStorage:
    """Abstract storage API."""
    def save_chunk(self, upload_id: str, chunk_name: str, data_bytes: bytes) -> str:
        raise NotImplementedError

    def read_final(self, upload_id: str, filename: str = "final.bin"):
        raise NotImplementedError

    def list_chunks(self, upload_id: str):
        raise NotImplementedError

    def delete_upload(self, upload_id: str):
        raise NotImplementedError

class LocalStorage(BaseStorage):
    def __init__(self, base=None):
        self.base = base or settings.MEDIA_ROOT

    def upload_dir(self, upload_id: str):
        return os.path.join(self.base, "uploads", str(upload_id))

    def chunk_dir(self, upload_id: str):
        return os.path.join(self.upload_dir(upload_id), "chunks")

    def ensure_dirs(self, upload_id: str):
        d = self.chunk_dir(upload_id)
        os.makedirs(d, exist_ok=True)
        return d

    def save_chunk(self, upload_id: str, chunk_name: str, data_bytes: bytes) -> str:
        d = self.ensure_dirs(upload_id)
        p = os.path.join(d, chunk_name)
        with open(p, "wb") as f:
            f.write(data_bytes)
        return p

    def read_final(self, upload_id: str, filename: str = "final.bin"):
        p = os.path.join(self.upload_dir(upload_id), filename)
        return open(p, "rb")

    def list_chunks(self, upload_id: str):
        d = self.chunk_dir(upload_id)
        if not os.path.exists(d):
            return []
        ret = []
        for name in os.listdir(d):
            if name.startswith("chunk_") and name.endswith(".bin"):
                try:
                    idx = int(name.replace("chunk_", "").replace(".bin", ""))
                    ret.append(idx)
                except:
                    pass
        return sorted(ret)

    def delete_upload(self, upload_id: str):
        d = self.upload_dir(upload_id)
        if os.path.exists(d):
            # careful: remove files and directories
            for root, dirs, files in os.walk(d, topdown=False):
                for name in files:
                    try:
                        os.remove(os.path.join(root, name))
                    except:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except:
                        pass
            try:
                os.rmdir(d)
            except:
                pass
