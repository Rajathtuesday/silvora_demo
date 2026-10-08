# Silvora Backend

The Django REST API behind Silvora, a zero-knowledge, end-to-end encrypted (E2EE) cloud storage app. This service routes and stores encrypted bytes. It never has access to file contents, filenames, or the encryption keys that protect them, only ciphertext, encrypted key envelopes, and metadata. The Flutter client lives in a separate repository (`silvora_app`).

## What this is

A multi-tenant SaaS backend for an encrypted personal vault product: users register, get a master encryption key generated on their own device, upload encrypted files in chunks, and pay for storage tiers through a real Razorpay subscription. The server's job is narrow on purpose: authenticate users, store ciphertext and metadata, enforce quota, and run billing, without ever being in a position to read what it's storing.

## How it's built

### The zero-knowledge encryption model

The server never runs the encryption or key derivation itself. Everything sensitive happens on the client (see the Flutter app's `lib/crypto/`); this backend only stores and returns opaque, already-encrypted values.

- **Master key**: generated on-device at registration, 256 bits, never leaves the device unencrypted.
- **Password path**: the client derives a KEK from the user's password with Argon2id, then wraps the master key with it (XChaCha20-Poly1305). The password itself never reaches the server: login, change-password, recovery and account deletion all send `login_auth_key`, derived from the KEK with HKDF (`info="silvora-login-auth"`), and Django stores only a password hash of that value. `POST api/auth/login-kdf-params/` hands the client the salt and Argon2 settings it needs before it can derive the key. Existing accounts were moved over with the `cutover_login_auth_key` command (31 Aug 2026). The server stores that wrapped envelope (`MasterKeyEnvelope.enc_master_key` / `enc_master_key_nonce`) plus the Argon2 parameters used (`kdf_salt`, `kdf_memory_kb`, `kdf_iterations`, `kdf_parallelism`), so a future login can fetch them and re-derive the same KEK.
- **Recovery path, independent of the password**: at registration the client also generates a 24-word recovery phrase, derives a second KEK from it (also Argon2id, its own salt), and wraps a second, separate copy of the same master key. The server stores that second envelope too (`enc_master_key_recovery` and friends), plus `recovery_auth_hash`, a Django password-hash of a value the client derives from the phrase (via HKDF) specifically so the server can verify someone typed the right phrase back without ever learning the phrase itself.
- **Server-side KDF floors**: even though the server never runs Argon2, `users/serializers.py` still rejects unreasonably weak parameters a client might send (memory below 64MB, fewer than 3 iterations, parallelism outside 1 to 8), so the server never faithfully stores and replays trivially brute-forceable settings.
- **Per-file, per-filename, and per-integrity-manifest keys** are all derived client-side from the master key via HKDF, each with its own domain-separated label, so a leaked key for one purpose (say, the integrity key) can't be reused to decrypt the file itself.
- **Integrity manifests**: alongside the encrypted file, the client uploads a separate manifest of per-chunk hashes, encrypted and opaque to the server. The server can't read it, but a commit is refused until one exists (`FileRecord.integrity_established`), which is what lets the server later prove a manifest was present at commit time even if it's since been deleted. A manifest that existed and is now missing fails closed on download, and the client enforces a monotonic version so an older, validly signed copy can't be replayed.

The full spec is `docs/CRYPTOGRAPHY_SPEC.md` (XChaCha20-Poly1305, both envelopes, login authentication, recovery authentication, integrity, KDF floors). The plain-language version for users is the public page `/security/`.

### Multi-tenancy

Shared database, not separate schemas or databases per tenant. Every `User` has a `tenant` FK (`tenants.Tenant`), and every `FileRecord` carries its own `tenant` FK too. Isolation is entirely application-layer: every query in `files/views.py` explicitly filters by both `owner=request.user` and `tenant=request.user.tenant`, and a cross-tenant request for someone else's file simply returns 404, since the row is excluded from the queryset rather than found-then-rejected. A `post_save` signal on `User` auto-creates one individual tenant per user at registration. The `Tenant.tenant_type` field has an `ORG` option in its choices, but nothing in the app currently creates or uses a multi-user organization tenant. It's schema headroom for later, not a working feature today.

### Files: chunked upload lifecycle

A file moves through explicit states (`FileRecord.upload_state`): `initiated`, `uploading`, `completed`, `committed`, or `failed`.

1. `POST /file/start/` registers the file (encrypted filename, size, chunk size) and **reserves** quota for it under a row lock, before a single byte has actually uploaded.
2. `POST /file/<id>/chunk/<index>/` uploads one encrypted chunk at a time to storage.
3. `GET /file/<id>/resume/` lets the client ask which chunk indices already made it, so an interrupted upload can pick back up instead of restarting.
4. `POST /file/<id>/integrity/` uploads the encrypted, client-signed integrity manifest.
5. `POST /file/<id>/commit/` finalizes the upload: refuses to proceed without an integrity manifest already in place, builds a JSON chunk-offset manifest, and only now actually **consumes** the quota that was reserved in step 1. Calling commit twice is safe; a repeat call returns `already_committed` instead of erroring.

Deleting a file is a soft-delete into Trash with a 7-day retention window (`mark_deleted`); deleting an already-trashed file, or dropping an incomplete upload, is a hard delete. A daily cron job (`purge_trashed_files`) is what actually enforces the 7-day promise, re-locking each row immediately before deletion to avoid a race against a simultaneous restore.

### Storage

Encrypted bytes live in Cloudflare R2, not on this server (`files/services/r2_storage_adapter.py`, used whenever `R2_ACCOUNT_ID` is set). If R2 isn't configured, the app transparently falls back to writing chunks to local disk under `local_r2_storage/` (`files/services/local_storage_gateway.py`), which means the whole upload and download flow works locally with zero cloud credentials. Both gateways expose the same method surface, so the app code that calls them doesn't know or care which one is active.

Object keys follow a fixed path: `Silvora/tenants/{tenant_id}/users/{user_id}/files/{file_id}/chunks/chunk_{index}.bin`.

### Billing

Real recurring subscriptions through Razorpay, on Silvora's own Razorpay account (distinct from any tenant-owned Razorpay keys elsewhere in other projects). There's no official Razorpay SDK here; `billing/services/razorpay_client.py` talks to the API directly with `requests`, and verifies webhook signatures manually with HMAC-SHA256.

There are two ways to pay, side by side:

- **Razorpay, on the web.** `GET api/billing/web-link/` returns a 10-minute signed URL; `/billing/checkout/` is the public checkout page, where the token itself is the credential.
- **Google Play Billing, in the app**, which Play requires for digital subscriptions bought inside an Android app. The app asks `GET api/billing/play/account-id/` for an obfuscated account ID, buys through Play, and sends the purchase token to `POST api/billing/play/verify/`, which checks it with the Google Play Developer API before changing the plan. Google's Real-time Developer Notifications arrive at `POST api/billing/play/rtdn/` (models `PlayBillingPlan`, `PlayBillingSubscription`). A purchase can only ever upgrade the account it was bought for.

Webhook idempotency doesn't use a separate event-ID ledger. Instead, each handler guards itself with the row's own state: `subscription.activated` and `subscription.charged` just set fields, so replaying them is harmless; `payment.failed` is deduped with a 24-hour timestamp window rather than a boolean, so a genuinely new failure weeks later still fires a notification; `subscription.cancelled` and `subscription.completed` explicitly no-op if the subscription is already in that state, so `grace_ends_at`/`purge_at` don't keep getting pushed forward on webhook redelivery. Creating a second subscription for a plan the user is already subscribed to is blocked at the service layer (`AlreadySubscribed`), except that a duplicate `created`-status attempt for the *same* plan reuses the existing row, which is what makes a page reload or double-tap on checkout harmless.

A second daily cron (`process_subscription_grace_periods`) handles what happens after cancellation: downgrade to the Free tier once the 7-day grace period ends, then delete the user's oldest files until they're back under the Free tier's limit if `purge_at` (30 days) arrives and they still haven't re-subscribed.

### Auth

JWT via `djangorestframework-simplejwt` (60-minute access tokens, 7-day refresh, rotation with blacklist-after-rotation). The token endpoints themselves are rate-limited (`ThrottledTokenObtainPairView`, throttle scope `login`).

## Apps

| App | Responsibility |
|---|---|
| `users` | Auth, `MasterKeyEnvelope` (both the password and recovery wrapping), quota, email verification |
| `files` | `FileRecord` state machine, chunked upload/download, integrity manifests, Trash |
| `billing` | `RazorpayPlan` / `Subscription`, checkout, webhook, grace period and purge cron |
| `tenants` | Per-user tenant isolation. No API surface of its own; a data model, not a feature |
| `feedback` | Beta-tester feedback form at `/beta-feedback/` (`TesterFeedback`, read-only in Django admin) |

## API surface

**Auth and account** (`api/auth/`, plus two paths registered at the project's root URLconf):
- `POST api/auth/register/`, `GET api/auth/me/`, `GET api/auth/verify-email/<token>/`, `POST api/auth/resend-verification/`
- `POST api/auth/login-kdf-params/` (public: the salt and Argon2 settings for an email)
- `GET api/auth/master-key/`, `POST api/auth/master-key/setup/`, `POST api/auth/master-key/change-password/`
- `POST api/auth/recover/start/`, `POST api/auth/recover/` (both logged-out)
- `POST api/auth/account/delete/` (confirmed with `login_auth_key`; purges R2 before the database rows)
- `POST api/auth/token/`, `POST api/auth/token/refresh/`

**Files** (mounted at the project root, no `/api/` prefix):
- `POST /file/start/`, `GET /file/<id>/resume/`, `POST /file/<id>/chunk/<index>/`, `POST /file/<id>/integrity/`, `POST /file/<id>/commit/`
- `GET /files/`, `GET /quota/`
- `DELETE /file/<id>/delete/`, `GET /trash/`, `POST /file/<id>/restore/`, `POST /file/<id>/rename/`
- `GET /download/file/<id>/manifest/`, `GET /download/file/<id>/integrity/`, `GET /download/file/<id>/chunk/<index>/`

**Billing**:
- `GET api/billing/web-link/`, `POST api/billing/webhook/` (Razorpay)
- `GET api/billing/play/account-id/`, `POST api/billing/play/verify/`, `POST api/billing/play/rtdn/` (Google Play)
- `GET /billing/checkout/` (public, token-authenticated)

**Public site** (Django templates in `templates/landing/` and `templates/legal/`): `/`, `/security/`, `/vs-google-drive/`, `/privacy/`, `/terms/`. They're listed once in `PUBLIC_PAGES` (`silvora_backend/pages.py`), which `sitemap.xml` and `robots.txt` both read. Every download button uses `settings.PLAY_STORE_URL` through the `site_links` context processor.

**Other**: `/healthz/` (health check), `/beta-feedback/` (tester form), `/admin-tools/send-tester-email/` (staff only), `/app-ads.txt` (deliberately empty: no ads).

## Requirements

Python 3.12, Django 5.2 LTS. PostgreSQL (Neon) in production (`DATABASE_URL`); SQLite is the local default if that's unset, which is fine for development.

## Running locally

```
python -m venv .venv
.venv/Scripts/activate        # or `source .venv/bin/activate` on Linux/macOS
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

With no `.env` and no `DJANGO_SECRET_KEY` set, the server refuses to start unless `DJANGO_DEBUG=True` (a hardcoded secret key was deliberately never committed, see `settings.py`). For local dev, either export `DJANGO_DEBUG=True` or create a `.env` file in this directory, which is loaded automatically.

With no R2 credentials set, uploads still work end to end, just against local disk instead of Cloudflare. This is genuinely useful for local development, not just a fallback for emergencies.

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | Prod only | Server refuses to start without it unless `DJANGO_DEBUG=True` |
| `DJANGO_DEBUG` | No | `"True"` / anything else, default treated as `False` |
| `DJANGO_SECURE` | No | Turns on HSTS, secure cookies, and SSL redirect. Only meaningful behind a TLS-terminating proxy like Render |
| `DATABASE_URL` | Prod | Falls back to local SQLite if unset |
| `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | For real uploads | Cloudflare R2 credentials. Without them, storage falls back to local disk |
| `RESEND_API_KEY` | For email | SMTP password for the Resend relay (`EMAIL_HOST_USER` is always the literal string `resend`) |
| `SITE_BASE_URL` | No | Used to build links in verification/billing emails, defaults to `https://api.silvora.cloud` |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | For billing | Silvora's own Razorpay account |
| `GOOGLE_PLAY_PACKAGE_NAME` | No | Defaults to `cloud.silvora.app` |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` or `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_PATH` | For Play Billing | Service account allowed to call the Play Developer API |

Create a `.env` file in this directory to set these locally. `settings.py` loads it automatically via `python-dotenv` (real environment variables still take precedence).

**Note on `.env.example`**: it currently lists `ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS`, but `settings.py` doesn't actually read either one, both are hardcoded lists in code instead, so setting those env vars has no effect. It also doesn't mention `RESEND_API_KEY`, `SITE_BASE_URL`, or any of the three `RAZORPAY_*` vars, all of which the app does need. Worth reconciling `.env.example` against this table directly.

## Testing

```
python manage.py test
```

188 tests (8 Oct 2026): users 53, files 74, billing 51, public site 10. `python manage.py test --parallel 6` runs them in about two minutes. CI (`.github/workflows/ci.yml`) runs `python manage.py check` and the full suite on every push to `main` and every pull request.

## Scheduled jobs

Two daily cron jobs are wired up in `render.yaml`, both idempotent by design:

- `process_subscription_grace_periods` at 03:00 UTC: downgrades expired-grace subscriptions to Free, then purges excess files for accounts past their purge date.
- `purge_trashed_files` at 04:00 UTC: permanently erases files whose 7-day Trash window has elapsed.

A third command, `cleanup_abandoned_uploads` (purges orphaned R2 chunks from uploads that were started but never committed), exists and works, but **isn't scheduled anywhere**. It's currently a manual-only command. Worth adding to `render.yaml` if abandoned uploads turn out to accumulate in practice.

All three can be run manually with `python manage.py <command_name>`.

## Deployment

Render (`render.yaml`): one web service running `gunicorn silvora_backend.wsgi:application`, build step runs `pip install`, `collectstatic`, and `migrate`, plus the two cron jobs above. `DJANGO_SECRET_KEY` is generated by Render itself; `DATABASE_URL`, the R2 credentials, and email credentials are all set manually in the Render dashboard rather than committed. The Razorpay variables and `SITE_BASE_URL` aren't declared in `render.yaml` at all, so if billing works in production, they're being set manually outside the blueprint. Confirm this is actually true rather than assuming it.

## Known gaps

- **`.env.example` is behind.** It still lists `ALLOWED_HOSTS`, which `settings.py` doesn't read (allowed hosts are a list in code), and it doesn't mention `RESEND_API_KEY`, `SITE_BASE_URL`, the `RAZORPAY_*` or the `GOOGLE_PLAY_*` variables. Use the table above.
- **`cleanup_abandoned_uploads` isn't scheduled.** See Scheduled jobs.
- **No independent security audit yet.** The public `/security/` page says so too.
- **Android only.** No iOS app or web client yet.

(The dead code and the broken `keep_alive.py` listed here before have since been deleted, and `docs/CRYPTOGRAPHY_SPEC.md` now matches the implementation.)

## Full architecture docs

`docs/` has the full set: `CRYPTOGRAPHY_SPEC.md`, `THREAT_MODEL.md`, `SECURITY_ARCHITECTURE.md`, `ACCESS_CONTROL_MODEL.md`, `DATA_FLOW.md`, `STORAGE_MODEL.md` and `INCIDENT_RESPONSE.md`, brought up to date with the code in September 2026.
