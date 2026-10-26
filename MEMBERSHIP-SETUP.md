# KANA NOTES membership and public drafts patch

Copy app.py and public_access.py into the repository root, replacing app.py.
Redeploy with the existing start command and environment variables. No new
environment variables or dependencies are required.

This patch is based on Capable-MD app.py blob b06631b4b94c3aab76cd48ebebdb1fae68dbf0c4.
Only the PublicAccess import, initialization, and early handle() hook were added
to that file. core.py, gmail_mail.py, the notes, and Render settings are unchanged.

## Telegram setup

Add the same KANA NOTES bot as an administrator in ALL FOUR required chats:

| Button | Membership lookup | Join link |
| --- | --- | --- |
| 🥱 Bot Heavon chat | -1003938744205 | https://t.me/+1ZJWPoUoe11iODZk |
| The JTF Casino | @thejtf | https://t.me/thejtf |
| Kana Arima | @kana_montai | https://t.me/kana_montai |
| MONTAI ZONE | @bot_heavon | https://t.me/bot_heavon |

The numeric ID is the one supplied by the owner. It must refer to the same chat
as the first invite link. If it is a linked discussion group rather than the
channel itself, supply the channel's actual ID instead. Telegram cannot derive
a private chat ID from an invite URL.

Owners listed in ADMIN_IDS must open /start in the bot so it can notify them.
They also need membership in all four chats. A pending join request does not
count as membership until a chat administrator approves it.

## Behavior

Everyone can open /start in a private conversation. The bot shows four join
buttons and Verify membership if any membership is missing or cannot be checked.
It checks all four chats again on each private command and button click; leaving
a chat therefore locks access on the next interaction. There is no stored
permanent verification flag or bypass through old inline buttons.

After verification, public users choose Telegram X or WhatsApp X, enter the
account identifier, pick a topic from the existing 30 titles, and describe what
actually happened. They preview and submit a draft to the owner. These are
user-provided, unverified descriptions; they are not automatically emailed.
The public flow cannot access Gmail settings, credentials, owner case data,
or owner send callbacks. It never calls Gmail or imports a draft into a sendable
owner case.

Owners keep their existing interface. Public submissions are sent to ADMIN_IDS
in Telegram and saved separately in the existing SQLite database. Owners can
retrieve a submitted draft with /draft CASE_ID if a notification fails.
Public /status shows the user's own most recent draft. /cancel cancels an
unsubmitted draft. Submitted drafts remain available for owner review.
Existing DATA_DIR persistence requirements still apply.

## Verification

Eight offline access-control tests passed: membership status handling; missing
membership blocking both public and owner buttons; API errors denying access;
verified owner handoff; membership recheck after leaving; public denial of owner
controls; draft submission without mail and duplicate submission prevention;
and cross-user draft isolation. Python compilation passed.

Live membership checks and notification delivery require your deployed bot and
administrator setup and were not performed with real credentials.
