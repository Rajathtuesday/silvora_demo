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
- Generates master key
- Derives encryption keys
- Encrypts filenames
- Encrypts file content
- Uploads encrypted chunks

Server:
- Stores encrypted metadata
- Stores encrypted chunks in R2
- Enforces tenant isolation
- Enforces quotas
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

- Derive master keys
- Decrypt file content
- Decrypt filenames
- Access other tenant files

Provided that:
- The client device is secure
- The user password is strong
- Cryptographic primitives are implemented correctly
