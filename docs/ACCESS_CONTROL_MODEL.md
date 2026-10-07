# Access Control Model

## Authentication

- JWT-based authentication (djangorestframework-simplejwt): 60-minute access tokens, 7-day refresh tokens
- Access token required for all file endpoints
- Refresh token rotation enabled, with blacklist-after-rotation, so a stolen refresh token can't be replayed after its first legitimate use
- Login itself is rate-limited (a dedicated throttle scope)

**Deliberate exception**: `RecoveryStartView` and `RecoverCompleteView` (the logged-out password-recovery flow) require no JWT at all, by design, since the user has lost their password. These are instead gated by a different secret entirely: the server checks the recovery-phrase-derived auth key against `recovery_auth_hash` (see CRYPTOGRAPHY_SPEC.md). That's the real access control on this path, not JWT possession.

---

## Authorization

All file queries enforce:

WHERE owner = request.user
AND tenant = request.user.tenant

No cross-tenant queries allowed.

---

## Isolation Guarantees

- Each user belongs to exactly one tenant
- FileRecord always stores tenant_id
- All access filtered by tenant and owner

Cross-tenant access returns 404.