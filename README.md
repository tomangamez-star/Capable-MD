# KANA NOTES

Telegram assistant with **Telegram X** and **WhatsApp X**, the original 30-note report pack, Gmail sending, and a separate controlled-inbox test mode. This is a full replacement project, not a patch. No custom domain, Resend or Supabase is needed.

## Push and deploy

1. Extract this ZIP and put its CONTENTS in your repository root. Include app.py, core.py, gmail_mail.py, requirements.txt and the complete assets/ directory. Do not upload your real .env or credentials.
2. Keep your existing Render web service and connect it to the updated repository. Do not start a second copy with the same Telegram token.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python app.py`
5. Health Check Path: `/healthz`
6. Keep existing Gmail and Telegram variables. Set SEND_MODE=test before first launch. Add TEST_RECIPIENT_EMAIL. Save and redeploy.

The included render.yaml uses a free Python web service with ephemeral storage. Render may put a free service to sleep, interrupting Telegram polling until the service wakes. Redeploys/restarts on ephemeral storage can erase cases, inbox verification, preferences and duplicate-send markers. For lasting data, use a paid service with a persistent disk at /var/data and set DATA_DIR=/var/data. Merely setting that path does not create a disk. Review Render's displayed prices before purchasing anything.

## Render environment variables

| Key | Value |
|---|---|
| TELEGRAM_BOT_TOKEN | Your existing BotFather token |
| ADMIN_IDS | Your numeric Telegram user ID; comma-separated if more than one owner |
| GMAIL_CLIENT_ID | Your existing Google OAuth client ID |
| GMAIL_CLIENT_SECRET | Your existing OAuth client secret |
| GMAIL_REFRESH_TOKEN | Refresh token of your primary Gmail |
| GMAIL_SENDER | That authorized Gmail address, plain address only |
| TEST_RECIPIENT_EMAIL | A real inbox you control; verify it in the bot |
| SEND_MODE | test on initial setup; initial default only |
| TELEGRAM_REPORT_EMAIL | Your chosen live Telegram destination, e.g. abuse@telegram.org |
| WHATSAPP_REPORT_EMAIL | Your chosen live WhatsApp destination, e.g. abuse@whatsapp.com; current handling unverified |
| GMAIL_EXTRA_ACCOUNTS_JSON | [] initially; optional array of up to nine extra authorized Gmail accounts |
| DATA_DIR | ./data normally, or /var/data with an actual disk mounted there |

PORT is supplied by Render. TEST_EMAIL from the old bot is accepted as a fallback if TEST_RECIPIENT_EMAIL is missing. Remove RESEND_API_KEY and MAIL_FROM if still present. You can use the existing Google OAuth project and refresh token; no additional Gmail scopes are needed.

Important: the in-bot test/live toggle is stored per owner. SEND_MODE only supplies the default before that owner has saved settings. After using the toggle, use /settings to check the actual mode. Changing a toggle cancels an unfinished draft.

## First test (one Gmail works)

1. Open the bot privately and send /start.
2. Open /settings and leave Test mode ON. Leave 10× test OFF initially.
3. Send /verify_test. One verification-only email is sent from your primary Gmail to TEST_RECIPIENT_EMAIL. It contains no report or target identifier.
4. Open that inbox and reply to the bot with `/verify_code 123456`, replacing the digits with the emailed code. The code expires in 10 minutes; five wrong attempts invalidate it. A new verification email is rate-limited to one per minute.
5. Choose Telegram X → @username or an https://t.me/... account/message link. Choose WhatsApp X → international + phone number.
6. Choose a category and a specific note title. Test mode asks for no incident description or screenshot. It inserts clearly labelled sample placeholders; the subject and body state that it is a transport/template test and not an allegation.
7. Review the destination, sender list, subject and body, then Confirm and send.
8. Check your inbox and Gmail Sent. /status shows each sender's outcome.

Test messages cannot route to telegram.org, whatsapp.com, whatsapp.net, their subdomains, or a configured live report address. Test destinations must be a single validated email and proven accessible via the inbox verification code. Changing the address requires verification again. A random nonexistent address will not work. Do not forward transport-test messages to platform abuse teams.

## Add senders later

No need to add nine accounts now. With the same Google OAuth project, add each Gmail to your OAuth test users, authorize gmail.send in OAuth Playground using your own existing client ID/secret, and obtain each account's own refresh token.

Set GMAIL_EXTRA_ACCOUNTS_JSON in Render to a single JSON array, for example:

```json
[{"email":"second@gmail.com","refresh_token":"SECOND_ACCOUNT_REFRESH_TOKEN"},{"email":"third@gmail.com","refresh_token":"THIRD_ACCOUNT_REFRESH_TOKEN"}]
```

The primary account remains in GMAIL_SENDER/GMAIL_REFRESH_TOKEN. Additional accounts use the shared GMAIL_CLIENT_ID/GMAIL_CLIENT_SECRET unless their JSON object supplies its own client_id/client_secret. Addresses must be distinct. Do not include the primary again in the array. No secrets belong in GitHub or chat.

Turn **10× test ON** under /settings when you want a multi-sender transport test. With one configured account it sends one email; with three it sends three; with ten it sends ten. It shows the real count before confirmation and submits concurrently only to the verified test inbox. A failed account produces its own status and does not stop other test senders.

Google OAuth apps in Testing normally receive Gmail refresh tokens that expire after seven days. Review Google's publishing/verification requirements for continuing use and renew authorization when needed. A production setting is not automatic Google verification. Tokens may also expire or be revoked for other reasons.

## Live reports

Turn Test mode OFF in /settings. Live mode always uses exactly one primary Gmail, regardless of the 10× preference. There is no automatic duplicate or mass-report sending and no automatic sender failover.

The bot asks for the actual fields in the selected template (dates, what happened, your knowledge of the incident, and evidence/references). It never manufactures factual claims from the title. Where a detail is genuinely unknown, state that explicitly. Provide relevant evidence/reference context; a screenshot is not required if meaningful references are supplied. Screenshots/PDFs are optional attachments: four files, 8 MB total, JPEG/PNG/PDF only. The bot returns attachments in the preview so you can check what is being sent.

The preview shows the exact recipient and content before confirmation. Emails use the sender's authorized Gmail address and Reply-To. The bot does not impersonate an official reporting authority, promise account bans, or verify wrongdoing.

Telegram officially publishes an abuse-report email route; current WhatsApp handling of the configured abuse mailbox remains unverified and general WhatsApp support says direct support email is discontinued. Delivery cannot establish whether moderation staff reviewed or acted on a submission.

## Case status, duplicates and privacy

- submitted = Gmail API accepted the send request, not confirmed inbox delivery, recipient review, or account action. Check Gmail Sent, replies and bounce notices yourself. This bot has send-only OAuth and cannot read your mail.
- rejected = Gmail explicitly rejected the send request.
- auth_failed = authorization failed before message submission.
- unknown = a timeout, unexpected response or restart left the send outcome uncertain. No automatic retries.
- Confirmation is atomically claimed, so double-tapping does not resend.
- Live target hashes block a second local live submission for the same platform/account or link, including after an uncertain send. Use genuinely distinct incident/content links as appropriate; do not use aliases or alternate spellings to bypass duplicate protection. Review failures manually; no reset/resend command is supplied.
- /delete removes the latest local case details but retains minimal send ledgers and target hashes. It cannot recall emails or erase Telegram copies. Cancelled and historical cases remain until managed. SQLite deletion is not guaranteed forensic erasure.
- Cases, evidence and settings stay in DATA_DIR; Gmail credentials are read from Render environment variables, not stored in SQLite. Do not commit real evidence, tokens or database files.
- This project uses a new kana-notes.db and does not migrate the old Sentinel database. Old files are not modified. Previous send history is not automatically known to the new bot.

Do not upload exploitative child imagery, intimate images shared without consent, malicious executables or stolen credentials/data. Use non-explicit context and appropriate platform/dedicated reporting channels instead. Immediate threats should not wait for an email report.

## Original notes

assets/original-report-notes.zip is the exact earlier ZIP. assets/report-pack/ contains its unmodified files. Tests compare every archived file byte-for-byte. The runtime reads report_notes.json; individual .txt edits are review copies and do not automatically change that JSON.

Only the Telegram rendering changes the platform greeting and account label; the source pack remains unchanged. Test rendering adds a test-only banner and labelled placeholder values. Live rendering fills the same templates with supplied facts. Some categories describe harmful behavior rather than established crimes; titles do not constitute legal findings.

## Commands

/start or /new — platform menu
/settings — test/live and multi-sender test toggles
/verify_test — verification email to configured test inbox
/verify_code CODE — verify control
/preview — current case and attachment preview
/status — latest case and per-sender outcomes
/clearfiles — remove draft attachments during evidence/preview
/cancel — cancel unfinished draft
/delete — remove latest local case details
/help — command help

## Local checks

Python 3.12. Install requirements, copy .env.example to .env and fill your own secrets. Run `python -m unittest discover -s tests -v`, then `python app.py`. Stop the Render copy first if using the same Telegram token.

Official protocol references:
https://developers.google.com/workspace/gmail/api/guides/sending
https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send
https://developers.google.com/identity/protocols/oauth2
https://core.telegram.org/bots/api
