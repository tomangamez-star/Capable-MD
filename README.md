# KANA NOTES

Telegram assistant with **Telegram X** and **WhatsApp X**, the original 30-note report pack, and send-only Gmail transport. This is a full replacement project, not a patch. No custom domain, Resend or Supabase is needed.

**Every report is emailed from all configured sender accounts** (up to ten) to the platform abuse address. Nothing about a configured email address can stop the bot booting or sending.

## What changed in this revision

The test-mode gating and the single-sender restrictions were removed:

- **No test mode.** There is no `SEND_MODE` toggle, no test-inbox verification step (`/verify_test`, `/verify_code`) and no test-recipient routing. Every case is a report.
- **No sender limits.** All configured senders are used for every report type, on both platforms, for all 30 notes. The "10× test" toggle is gone because ten senders is now the normal path, not an opt-in.
- **No address-based blocking.** A missing or malformed `TELEGRAM_REPORT_EMAIL` / `WHATSAPP_REPORT_EMAIL` falls back to the official address instead of raising. Test and report addresses may be the same without conflict.
- **No sender allowlist or "verified sender" gate.** Extra senders are loaded straight from `GMAIL_EXTRA_ACCOUNTS_JSON`.
- **No daily/hourly caps, cooldowns or round-robin limits** — there were none, and none were added.
- **Per-sender status is preserved.** Each sender still records `submitted` / `rejected` / `auth_failed` / `unknown` independently, so one failing account does not hide the others.

What is deliberately **kept**:

- Confirmation before sending, with a fresh preview required if the destination or sender list changed.
- Independent per-sender results and `/status`.
- Uncertainty handling: an ambiguous send is **never retried automatically**.

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

`GMAIL_EXTRA_ACCOUNTS_JSON` example:

```json
[
  {"email": "sender2@gmail.com", "refresh_token": "1//..."},
  {"email": "sender3@gmail.com", "refresh_token": "1//..."}
]
```

Entries may also carry their own `client_id` / `client_secret` when the senders belong to different Google Cloud projects. Duplicate addresses and entries with no `refresh_token` are skipped with a warning shown in `/settings`. More than ten senders uses the first ten.

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
4. Answer the incident-field prompts. State only what you actually know; put "unknown" where you are unsure. Each field accepts 1–1500 characters.
5. Optionally attach up to four JPEG/PNG/PDF files (8 MB total), then `/preview`.
6. Review the exact recipients, sender list, subject, body and attachments, then **Confirm and send**.

Commands: `/start` `/settings` `/preview` `/status` `/cancel` `/clearfiles` `/delete` `/help`.

`/settings` lists every configured sender, both report destinations and any configuration warnings.

## Sending behaviour

- A report is submitted from **all configured senders at once**, to the destination for the chosen platform. Ten senders means ten sends per report.
- The email's `Reply-To` is the first sender, so replies land somewhere you read.
- `submitted` means Gmail accepted the send request. It is **not** proof of delivery, inbox placement or platform action. Check Gmail Sent, replies and bounce notices yourself.
- An ambiguous outcome is recorded as `unknown` and **never retried automatically** — a crash or timeout must never silently repeat a report.
- A case can only be sent once; the confirm claim is atomic, so double-tapping cannot double-send.

## Risks to understand before sending

- **Gmail quotas are per account.** A consumer Gmail account can send roughly 500 recipients a day (Google Workspace around 2,000). Ten accounts gives ten separate quotas, not one large one. Sending close to the limit can suspend an account.
- **Identical messages from many accounts look like abuse.** Ten accounts emailing the same platform the same text at the same second is a classic spam signature. Platforms and Google both rate-limit, silently drop, or block such traffic, and accounts can be suspended.
- **Abuse desks rate-limit submissions.** Telegram and WhatsApp accept user reports primarily through their in-app reporting flows; unsolicited bulk email from new addresses is frequently filtered before a human sees it. Volume is not the same as effect.
- **Each refresh token can expire.** A Google OAuth app left in **Testing** normally issues refresh tokens that expire after seven days. Move the app to **In production** and re-authorise, or repeat authorisation when it lapses.
- **This sends real allegations to real platforms.** Only submit accounts you genuinely believe violated policy, with evidence you are authorised to share. Do not use it to harass, mass-flag, or target someone you merely dislike.

## Storage

`DATA_DIR` holds `kana-notes.db` (SQLite): the latest case per owner, the send ledger, and the Telegram poll offset. On Render's free plan the filesystem is ephemeral, so cases and the ledger are lost on every deploy and the service may sleep, interrupting polling. For durability use a paid service with a disk and set `DATA_DIR` to the mount path.

`/delete` removes the latest local case but keeps the send records, so a deletion cannot be used to re-send the same report.

## No automatic actions

KANA NOTES does not ban, report automatically, retry, fail over between senders, or claim delivery. It prepares the message, sends it when you confirm, and reports exactly what each sender returned.
