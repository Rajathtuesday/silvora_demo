# Silvora Threat Model

## Attacker Categories

1. Compromised Database
2. Compromised Cloud Storage
3. Rogue Server Administrator
4. Stolen JWT Token
5. Network Interceptor (MITM)
6. Malicious Tenant User

---

## Scenario Analysis

### 1. Database Compromise

Attacker gains full database dump.

They obtain:
- Encrypted filenames
- Both encrypted master key envelopes: the password-wrapped one and the recovery-phrase-wrapped one
- The login authentication hash (`User.password` — as of 2026-08-31, a hash of `login_auth_key`, not the real password; see below) and the recovery authentication hash (a password-hash of a value derived from the recovery phrase, not the phrase itself)
- File metadata

They cannot:
- Derive the master key via either path
- Decrypt file content or filenames
- Derive the password from `User.password`, that field is one more derivation removed from the password (the password goes through Argon2id, then HKDF, before it's hashed) — the same one-way distance the recovery hash already had, now applied to login too
- Derive the recovery phrase from the auth hash, that hash is one more derivation removed from the phrase (the phrase goes through Argon2id, then HKDF, before it's hashed), so recovering it from a leaked hash is at least as hard as brute-forcing the master key envelope directly

Having both envelopes doesn't give the attacker two chances at anything easier, each one still requires the corresponding secret (the password, or the 24-word phrase) that never touched the server.

---

### 1a. Recovery Phrase Compromise

A threat category the earlier version of this document didn't cover, worth its own entry since it's a real, shipped feature with its own attack surface.

If an attacker obtains a user's 24-word recovery phrase (photographed, found written down, phished), they can:
- Recover the master key via the recovery path, exactly as the legitimate user would
- From there, decrypt all of that user's files and filenames, and set a new password, effectively taking over the account

This is symmetric with a stolen password: the recovery phrase is not a lesser secret than the password, it's a second, equally powerful key to the same vault. The product's own onboarding flow (a mandatory 3-word verification quiz after the phrase is shown, a clipboard auto-clear after 60 seconds) is the only mitigation against a user handling the phrase carelessly; there's no server-side way to detect or prevent phrase compromise, since the server never sees the phrase in the first place.

---

### 2. Cloud Storage Compromise

Attacker gains access to R2 bucket.

They obtain:
- Encrypted chunks

They cannot:
- Decrypt chunk contents
- Associate chunks with plaintext

---

### 3. Rogue Admin

Admin can:
- Delete files
- View metadata
- Lock accounts

Admin cannot:
- Decrypt files
- Derive encryption keys

**History, not hypothetical:** until 2026-08-31, this section's last line was
wrong. The login endpoint received the user's actual password (needed to
derive the KEK client-side, but also incidentally readable by anything that
saw the request — a rogue admin with request-log access, a compromised
server, or a future well-intentioned debug commit logging `request.data`),
and the database already stored every KDF parameter needed to redo that
derivation. A captured login, not a database breach, was enough to decrypt
the whole vault — no cryptographic primitive was broken, the design just
handed the server the one input it should never have needed. Fixed by
deriving a second, HKDF-separated value (`login_auth_key`) that the client
sends instead, mirroring the recovery-phrase flow's already-correct pattern.
Full mechanism in `docs/CRYPTOGRAPHY_SPEC.md`'s Login Authentication
section. "Admin cannot derive encryption keys" is accurate again as of this
fix, not before it.

---

### 4. Stolen JWT Token

Attacker can:
- Access API as user

Mitigation:
- Token expiry
- Token refresh logic
- Future: device binding

---

### 5. MITM Attack

Mitigation:
- HTTPS enforced
- TLS termination at trusted provider

---

### 6. Malicious Tenant User

A real, authenticated user attempting to act against another tenant.

They can:
- Query only their own tenant and owner-scoped rows; every file/quota query filters on both explicitly
- Attempt to enumerate other users' files by guessing IDs

They cannot:
- Learn whether a guessed file ID even exists for another tenant, cross-tenant access returns a plain 404, the same response as a nonexistent ID, not a 403 that would confirm the row exists

Known gap, not yet mitigated: a user can start uploads and abandon them before committing, and the cleanup command that reclaims those orphaned chunks (`cleanup_abandoned_uploads`) exists but isn't currently scheduled anywhere, so repeated abandoned uploads could accumulate storage cost over time. Not a confidentiality risk, a cost/availability one.

---

## Explicit Non-Goals

Silvora does not protect against:
- Compromised client device
- Weak user passwords
- User voluntarily sharing keys