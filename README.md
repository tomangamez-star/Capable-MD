# KANA NOTES

Telegram assistant with **Telegram X** and **WhatsApp X**, the original 30-note report pack, and send-only Gmail transport. This is a full replacement project, not a patch. No custom domain, Resend or Supabase is needed.

**Every report is one of the 30 shipped notes**, emailed from all configured sender accounts (up to ten) to the platform abuse address. Nothing about a configured email address can stop the bot booting or sending.

## What changed in this revision

Manual reporting is gone. The bot can only send from the approved note pack:

- **No manual text entry.** The incident-field prompts are removed. There is no free-text collection step, no `/preview` free-form path and no compose mode.
- **No image or file upload.** Photo, document and screenshot handling is removed, along with the 8 MB / four-file limits, the `/clearfiles` command and the attachment transport in `gmail_mail.py`. `raw_message()` no longer takes a `files` argument at all.
- **No evidence state.** The flow is now exactly: **pick a note → confirm → it sends with all senders.**

What is deliberately **kept**:

- Confirmation before sending, with a fresh preview required if the destination or sender list changed.
- The atomic claim against double-tap: a case can only be sent once.
- Independent per-sender results and `/status`.
- Uncertainty handling: an ambiguous send is **never retried automatically**.

Carried over from the previous revision: no test mode (`SEND_MODE`), no test-recipient routing, no sender allowlist, no daily/hourly caps or cooldowns, and no address-based blocking.

## How a report is composed

The pack's 30 notes are **templates**, not ready-to-send letters: each interleaves fixed report wording with incident-detail slots such as `{{promised_item_or_service}}` and `{{incident_dates_and_timezone}}` — roughly nine slots per note.

Because the bot now collects nothing from the operator, those slots cannot be filled. It does not invent a fact and does not send an unfilled marker. Composition therefore:

1. fills the only slots it can fill mechanically — **`account_number`** (the target you entered) and **`reporter_contact`** (your first sender);
2. keeps every sentence whose slots are mechanical only, preserving the pack's line structure so labelled fields stay on their own line;
3. drops the sentences that would need an incident fact;
4. if that leaves no statement of the concern, adds one line taken from the note's own name — for example *"I am reporting the account identified below for a payment scam."*

The result is grammatical, identical for a given note and target, and never asserts anything that was not supplied. **What it cannot do is describe the specific incident** — an abuse desk reading it learns the category, the account and your reply address, not what happened. If a report needs to describe the incident, the note pack has to supply pre-written text with no slots, or manual entry has to come back.

## Setup

### 1. Gmail send-only OAuth

Enable the Gmail API in a Google Cloud project, configure the consent screen, and add **only** `https://www.googleapis.com/auth/gmail.send` (send-only — the bot cannot read any inbox). Create an OAuth client, then exchange an authorization code for a **refresh token** per account.

Each sender needs its own refresh token. The client id/secret are shared by default.

### 2. Environment variables

Required:

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `ADMIN_IDS` | Comma-separated numeric Telegram user IDs allowed to use the bot |
| `GMAIL_CLIENT_ID` | OAuth client id (shared default) |
| `GMAIL_CLIENT_SECRET` | OAuth client secret (shared default) |
| `GMAIL_SENDER` | Sender #1 address |
| `GMAIL_REFRESH_TOKEN` | Refresh token for sender #1 |
| `GMAIL_EXTRA_ACCOUNTS_JSON` | JSON array of senders #2–#10 |

Optional:

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_REPORT_EMAIL` | `abuse@telegram.org` | Telegram X destination |
| `WHATSAPP_REPORT_EMAIL` | `abuse@whatsapp.com` | WhatsApp X destination |
| `DATA_DIR` | `./data` | SQLite location |
| `PORT` | `10000` | Health server port |

No environment variable existed only to support manual reports, so this revision adds and removes none. `GMAIL_EXTRA_ACCOUNTS_JSON` example:

```json
[
  {"email": "sender2@gmail.com", "refresh_token": "1//..."},
  {"email": "sender3@gmail.com", "refresh_token": "1//..."}
]
```

Entries may also carry their own `client_id` / `client_secret` when the senders belong to different Google Cloud projects. A blank or whitespace-only override counts as absent and falls back to the shared client. Duplicate addresses and entries with no `refresh_token` are skipped with a warning shown in `/settings`. More than ten senders uses the first ten.

**Each entry needs a refresh token minted for the account named beside it.** A token issued to a different address, or one pasted into every entry, authorizes nothing except its own address — which is why `/settings` reports how many *distinct* tokens are configured.

### 3. Run

```bash
pip install -r requirements.txt
python app.py
```

Health check: `GET /healthz` (also `GET /`) returns `{"service":"KANA NOTES","ready":true}` with HTTP 200 once polling has started, 503 otherwise. It returns 503 when no Telegram update has been seen for 180 seconds.

Run **exactly one instance** per bot token — the process owns both long-polling and the health server.

## Using the bot

The bot is private to `ADMIN_IDS` and ignores group chats.

1. Send `/start` and choose **Telegram X** or **WhatsApp X**.
2. Enter the target: `@username` or `https://t.me/...` for Telegram; international format starting with `+` for WhatsApp.
3. Pick a category, then a note from the approved 30-note pack.
4. The exact message is shown immediately: destination, all senders, subject and body.
5. Press **Confirm and send**, or **Cancel**.

