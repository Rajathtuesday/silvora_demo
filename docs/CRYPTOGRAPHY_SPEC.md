# Silvora Cryptography Specification

## Encryption Algorithm

- AES-256-GCM
- Authenticated encryption
- Unique nonce per encryption operation

---

## Key Hierarchy

User Password
    ↓
Key Derivation Function (Argon2 or PBKDF2)
    ↓
Derived Key
    ↓
Decrypt Master Key Envelope
    ↓
Master Key
    ↓
File Encryption Keys

---

## Master Key

- Generated client-side
- 256-bit random value
- Stored encrypted on server
- Never stored in plaintext

---

## Filename Encryption

- AES-GCM
- Ciphertext + nonce + MAC stored in DB
- Server cannot decrypt filenames

---

## Chunk Encryption

- Files split into chunks
- Each chunk encrypted before upload
- Stored as opaque binary in R2

---

## KDF Parameters

Example:
- Argon2 memory: 64MB
- Iterations: 3
- Parallelism: 2

Parameters are stored per-user.