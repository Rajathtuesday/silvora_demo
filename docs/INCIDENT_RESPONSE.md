# Incident Response Plan

This is a lightweight playbook, not a full runbook. It exists so the first hour of a real incident has a checklist instead of a blank page.

## If Database Breach Occurs

- Notify users. Per THREAT_MODEL.md, a DB dump alone doesn't give an attacker file content, filenames, or either master-key path, but they now have offline access to every user's encrypted envelopes and the recovery auth hash, worth disclosing plainly rather than downplaying.
- Rotate `DJANGO_SECRET_KEY` and every issued JWT becomes invalid as a result; consider also forcing a fresh login (clear refresh tokens) for all users as a precaution, even though the breach itself doesn't expose them directly.
- Force password reset is not actually meaningful here on its own: a new password doesn't invalidate the leaked old envelope, since the master key itself never changes on a password change (it's re-wrapped, not regenerated). If there's any reason to suspect the master key itself could eventually be derived (e.g., weak passwords across the affected population), the real mitigation is prompting affected users to rotate their actual master key (re-encrypt everything under a fresh one), which is a heavier, currently-manual operation, not a checkbox.
- Confirm the encrypted envelopes and integrity manifests in the dump match what's expected (no unexpected plaintext fields, no logging leak alongside the dump).

## If Storage Breach Occurs

- Validate object integrity: run the same per-chunk hash verification the client does on download, server-side, against a sample of affected objects, to confirm ciphertext wasn't altered, not just read.
- Confirm the encryption layer is intact, an attacker with R2 access has ciphertext and nothing else; verify this hasn't also coincided with a database breach (the two together are what would actually matter, ciphertext alone is not useful without the corresponding envelope).

## If Vulnerability Found

- Patch immediately.
- Publish a security advisory once a fix has shipped, not before, so the disclosure doesn't function as an exploit roadmap.
- Offer a transparent, specific explanation: what was affected, what wasn't, and what action (if any) users actually need to take. A vague "we take security seriously" notice is worse than useful silence.