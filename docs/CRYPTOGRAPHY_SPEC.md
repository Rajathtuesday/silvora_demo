# Silvora Cryptography Specification

## Encryption Algorithm

- XChaCha20-Poly1305 (not AES-GCM; corrected from an earlier draft of this doc that no longer matched the implementation)
- Authenticated encryption (ciphertext + Poly1305 MAC)
- 24-byte random nonce per encryption operation, never reused

---

## Key Hierarchy

There are two independent paths to the same master key, not one.

**Password path:**
```
User Password
    -> Argon2id (client-side)
    -> Password KEK
    -> decrypts enc_master_key           -> Master Key
    -> HKDF-SHA256(KEK, info="silvora-login-auth") -> login_auth_key (sent to the server)
```
As of 2026-08-31, the KEK branches two ways, not one. It still unwraps the
master key exactly as before, but it also derives a second, independent
value — `login_auth_key` — and that is what actually reaches the server on
register/login/change-password/recover/delete-account, never the password
itself. See **Login Authentication** below; this replaces an earlier design
where the raw password was sent directly to the login endpoint, which meant
a captured login request (a rogue admin, a compromised server, or a stray
debug log of the request body) gave the server everything needed to
re-derive the KEK and decrypt the vault. Details and full history in
`docs/THREAT_MODEL.md`'s Rogue Admin section.

**Recovery path, entirely independent of the password:**
```
24-word BIP39 recovery phrase (shown once, at registration)
    -> Argon2id (client-side, its own salt)
    -> Recovery KEK
    -> decrypts enc_master_key_recovery
    -> Master Key (the same one, wrapped a second time)
```

Both wrapped copies of the master key exist simultaneously (`MasterKeyEnvelope.enc_master_key` and `enc_master_key_recovery`). Losing the password doesn't mean losing the vault, the recovery phrase unwraps the identical master key through a completely separate KEK. Losing both means the vault cannot be recovered, by design, not even by the server.

**From the master key onward:**
```
Master Key
    -> HKDF-SHA256(masterKey, info="silvora_file_<file_id>")      -> per-file key
    -> HKDF-SHA256(masterKey, info="silvora_filename_<file_id>")  -> per-filename key
    -> HKDF-SHA256(masterKey, info="silvora-integrity-<file_id>") -> per-integrity key
```

Each derivation uses its own domain-separation label. A key leaked for one purpose (say, the integrity key for one file) cannot be reused to decrypt that file's content or filename, or any other file's keys.

---

## Master Key

- Generated client-side, 256-bit random value
- Never transmitted to the server in plaintext, only as two independently-wrapped envelopes (see above)
- Held in memory only on the client while unlocked; not cached to disk

---

## Login Authentication

The server needs to verify a user typed the correct password back, without that verification depending on ever seeing (or being able to reproduce) the value that unwraps the vault. This is done with a value the client derives via `HKDF(passwordKek, info="silvora-login-auth")`, sent as the `password` field to `/api/auth/register/`, `/api/auth/token/`, `/api/auth/master-key/change-password/`, `/api/auth/recover/`, and `/api/auth/account/delete/`. Django's own auth machinery stores and checks this exactly as it would a real password (`User.password`, standard PBKDF2 hash) — it is opaque to Django which string it's hashing, so no change to that machinery was needed, only to what the client sends it.

Since login now needs the account's Argon2id salt/parameters *before* authenticating (to derive the KEK that `login_auth_key` comes from), `POST /api/auth/login-kdf-params/` returns just those parameters, publicly, given an email — never `enc_master_key`. Knowing the KDF parameters alone reveals nothing about the vault; the same reasoning already applied to `/api/auth/recover/start/` below, extended to a second endpoint. Registration doesn't need this endpoint, since the client generates its own fresh salt there.

The server can confirm a `login_auth_key` match; it cannot work backward from a captured value (in transit, in a log, or from a compromised server) to the KEK or the password, for the same HKDF one-wayness reason the recovery-auth-key below already relies on. This is a direct application of that existing pattern to the primary password path, not a new mechanism — see `docs/THREAT_MODEL.md`'s Rogue Admin section for the vulnerability this closed.

---

## Recovery Authentication

The server needs to verify a user typed the correct recovery phrase back, without ever learning the phrase itself. This is done with a value the client derives via `HKDF(recoveryKek, info="silvora-recovery-auth")`, which the server stores as a standard Django password hash (`MasterKeyEnvelope.recovery_auth_hash`). The server can confirm a match; it cannot work backward from that hash to the phrase or the recovery KEK.

---

## Filename Encryption

- XChaCha20-Poly1305, same as file content, but a separate key derivation (see Key Hierarchy above) and a single-shot encryption rather than chunked
- Ciphertext, nonce, and MAC stored in the database (`FileRecord.filename_ciphertext` / `filename_nonce` / `filename_mac`)
- Server cannot decrypt filenames

---

## Chunk Encryption

- Files are split into fixed-size chunks (2MB) before upload
- Each chunk is encrypted client-side with XChaCha20-Poly1305 under the per-file key, with its own fresh random nonce
- Stored as opaque binary in R2 (or local disk in development); the server never sees plaintext bytes or the key

---

## Integrity Verification

Separate from encryption, and worth its own section since it's a real, shipped feature this doc previously didn't mention at all.

- At upload time, the client builds a manifest of `{chunk_index: sha256(plaintext_chunk)}` from the source file, encrypts it under yet another per-file key (see Key Hierarchy), and uploads it alongside the file.
- The server cannot read this manifest, but a commit is refused unless one has already been uploaded (`FileRecord.integrity_established`), which is what lets the server later prove a manifest existed at commit time even if it's since been deleted.
- On download, the client re-hashes every decrypted chunk and compares it against the manifest. A mismatch, reorder, or truncation aborts the download rather than serving corrupted or tampered data.
- A file with no manifest at all (legacy) downloads unverified. A manifest that existed once and is now missing is treated as tampering and fails hard, it does not silently downgrade to unverified.

---

## KDF Parameters

- Algorithm: Argon2id only (an earlier draft of this doc mentioned PBKDF2 as an alternative; nothing in the codebase, client or server, implements a PBKDF2 path)
- Server-enforced minimums, so a client can't submit trivially weak parameters the server would faithfully store and replay forever: memory >= 64MB, iterations >= 3, parallelism between 1 and 8
- Actual parameters currently used by the client at registration, recovery, and password change: memory 64MB, iterations 3, parallelism 1
- Parameters are stored per-user, per-envelope (the password envelope and the recovery envelope each have their own salt and parameter set, since they're independent KEKs)
