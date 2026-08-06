# Silvora Backend

The Django REST API behind Silvora, a zero-knowledge, end-to-end encrypted
(E2EE) cloud storage app. This service routes and stores encrypted bytes; it
never has access to file contents, filenames, or the encryption keys that
protect them. The Flutter client lives in a separate repository
(`Silvora_app`).

## Architecture, in brief

- **Zero-knowledge model**: a user's master key is generated on-device and
  wrapped with a KEK derived from their password (Argon2id). The server
  stores only the encrypted envelope, never the password or the raw key.
- **Files**: chunked, resumable uploads. Each file is encrypted client-side
  (XChaCha20-Poly1305) before it reaches this backend; the server stores
  ciphertext, a manifest describing chunk layout, and a separate
  client-signed integrity manifest it cannot read but can prove existed at
  commit time (`FileRecord.integrity_established`).
- **Storage**: encrypted blobs live in Cloudflare R2 (`files/services/
  storage_gateway.py`), not on this server. The database holds account
  records, encrypted key envelopes, and file metadata only.
- **Tenants**: every user belongs to a tenant; all file/quota queries scope
  by both owner and tenant.
- **Billing**: real recurring Razorpay subscriptions, created via a signed
  web checkout link (not in-app, to sidestep Google Play Billing
  requirements), driven end-to-end by a webhook.

For the full write-up (threat model, crypto spec, data flow, storage model,
access control, incident response), see `docs/`. For an honest, dated
account of what's actually implemented vs. what's been fixed since, see
`project_review.md` and `FIX_PLAN_2.md` at the repo root.

## Apps

| App | Responsibility |
|---|---|
| `users` | Auth, `MasterKeyEnvelope`, recovery phrase, quota, email verification |
| `files` | `FileRecord` state machine, chunked upload/download, integrity manifests, Trash |
| `billing` | `RazorpayPlan`/`Subscription`, checkout, webhook, grace/purge cron |
| `tenants` | Per-user tenant isolation |

## Requirements

Python 3.12. PostgreSQL in production (SQLite is the local default via
`dj_database_url`, fine for development).

## Running locally

```
python -m venv .venv
.venv/Scripts/activate        # or `source .venv/bin/activate` on Linux/macOS
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

With no `.env` and no `DJANGO_SECRET_KEY` set, the server refuses to start
unless `DJANGO_DEBUG=True` (a hardcoded secret key was deliberately never
committed, see the comment in `settings.py`). For local dev, either export
`DJANGO_DEBUG=True` or create a `.env` file in this directory (loaded
automatically, see below).

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | Prod only | Falls back to a per-process random key when `DJANGO_DEBUG=True` |
| `DJANGO_DEBUG` | No | `"True"`/`"False"`, default `False` |
| `DJANGO_SECURE` | No | Turns on HSTS/secure cookies/SSL redirect, only meaningful behind a TLS-terminating proxy (Render) |
| `DATABASE_URL` | Prod | Falls back to local SQLite if unset |
| `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Yes, for real uploads | Cloudflare R2 credentials |
| `RESEND_API_KEY` | For email | SMTP password for the Resend relay (`EMAIL_HOST_USER` is always the literal string `resend`) |
| `SITE_BASE_URL` | No | Used to build links in verification/billing emails, defaults to `https://api.silvora.cloud` |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | For billing | Silvora's own Razorpay account, unrelated to any tenant-owned Razorpay keys elsewhere |

Create a `.env` file in this directory to set these locally; `settings.py`
loads it automatically via `python-dotenv` (real environment variables still
take precedence).

## Testing

```
python manage.py test
```

114 tests as of this writing, across all four apps. CI runs the full suite
on every push/PR.

## Scheduled jobs

Two daily cron jobs (see `render.yaml`), both idempotent by design:

- `process_subscription_grace_periods` — acts on subscriptions past their
  post-cancellation grace/purge dates (downgrade to Free, then delete
  excess files if still over the limit).
- `purge_trashed_files` — permanently erases files whose 7-day Trash
  retention has elapsed. This is what makes the app's "auto-purges in 7
  days" promise to users actually true.

Both can be run manually with `python manage.py <command_name>`.

## API overview

- `api/auth/` — registration, login/token refresh, master key envelope,
  recovery, email verification, account deletion/export.
- `/` (files.urls) — chunked upload/commit, download, list, rename, Trash
  (delete/restore/list), quota.
- `api/billing/` — signed checkout link, Razorpay webhook.
- `/billing/checkout/` — the actual checkout page (public, token-authenticated).
- `/privacy/`, `/terms/` — legal pages.
- `/healthz/` — healthcheck.

## Deployment

Render (`render.yaml`): one web service (gunicorn) plus the two cron jobs
above. `DJANGO_SECURE=True` is set on the real deployment.
