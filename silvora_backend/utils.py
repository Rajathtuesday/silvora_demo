# silvora_backend/utils.py
"""
Real production incident, 2026-09-07: a brand-new user's very first login
attempt, seconds after successfully registering, failed with "Account
made, but sign-in failed." The actual cause had nothing to do with that
specific account -- django-axes was locking out logins platform-wide.

Behind Render's reverse proxy, request.META['REMOTE_ADDR'] is always
Render's own internal proxy address, never the real visitor's IP (Django
never sees a direct connection at all). django-axes has no built-in
fallback for this (it only checks django-ipware, which isn't installed
here, before falling back to REMOTE_ADDR) -- so every single visitor to
the whole platform was being tracked as the exact same "client" for
lockout purposes. Five failed login attempts from anyone, anywhere,
against any account, locked every real user out of logging in for the
next hour (AXES_COOLOFF_TIME).

Exactly the same bug, already found and fixed once before in a sibling
project (Rasova's core.utils.get_client_ip) -- adapted here for Render's
proxy instead of Cloudflare + Nginx. Render's own proxy sets
X-Forwarded-For reliably; Django is never reachable except through it, so
the header can be trusted here the same way Rasova's version trusts its
own proxy layer.
"""


def get_client_ip(request):
    """
    Real client IP behind Render's reverse proxy.

    X-Forwarded-For can carry a comma-separated chain if there were
    multiple hops; the first entry is the original client, everything
    after it was added by proxies in between. Falls back to REMOTE_ADDR
    for local/direct connections (e.g. running the dev server locally
    with no proxy in front at all).
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