Commands: `/start` `/settings` `/status` `/cancel` `/delete` `/help`. `/preview` and `/clearfiles` no longer exist; a note is previewed the moment you pick it.

Text or files sent to the chat during a case are not used. Only the note, the target and your sender list determine what is emailed.

`/settings` lists every configured sender, both report destinations, any configuration warnings, and a diagnostics block: each sender's token presence and length, how many *distinct* refresh tokens exist, and whether the shared `GMAIL_CLIENT_ID` is set. No token value is ever printed.

## When a sender reports `auth_failed`

`auth_failed` means Google refused the refresh token **before** anything was submitted, so nothing was sent from that address. The reason printed after the address is Google's own error text, and the three common ones need three different repairs:

| Google reason | What it means | Repair |
|---|---|---|
| `invalid_grant` — *Token has been expired or revoked* | The token is dead: expired (7 days in **Testing**), revoked by the user or by a password change, or never real | Re-run the OAuth flow for **that** address and replace its `refresh_token` |
| `invalid_client` — *Unauthorized* / *The OAuth client was not found* | That sender's `client_id` / `client_secret` is wrong, or blank while no shared one is set | Fix the credentials, or remove the entry's own `client_id` so the shared pair applies |
| `unauthorized_client` | The token was minted by a different OAuth client than the one now presented | Re-authorize that address against the client in `GMAIL_CLIENT_ID` |
| `invalid_scope` / `access_denied` | Consent was granted without `gmail.send`, or with an extra scope | Re-consent with only `https://www.googleapis.com/auth/gmail.send` |

If **every** sender fails, suspect the shared client (`GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET`). If **exactly the extras** fail while sender #1 works, the shared client is fine and the extras' tokens or their own overrides are the problem. If one failure reason repeats verbatim across unrelated addresses, those entries are not independent — check `/settings` for a repeated token.

A sender that could not be reached because Google never answered is recorded as `unknown`, not `auth_failed`: that is a network problem, not a credential one, and the report is not retried.

## Sending behaviour

- A report is submitted from **all configured senders at once**, to the destination for the chosen platform. Ten senders means ten sends per report.
- The email's `Reply-To` is the first sender, so replies land somewhere you read.
- Each message is plain text with no attachments.
- `submitted` means Gmail accepted the send request. It is **not** proof of delivery, inbox placement or platform action. Check Gmail Sent, replies and bounce notices yourself.
- A sender that fails authorization is reported as `auth_failed` **with Google's own reason**, so a dead token can be told apart from a wrong client id without reading Render logs.
- An ambiguous outcome is recorded as `unknown` and **never retried automatically** — a crash or timeout must never silently repeat a report.
- A case can only be sent once; the confirm claim is atomic, so double-tapping cannot double-send.

## Risks to understand before sending

- **The report cannot describe the incident.** With incident slots dropped, every report for a given note and target is the same text. Ten senders mailing an identical, detail-free allegation is weak at best and looks automated at worst.
- **Gmail quotas are per account.** A consumer Gmail account can send roughly 500 recipients a day (Google Workspace around 2,000). Ten accounts gives ten separate quotas, not one large one. Sending close to the limit can suspend an account.
- **Identical messages from many accounts look like abuse.** Ten accounts emailing the same platform the same text at the same second is a classic spam signature. Platforms and Google both rate-limit, silently drop, or block such traffic, and accounts can be suspended.
- **Abuse desks rate-limit submissions.** Telegram and WhatsApp accept user reports primarily through their in-app reporting flows; unsolicited bulk email from new addresses is frequently filtered before a human sees it. Volume is not the same as effect.
- **Each refresh token can expire.** A Google OAuth app left in **Testing** normally issues refresh tokens that expire after seven days, and all ten senders will lapse at once. Move the app to **In production** (OAuth consent screen → *Publish app*) and re-authorise every sender, or repeat authorisation each time it lapses. Tokens are also revoked when that Google account's password changes or the user removes access.
- **This sends real allegations to real platforms.** Only submit accounts you genuinely believe violated policy. Do not use it to harass, mass-flag, or target someone you merely dislike.

## Storage

`DATA_DIR` holds `kana-notes.db` (SQLite): the latest case per owner, the send ledger, and the Telegram poll offset. Cases no longer store incident answers or attachments — only the note number, target, composed subject and body. On Render's free plan the filesystem is ephemeral, so cases and the ledger are lost on every deploy and the service may sleep, interrupting polling. For durability use a paid service with a disk and set `DATA_DIR` to the mount path.

`/delete` removes the latest local case but keeps the send records, so a deletion cannot be used to re-send the same report.

## No automatic actions

KANA NOTES does not ban, report automatically, retry, fail over between senders, or claim delivery. It prepares the message, sends it when you confirm, and reports exactly what each sender returned.
