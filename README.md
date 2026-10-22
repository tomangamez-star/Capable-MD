# MONTAi Sentinel — Gmail patch

Replace the matching files in your existing repository and add gmail_mail.py. Keep requirements.txt unchanged. Render build: `pip install -r requirements.txt`; start: `python app.py`; health: `/healthz`. Only one instance per bot token.

## Render variables

```
TELEGRAM_BOT_TOKEN=your_existing_botfather_token
ADMIN_IDS=your_numeric_telegram_id
GMAIL_CLIENT_ID=your_oauth_client_id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your_oauth_client_secret
GMAIL_REFRESH_TOKEN=your_oauth_refresh_token
GMAIL_SENDER=youraccount@gmail.com
TEST_EMAIL=youraccount@gmail.com
SEND_MODE=test
DATA_DIR=./data
```

Use DATA_DIR=/var/data instead if your persistent disk is mounted there. Keep your existing Render service; do not create a second one for this patch. The included Blueprint retains the original paid Starter service with persistent disk. Without persistent storage, redeploys lose cases and deduplication records.

Remove RESEND_API_KEY and MAIL_FROM. No domain, Resend account, Gmail password or App Password is required. A one-time Google OAuth setup is required instead.

## Google authorization (can be done in a phone browser)

1. Open https://console.cloud.google.com/ and create/select a project. Under APIs & Services → Library enable **Gmail API**.
2. Configure **Google Auth Platform** (or OAuth consent screen). Use app name MONTAi Sentinel, your contact emails, and External audience for a personal Gmail account. Add your Gmail as a test user if the app is in Testing.
3. Add only this data-access scope: `https://www.googleapis.com/auth/gmail.send`. This lets the bot send, not read your inbox.
4. Create an OAuth client of type **Web application**. Add this exact authorized redirect URI: `https://developers.google.com/oauthplayground`.
5. Put that client's ID and secret into Render as GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET.
6. Open https://developers.google.com/oauthplayground/ . In settings (gear), enable **Use your own OAuth credentials** and enter the same client ID and secret. Use offline access if shown.
7. In Step 1 enter `https://www.googleapis.com/auth/gmail.send`, choose **Authorize APIs**, and authorize the Gmail account you want to send from. Review the app identity and permissions. If Google blocks access, check your project/test-user setup; do not use someone else's OAuth credentials.
8. In Step 2 choose **Exchange authorization code for tokens**. Copy the **refresh token**, not the access token, into Render as GMAIL_REFRESH_TOKEN. Set GMAIL_SENDER to that authorized Gmail address. Never share these secrets in chat or commit them to GitHub.
9. Redeploy in test mode. Prepare and confirm a small case; verify the email and attachments in your own inbox and Gmail Sent. Then change SEND_MODE to live when ready.

**Important OAuth limitation:** external apps in Testing normally get Gmail refresh tokens that expire after seven days. For continued personal use, review Google's publishing and verification requirements and move the app to In production where appropriate, then authorize again. Changing publishing status does not automatically verify an app or override account restrictions. Otherwise repeat authorization when the token expires. Tokens may also be revoked for other reasons.

## Sending and status

Test mode sends only to TEST_EMAIL. Live mode sends one email addressed to both support@whatsapp.com and abuse@whatsapp.com. Current handling of these mailboxes remains unverified; WhatsApp's general support guidance says direct support email is discontinued.

A successful Gmail API response means Gmail accepted the send request, not confirmed recipient delivery, WhatsApp review or account action. Replies go to the supplied contact email. The bot has send-only permission and cannot read replies/bounces. Use Gmail Sent and your inbox to inspect them.

Gmail has no Resend-style send idempotency key. Atomic local claims prevent duplicate confirmation taps. A timeout or restart mid-send creates an unknown outcome and blocks automatic resend. Check Gmail Sent first before deleting that local case and starting another. A stable Message-ID helps manual reconciliation, but does not guarantee server deduplication. Gmail's normal sending limits apply.

## Bot workflow

/start → /new → international number → your contact email → category buttons or 1,2,4 → factual incident details → genuine screenshots/PDFs → /preview → Confirm and send.

Four attachments / 8 MB total. Abuse reports require evidence; own-account requests require /ownaccount. No automatic invented accusations. Named private files under evidence/ can be selected with /attach filename. Do not commit private evidence. Do not upload illegal imagery.

Commands: /start, /help, /new, /preview, /status, /evidence, /attach filename, /clearfiles, /ownaccount, /cancel, /delete.

SQLite stores drafts, evidence and offsets. /cancel retains the cancelled draft; /delete removes the latest local case logically, not emails or Telegram copies. Historical cases remain until managed; logical deletion is not forensic erasure.

## Validation

Run `python -m unittest discover -s tests -v`. No real report or email was sent during development; Gmail transport is mocked in tests.

Official references:
- https://developers.google.com/workspace/gmail/api/guides/sending
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send
- https://developers.google.com/identity/protocols/oauth2
