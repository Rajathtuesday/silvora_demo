# # files/services/manifest_service.py

# import json
# import hashlib
# from typing import Dict, List


# class ManifestService:

#     @staticmethod
#     def compute_hash(manifest: Dict) -> str:
#         raw = json.dumps(
#             manifest,
#             sort_keys=True,
#             separators=(",", ":")
#         ).encode()
#         return hashlib.sha256(raw).hexdigest()

#     @staticmethod
#     def create_initial_manifest(
#         file_id: str,
#         filename: str,
#         size: int,
#         chunk_size: int,
#         security_mode: str,
#     ) -> Dict:
#         manifest = {
#             "version": 1,
#             "file_id": file_id,
#             "filename": filename,
#             "file_size": size,
#             "chunk_size": chunk_size,
#             "security_mode": security_mode,
#             "chunks": [],
#         }
#         manifest["server_hash"] = ManifestService.compute_hash(manifest)
#         return manifest

#     @staticmethod
#     def add_or_replace_chunk(
#         manifest: Dict,
#         index: int,
#         ciphertext_size: int,
#         sha256: str,
#         nonce: str,
#         mac: str,
#     ) -> Dict:

#         manifest["chunks"] = [
#             c for c in manifest["chunks"]
#             if c["index"] != index
#         ] + [{
#             "index": index,
#             "ciphertext_size": ciphertext_size,
#             "ciphertext_sha256": sha256,
#             "nonce": nonce,
#             "mac": mac,
#         }]

#         manifest["chunks"].sort(key=lambda c: c["index"])
#         manifest["server_hash"] = ManifestService.compute_hash(manifest)
#         return manifest

#     @staticmethod
#     def finalize_manifest(
#         manifest: Dict,
#         chunks_with_offsets: List[Dict]
#     ) -> Dict:
#         manifest["chunks"] = chunks_with_offsets
#         manifest["server_hash"] = ManifestService.compute_hash(manifest)
#         return manifest


# files/services/manifest_service.py

"""
Strict Zero Knowledge:
Server does NOT interpret manifest.
Client builds, encrypts, validates it.
Server stores it as opaque blob.
"""

class ManifestService:
    pass