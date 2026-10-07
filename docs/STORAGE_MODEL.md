# Storage Model

Object storage path format:

Silvora/tenants/{tenant_id}/users/{user_id}/files/{file_id}/chunks/chunk_{index}.bin

This structure ensures:
- Strong tenant separation
- No key reuse
- Clear object scoping

## Backend

Chunks live in Cloudflare R2 when `R2_ACCOUNT_ID` (and the other R2 credentials) are configured. If they're not, the app transparently falls back to writing chunks to local disk under `local_r2_storage/` at the repo root, same path structure, same interface, the application code that reads and writes chunks doesn't know or care which one is active. This makes local development fully functional with zero cloud credentials, and is a deliberate design choice, not a stopgap.

## What lives where

- **Database**: account records, both encrypted master-key envelopes (password and recovery path), KDF parameters, encrypted filenames, file metadata (size, timestamps, upload state), the chunk-offset manifest.
- **R2 (or local disk)**: encrypted file chunks and the encrypted integrity manifest, both entirely opaque to the server.

The database never holds file content, even encrypted; the object store never holds anything that identifies which tenant or user a chunk belongs to beyond what's baked into its key path.
