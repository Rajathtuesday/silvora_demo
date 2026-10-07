# Data Flow Description

## Upload

1. User selects a file.
2. Client derives a per-file key and a per-filename key from the master key (HKDF, domain-separated, see CRYPTOGRAPHY_SPEC.md).
3. Client encrypts the filename.
4. `POST /file/start/`: server registers the file record and **reserves** quota for it under a row lock, before a single chunk has actually uploaded. Returns a file ID and an upload-expiry timestamp (24 hours).
5. Client splits the file into 2MB chunks, encrypts each one (its own nonce per chunk), and uploads them one at a time (`POST /file/<id>/chunk/<index>/`). Server stores each chunk as opaque binary in R2 (or local disk in development, see STORAGE_MODEL.md).
6. If the upload is interrupted (app closed, connection lost), the client can later call `GET /file/<id>/resume/` to find out which chunk indices already made it, and continue from there instead of restarting.
7. Client builds a per-chunk SHA-256 integrity manifest from the source file, encrypts it under a third, separately-derived key, and uploads it (`POST /file/<id>/integrity/`).
8. Client calls `POST /file/<id>/commit/`. The server refuses to finalize unless an integrity manifest is already present, builds a JSON chunk-offset manifest, and only now **consumes** the quota that was reserved in step 4. A repeat commit call is a no-op (`already_committed`), not an error.

At no point does plaintext file content, the filename, or any encryption key reach the server. What the server does see: ciphertext, nonces, MACs, and byte sizes.

## Download

1. Client requests the chunk manifest (`GET /download/file/<id>/manifest/`) and the encrypted integrity manifest (`GET /download/file/<id>/integrity/`).
2. Client fetches each chunk (`GET /download/file/<id>/chunk/<index>/`) and decrypts it with the per-file key.
3. Client re-derives the per-file integrity key, decrypts the integrity manifest, and re-hashes every decrypted chunk against it.
4. Any mismatch, reorder, or truncation aborts the download. A file with no integrity manifest at all (legacy) downloads unverified; a manifest that existed once and is now missing is treated as tampering, not silently downgraded.

## Deletion

1. `DELETE /file/<id>/delete/` on an active file soft-deletes it into Trash with a 7-day retention window.
2. Deleting an already-trashed file, or dropping an incomplete upload, is a hard delete.
3. A daily cron job (`purge_trashed_files`) permanently erases anything past its 7-day window, re-locking each row immediately before deletion to avoid a race against a simultaneous restore.
