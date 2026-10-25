# Validation

KANA NOTES, revision removing every manual-report path so the only report is one of the 30 shipped notes.

**Test result: 33 tests passed** (`python -m unittest discover -s tests`). Python compilation passes for `app.py`, `core.py`, `gmail_mail.py` and `tests/test_bot.py`.

## What the suite covers

Original report pack bytes are compared against the preserved original ZIP, so the notes themselves are unchanged by this revision.

**Composition.** All 30 notes are composed for both Telegram and WhatsApp and checked for: no unresolved `{{markers}}`, the correct salutation for the platform, a stated concern (`I am reporting`), the target present in the body, the reply address present, the `Prepared with KANA NOTES.` footer, and a body longer than 200 characters. Composition is asserted to take only `platform`, `note` and `target` — no incident answers. Only `account_number` and `reporter_contact` are fillable. Labelled field lines are asserted to stay on their own line rather than being joined into one paragraph.

**No manual path.** `Bot` must no longer expose `attach` or `field`. The application source must contain none of `sendDocument`, `getFile`, `MAX_BYTES`, `clearfiles` or `base64`; cases must carry no `files` key; the `fields` and `evidence` phases must not appear; and the help text must state that there is no manual entry and no attachment upload. The transport is checked by signature: `raw_message(sender, recipient, subject, body, case_id)` and `Gmail.send(account, recipient, subject, body, case_id)` take no files argument, and `add_attachment` is absent from `gmail_mail`.

**Bot behaviour.** Owner-only private access; picking a note goes straight to `review` with no field prompts; the preview shows the sender count and no attachment line; confirm sends ten concurrent emails to `abuse@telegram.org` and ten to `abuse@whatsapp.com`; all 30 note numbers route to all ten senders; free text typed after preview does not change the body that is sent; a photo message attaches nothing; custom report destinations are honoured; two consecutive cases are not blocked; double-tap produces exactly one batch of ten; a stale destination is still refused at confirm; an ambiguous result is recorded as `unknown` and not retried.

**Configuration.** Ten senders load from `GMAIL_SENDER` plus `GMAIL_EXTRA_ACCOUNTS_JSON`; the service boots with no test recipient; a malformed report address falls back to the official default with a warning; duplicate, malformed and refresh-token-less entries are skipped rather than fatal; no sender is a warning, not a crash; more than ten senders truncates to ten; a missing bot token is still fatal.

**Transport.** Mocked Gmail: `submitted` / `rejected` / `unknown` outcomes and `AuthError` on a failed token refresh.

## Not performed

Google and Telegram calls were mocked throughout. **No real OAuth authorisation, email submission, abuse report or Render deployment was performed.** No message was delivered to any platform.

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
