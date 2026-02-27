# Storage Model

Object storage path format:

Silvora/tenants/{tenant_id}/users/{user_id}/files/{file_id}/chunks/chunk_{index}.bin

This structure ensures:
- Strong tenant separation
- No key reuse
- Clear object scoping
