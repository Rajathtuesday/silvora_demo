# Access Control Model

## Authentication

- JWT-based authentication
- Access token required for all file endpoints
- Refresh token rotation enabled

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