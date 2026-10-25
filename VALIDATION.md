# Validation

KANA NOTES, revision removing test-mode gating and single-sender limits.

**Test result: 23 tests passed** (`python -m unittest discover -s tests`). Python compilation passes for `app.py`, `core.py` and `tests/test_bot.py`.

## What the suite covers

Original report pack bytes are compared against the preserved original ZIP. All 30 templates are rendered for both Telegram and WhatsApp and checked for unresolved `{{markers}}`; rendering without incident facts must raise.

Configuration is tested against every removed limiter:

- ten senders load from `GMAIL_SENDER` plus `GMAIL_EXTRA_ACCOUNTS_JSON`;
- the service boots with **no** test recipient configured;
- a malformed report address falls back to the official default and records a warning;
- the same address used for test and report routing is harmless;
- duplicate, malformed and refresh-token-less sender entries are skipped rather than fatal;
- no configured sender is a warning, not a crash;
- more than ten senders is truncated to ten with a warning;
- a missing bot token is still fatal, since there is nothing to run without it.

Bot behaviour is tested end to end: owner-only private access, the full flow through fields to preview, **ten concurrent sends to `abuse@telegram.org`**, ten concurrent sends to `abuse@whatsapp.com`, all 30 note numbers routing to all ten senders, custom report destinations honoured, no blocking between consecutive cases, a stale destination still refused at confirm, dual-tap producing exactly one batch of ten, and an ambiguous result recorded as `unknown` without retry. `/settings` is checked to list senders, destinations and warnings.

Transport is tested against a mocked Gmail: MIME construction with attachment bytes intact, `submitted` / `rejected` / `unknown` outcomes, and `AuthError` on failed token refresh.

## Not performed

Google and Telegram calls were mocked throughout. **No real OAuth authorisation, email submission, abuse report or Render deployment was performed.** No message was delivered to any platform.

## Removed in this revision

`SEND_MODE` test/live gating, `/verify_test` and `/verify_code` inbox verification, test-recipient-only routing, the "10× test" toggle, the single-primary-sender fallback, the `live-used:` duplicate-target block, the test-versus-live address divergence check, and the sender count ceiling that raised instead of truncating.

`tests/test_app.py` was removed: it was orphaned from the earlier MONTAi Sentinel project and imported `complaint` / `phone` / `selections`, none of which exist in KANA NOTES. It had been failing at `e7a33c9` before this change.
