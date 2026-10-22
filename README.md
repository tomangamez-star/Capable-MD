# MONTAi Sentinel

Telegram social safety and reporting assistant, prepared for Render. Python 3.12.

This bot prepares factual, individually reviewed incident emails. It does not ban accounts or submit native WhatsApp chat reports. Both requested email destinations (`support@whatsapp.com`, `abuse@whatsapp.com`) are included together in one live email. Their current processing is not verified; WhatsApp's general support guidance says direct support email is discontinued. Sending or delivery does not establish review or enforcement.

## Deploy on Render

1. Create a Telegram bot with the official @BotFather and obtain its token. Obtain your own numeric Telegram user ID.
2. Create a Resend account, verify a sending domain you control, and create an API key. A normal Gmail address is not a verified sending domain. Resend's test sender generally cannot send to arbitrary external recipients; use a verified domain for live mode.
3. Upload the contents of this folder to your own GitHub repository. Do not upload `.env`, the `data` directory, or private evidence.
4. In Render choose **New → Blueprint**, connect that repository, and apply `render.yaml`. The supplied Blueprint uses a **paid Starter web service and persistent disk**; review Render's displayed price before creating it. No deployment has been performed for you.
5. Enter the secrets below. Start in `test` mode, run a complete case and confirm you receive the evidence in your own inbox.
6. When ready, change `SEND_MODE` to `live` in Render and redeploy. The preview explicitly shows the destinations before each confirmation.

| Variable | Value |
|---|---|
| TELEGRAM_BOT_TOKEN | Token from @BotFather |
| ADMIN_IDS | Your numeric Telegram ID; comma-separated IDs if necessary |
| RESEND_API_KEY | API key from Resend |
| MAIL_FROM | `MONTAi Sentinel <reports@your-verified-domain.com>` |
| TEST_EMAIL | Your own inbox for test mode |
| SEND_MODE | `test` initially, `live` to address both WhatsApp mailboxes |
| DATA_DIR | `/var/data` on the Blueprint's persistent disk |

Manual Render setup: Python web service; build `pip install -r requirements.txt`; start `python app.py`; health path `/healthz`. Attach a disk at `/var/data` and set DATA_DIR accordingly. Only run one instance per Telegram token. Do not use a Gunicorn multiworker setup: this service owns both polling and its HTTP health server.

A free web service can run with ephemeral DATA_DIR, but sleeping interrupts polling and redeploys lose cases and deduplication history. The provided Blueprint deliberately includes a persistent disk. This project does not require Supabase.

## Use

Private chat only; unlisted users cannot create cases or access evidence.

1. `/start`, then `/new`.
2. Enter an international number starting with `+`, then your own contact email.
3. Select category buttons and Continue, or send `1,2,4`.
4. Describe the actual incident, its dates, your connection to the account, and evidence for each selected category. Labels are not automatically expanded into accusations. Modified clients alone are not proof of criminal activity; describe actual abuse under automation/other where relevant.
5. Upload up to four JPEG/PNG screenshots or PDFs (8 MB total), or `/attach filename` for a provisioned local evidence file. Telegram photos may be compressed; send screenshots as documents to preserve their bytes. Never upload illegal imagery; report such material through an appropriate dedicated channel.
6. `/preview` shows the exact complaint and returns every attachment for review. Then use **Confirm and send**. Editing requires a fresh preview. Own-account requests require `/ownaccount`; they do not require a screenshot. Use this category only for accounts you own.
7. `/status` shows the submission record and queries the provider's latest event when available. Email responses go to the supplied Reply-To address. This version does not monitor your inbox or infer WhatsApp action from delivery.

Other commands: `/evidence`, `/clearfiles`, `/cancel`, `/delete`, `/help`.

## Reliability and privacy

- Test mode is the default; no real recipient submissions occur until live mode is explicitly configured and a case is confirmed.
- Cases, evidence bytes and poll offsets are stored in SQLite on disk. Nothing is publicly served except a minimal health response.
- Attachments are snapshotted and hashed. Telegram evidence, your contact email, case text and attachments are shared with the email provider and live recipients upon confirmation.
- Sending is claimed atomically and uses a provider idempotency key. Duplicate taps cannot resend the same case. An uncertain timeout or a restart mid-send marks the case `unknown` and prevents automatic retries. Resolve it in Resend's dashboard before deleting that local record and creating a new case.
- `/delete` removes the latest local case, not emails or Telegram copies. `/cancel` preserves the cancelled draft locally; use `/delete` to erase it. Older cases remain on disk; manage retention and backups for the sensitive evidence you choose to retain. SQLite deletion is logical deletion, not guaranteed forensic erasure.
- No scraping, fake evidence, multiple reporting identities, automatic repeated submissions, invented success rates, or automatic suspension claims.
- No report has been submitted during development.

## Local setup and checks

Copy `.env.example` to `.env` and fill in your own values. Install dependencies and run:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python app.py
```

Stop any other deployment using this same Telegram token before running locally.

References: https://faq.whatsapp.com/1336994774516288 · https://resend.com/docs/api-reference/emails/send-email · https://render.com/docs/web-services
