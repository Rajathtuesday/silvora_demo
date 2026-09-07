# Changelog

All notable changes to the Silvora backend are recorded here, newest first.
This file starts 2026-09-07 - it is not a retroactive rewrite of the full
project history. For anything earlier, `git log` is the source of truth.

---

## 2026-09-07

### Fixed
- **Every visitor sharing one django-axes lockout bucket behind Render's proxy** - real production incident: a brand-new user's very first login attempt, seconds after successfully registering, failed with "Account made, but sign-in failed." Nothing was wrong with that account. Behind Render's reverse proxy, `request.META['REMOTE_ADDR']` is always Render's own internal proxy address, never the real visitor's IP, and axes has no built-in fallback for this (it only checks `django-ipware`, which isn't installed here, before falling back to `REMOTE_ADDR`). Every visitor to the whole platform was being tracked as the exact same "client" for lockout purposes, so five failed login attempts from anyone, anywhere, against any account, locked every real user out of logging in for the next hour. Same bug class already found and fixed once before in Rasova, a sibling project (`core.utils.get_client_ip`), adapted here for Render's proxy instead of Cloudflare + Nginx. Wired via `AXES_CLIENT_IP_CALLABLE`.
- **`/api/auth/token/refresh/` crashing with a 500 instead of a clean 401** - a user deleted their own account while the app still held a valid refresh token from that session. The app's background polling then tried to silently refresh it, and the stock SimpleJWT `TokenRefreshSerializer` does an unguarded `User.objects.get()` on the token's embedded user id; with that user gone, it raised `User.DoesNotExist` uncaught. `SafeTokenRefreshSerializer` catches this and returns the 401 the client already knows how to handle (log out, show the login screen).
- 6 new tests across both fixes, full suite 178/178 passing.

---

## Recent history

A condensed summary of earlier shipped work, grouped by theme rather than
commit-by-commit. See `git log` for the exact commits.

- Upgraded Django 5.0.4 to 5.2 LTS and reconciled drifted `requirements.txt` pins.
- Required the current password before allowing a logged-in password change (a stolen session alone was previously enough to lock the real owner out permanently).
- Rejected a Google Play purchase token already bound to a different account.
- Fingerprinted the integrity manifest and commit manifest to close a rollback gap.
- Fixed three medium-severity findings: admin lockout, upload-time quota enforcement, and a path traversal issue.
- Separated login authentication from KEK derivation (the server now never sees anything from which the vault key could be reconstructed, even in principle).
- Brought the privacy and terms pages in line with the landing page's UI; added `robots.txt` and `sitemap.xml`; fixed two recurring 404s from AdsBot and browsers.

---

*For anything before this file started, see the full commit history: `git log`.*
