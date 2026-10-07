# Silvora Security Architecture

## Overview

Silvora is a tenant-aware, zero-knowledge encrypted file storage system.

The system is designed such that:

- All file data is encrypted client-side
- File names are encrypted client-side
- The server does not have access to user master keys
- The server cannot decrypt file content
- Each user belongs to exactly one tenant
- All file operations are scoped by tenant and owner

---

## Core Principles

1. Zero-Knowledge by Design
2. Strict Tenant Isolation
3. Server-Blind Storage
4. Defense-in-Depth
5. Explicit Threat Modeling

---

## High-Level Architecture

Client:
- Generates master key, once, at registration
- Wraps it two independent ways: a password-derived KEK, and a separate recovery-phrase-derived KEK (see CRYPTOGRAPHY_SPEC.md). Both wrapped copies exist simultaneously; either one alone can recover the vault
- Derives per-file, per-filename, and per-integrity keys, each domain-separated
- Encrypts filenames, file content, and a per-chunk integrity manifest
- Uploads encrypted chunks

Server:
- Stores two encrypted master-key envelopes (password path and recovery path), never the key itself
- Stores encrypted metadata and encrypted chunks in R2
- Enforces tenant isolation and quotas
- Enforces a floor on client-submitted KDF parameters (minimum memory/iterations/parallelism), so a weak client-chosen setting can't be stored and faithfully replayed forever
- Refuses to finalize an upload without a client-signed integrity manifest already present, though it cannot read that manifest's contents
- Does not store plaintext

Cloud Storage (R2):
- Stores encrypted chunks only
- No encryption keys stored

---

## Trust Boundaries

Trusted:
- Client device
- Cryptographic primitives

Untrusted:
- Backend server
- Database
- Cloud object storage
- Network

---

## Security Guarantees

The server cannot:

- Derive master keys, from either the password path or the recovery path
- Decrypt file content, filenames, or the integrity manifest
- Access other tenant files
- Learn a user's recovery phrase, even while verifying a recovery attempt (it checks a derived auth key, not the phrase itself)

The system also detects, though it cannot prevent, tampering with stored ciphertext: a client re-hashes every chunk on download against the integrity manifest and aborts on any mismatch, reorder, or truncation, rather than silently serving corrupted data.

Provided that:
- The client device is secure
- The user password is strong, or the recovery phrase was stored safely
- Cryptographic primitives are implemented correctly
