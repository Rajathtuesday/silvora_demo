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
- Encrypted master key envelope
- File metadata

They cannot:
- Derive master key
- Decrypt file content
- Decrypt filenames

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

## Explicit Non-Goals

Silvora does not protect against:
- Compromised client device
- Weak user passwords
- User voluntarily sharing keys