# KANA NOTES web control room

Upload the files in this ZIP into the existing Capable-MD repository root,
preserving the panel/ folder. Replace app.py and public_access.py. No dependency
changes are needed. This is a patch, not a complete standalone bot.

Based on these repository file versions:

- app.py: 90d9a6407ea23fe8b135c08fe8bede2d1f116395
- public_access.py: 16da7546535cfd2a6eca5a6941324f52faec15a6

## Render

Keep the current build and start commands and /healthz health path.
Add these variables:

| Name | Value |
| --- | --- |
| ADMIN_PANEL_PASSWORD | A unique password of at least 16 characters. Do not reuse a Gmail password. |
| NOTIFICATION_EMAIL | The shared inbox you control. Existing TEST_RECIPIENT_EMAIL is also accepted if NOTIFICATION_EMAIL is absent. |

Keep the existing TELEGRAM_BOT_TOKEN, ADMIN_IDS and Gmail credentials.
GMAIL_SENDER must identify the working primary Gmail account. The new notification
and feedback flow uses only that sender. Extra Gmail accounts and report
destinations are not edited by this panel.

The dashboard is hosted by the same Render web service at:
https://YOUR-SERVICE.onrender.com/admin

Use HTTPS. Secure login cookies intentionally do not work on an ordinary remote
HTTP connection. An unset or short panel password disables login without stopping
the Telegram bot or deployment. Sessions expire after eight hours or a restart.

## First use

1. Deploy the patch and open /admin on your service URL.
2. Sign in with ADMIN_PANEL_PASSWORD.
3. Open Inbox & access. Request a confirmation code, check the configured inbox,
   and enter the code. This proves you can access the destination. Changing the
   destination requires confirmation again.
4. Edit the welcome, labels and flow messages. Save draft keeps changes private.
   Publish live makes new bot messages read the new version immediately.
5. Add your own notification text in the 30 notification slots. An empty entry
   cannot be enabled. These entries start empty and disabled; they do not copy
   accusations from the reporting pack.
6. Publish Free usage ON to give verified public users the notification path.
   OFF gives public users the feedback path. Both require a preview and explicit
   Send confirmation. Owners can use notifications in either mode. /feedback
   explicitly starts feedback. Owner command /freeusage on or /freeusage off
   publishes a mode change; it requires an enabled entry when turning on.

The membership requirements remain in effect for public users and owners.
Join buttons are red; Verify is green. The new main menu and confirmation use
native rich button rows and quote-style panels, falling back to ordinary inline
keyboards if rich messages are unsupported. The phone preview is approximate:
Telegram controls exact fonts and styling. Processing uses a streamed draft,
with typing as a compatibility fallback.

## What is editable

The dashboard edits the heading, welcome, Telegram/WhatsApp labels, Send and
Cancel labels, secondary Reports label, feedback/reference prompts, processing
text, completion text, public mode and the 30 notification titles/message bodies.
All text is treated as plain content and escaped before rendering; it is not
executed as HTML, JavaScript or Telegram commands. No arbitrary route/action
editor is included. Success wording never replaces the actual Gmail status.

The first menu becomes notification/feedback-first. Reports remain available as
a secondary button and /report. The original reporting menu and report sending
methods, core.py, gmail_mail.py and assets/report-pack are unchanged. The existing
30 reporting templates are retained separately, and cannot be edited in this
dashboard. This patch does not add public random report sending or multi-sender
notifications. Editing notification content does not alter a report template.

## Live edits, history and backups

Every new menu reads the current published content directly from the database.
No redeployment is needed for a content change. Existing Telegram messages retain
their old text. A request already previewed retains its selected text; inbox
changes invalidate an old send preview rather than silently rerouting it.

Publishing creates an immutable version. Version history lists the most recent
20 versions. Restore publishes the selected snapshot as a new version. Export
downloads the current editor content and published version without credentials.
Import loads an exported draft into the editor; it does not publish automatically.
Concurrent edits from multiple tabs are rejected rather than silently overwriting
each other. Reload to pick up the latest revision before editing again.

The database is the existing kana-notes.db under DATA_DIR. On Render ephemeral
storage, edits and history can disappear on redeploy/restart. For persistent
edits, mount a persistent Render disk (for example /var/data) and set DATA_DIR to
that mount, or export and later import a backup. This patch does not migrate
existing database files to another directory. If changing DATA_DIR, preserve the
existing database yourself first.

## Delivery behavior

Feedback and notifications go only to the configured, confirmed inbox, through
the primary sender. They do not use the report recipient settings. A submission
is claimed once before contacting Gmail, so repeated Send presses cannot resend
it. Unknown outcomes are not automatically retried, including after a restart.
The new flow allows one submitted/uncertain request per user per minute and 20
per hour overall. /notification_status shows the latest request status.
Gmail acceptance is not proof of inbox delivery. Check Sent and bounce messages.

## Validation

Eighteen offline tests passed, covering draft/publish isolation, persistence,
concurrency conflicts, schema validation, recovery after interrupted sends,
confirmation, random selection from enabled notification entries, one-sender
delivery, duplicate prevention, user isolation, membership checks, preserved
legacy report routing, destination verification, HTML escaping, authenticated
web access, CSRF protection, version rollback, logout and login throttling.
Python compilation and JavaScript syntax checks passed. Live Gmail delivery,
Telegram rich rendering and the production deployment were not tested with real
credentials. Browser visual testing was unavailable because the local Chromium
executable is not installed.
