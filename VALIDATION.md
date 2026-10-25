# Validation

KANA NOTES, revision surfacing Google's per-sender authorization reason instead of a bare `auth_failed`.

**Test result: 43 tests passed** (`python -m unittest discover -s tests`). Python compilation passes for `app.py`, `core.py`, `gmail_mail.py` and `tests/test_bot.py`.

## What the suite covers

Original report pack bytes are compared against the preserved original ZIP, so the notes themselves are unchanged by this revision.

**Composition.** All 30 notes are composed for both Telegram and WhatsApp and checked for: no unresolved `{{markers}}`, the correct salutation for the platform, a stated concern (`I am reporting`), the target present in the body, the reply address present, the `Prepared with KANA NOTES.` footer, and a body longer than 200 characters. Composition is asserted to take only `platform`, `note` and `target` — no incident answers. Only `account_number` and `reporter_contact` are fillable. Labelled field lines are asserted to stay on their own line rather than being joined into one paragraph.

**No manual path.** `Bot` must no longer expose `attach` or `field`. The application source must contain none of `sendDocument`, `getFile`, `MAX_BYTES`, `clearfiles` or `base64`; cases must carry no `files` key; the `fields` and `evidence` phases must not appear; and the help text must state that there is no manual entry and no attachment upload. The transport is checked by signature: `raw_message(sender, recipient, subject, body, case_id)` and `Gmail.send(account, recipient, subject, body, case_id)` take no files argument, and `add_attachment` is absent from `gmail_mail`.

**Bot behaviour.** Owner-only private access; picking a note goes straight to `review` with no field prompts; the preview shows the sender count and no attachment line; confirm sends ten concurrent emails to `abuse@telegram.org` and ten to `abuse@whatsapp.com`; all 30 note numbers route to all ten senders; free text typed after preview does not change the body that is sent; a photo message attaches nothing; custom report destinations are honoured; two consecutive cases are not blocked; double-tap produces exactly one batch of ten; a stale destination is still refused at confirm; an ambiguous result is recorded as `unknown` and not retried.

**Configuration.** Ten senders load from `GMAIL_SENDER` plus `GMAIL_EXTRA_ACCOUNTS_JSON`; the service boots with no test recipient; a malformed report address falls back to the official default with a warning; duplicate, malformed and refresh-token-less entries are skipped rather than fatal; no sender is a warning, not a crash; more than ten senders truncates to ten; a missing bot token is still fatal.

**Transport.** Mocked Gmail: `submitted` / `rejected` / `unknown` outcomes and `AuthError` on a failed token refresh.

**Authorization diagnosis.** Google's reason survives to the operator. `AuthError` carries `reason`; `reason_from` handles both the OAuth envelope (`error` + `error_description`) and the Gmail API envelope (`error.code` with `error.errors[].reason`). A regression test pins the original defect: `token()` must not catch its own `AuthError` and blank the reason, so an `invalid_grant` body still reaches the caller intact, and so does `invalid_client`. A token endpoint that cannot be reached raises `TransportError`, and `send()` records that as `unknown` — never `auth_failed`, because a network failure is not a credential failure. A blank per-account `client_id` / `client_secret` is treated as absent and falls back to the shared client; a non-blank one is used. At the bot level, a send where every sender raises `AuthError` must show Google's reason in the status message, count the failures, and store each sender's reason in the ledger. `/settings` must report `Distinct refresh tokens: N` while printing no token value, and `Config.diagnostics()` must flag one token reused across all senders and a blank shared client id.

## Not performed

Google and Telegram calls were mocked throughout. **No real OAuth authorisation, email submission, abuse report or Render deployment was performed.** No message was delivered to any platform. The actual reason a real sender fails can only be confirmed by reading the reason this revision now prints, or by inspecting the Google Cloud project directly — the suite cannot reach Google.

## Changed in this revision

| File | Change |
|---|---|
| `gmail_mail.py` | `AuthError` carries Google's `reason`; new `TransportError`; `reason_from()` parses both Google error envelopes; `token()` raises outside its `try` so the reason is never re-wrapped; blank per-account client overrides fall back to the shared client; `send()` returns the refusal reason |
| `core.py` | `Config.diagnostics()`: offline, secret-free summary of token presence, distinct-token count, and shared client status; blank shared `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` becomes a warning instead of silence |
| `app.py` | `auth_failed` records Google's reason per sender; the status message adds a result tally and a repair hint; `/settings` prints the diagnostics block |
| `tests/test_bot.py` | 33 → 43 tests; new coverage listed above |
| `README.md`, `VALIDATION.md` | Document the new output, the reason table and the 7-day testing-mode expiry |

No environment variable was added or removed, and `render.yaml` is unchanged.

## Removed in this revision

| Removed | Where |
|---|---|
| Incident-field state and per-field prompts | `app.py` (`Bot.field`, `fields` phase), `core.py` (`Notes.fields`, `Notes.render`) |
| Free-text answer collection and its 1–1500 character validation | `app.py` (`handle`, `fields` branch) |
| `Bot.attach`, `MAX_BYTES`, JPEG/PNG/PDF sniffing | `app.py` |
| Photo / document download via `getFile`, and attachment preview via `sendDocument` | `app.py` (`handle`, `preview`) |
| `/clearfiles` and the `/preview` command | `app.py` |
| `evidence` phase and the `files` list on cases | `app.py` |
| Attachment transport: `files` parameter, `add_attachment` | `gmail_mail.py` |

`tests/test_bot.py` was rewritten for the note-only flow: 33 tests, replacing the 23 that exercised field collection and attachments.

No environment variable, `render.yaml` value or `.env.example` entry existed only to support manual reports, so none were removed and none were added.
