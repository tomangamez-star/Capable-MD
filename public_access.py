"""Membership gate and public draft inbox. Public users never invoke email sending."""
import asyncio
import json
import logging
import time
import uuid

from core import target

REQUIRED_CHATS = (
    ('🥱 Bot Heavon chat', -1003938744205, 'https://t.me/+1ZJWPoUoe11iODZk'),
    ('The JTF Casino', '@thejtf', 'https://t.me/thejtf'),
    ('Kana Arima', '@kana_montai', 'https://t.me/kana_montai'),
    ('MONTAI ZONE', '@bot_heavon', 'https://t.me/bot_heavon'),
)


def btn(text, data):
    return {'text': text, 'callback_data': data}


def joined(member):
    return member.get('status') in ('creator', 'administrator', 'member') or (
        member.get('status') == 'restricted' and member.get('is_member') is True)


class PublicAccess:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.store.db
        self.db.execute('CREATE TABLE IF NOT EXISTS public_drafts '
                        '(id TEXT PRIMARY KEY, owner INTEGER, phase TEXT, data TEXT, updated REAL)')
        self.db.commit()

    async def membership(self, uid):
        async def check(chat):
            try:
                result = await asyncio.wait_for(
                    self.bot.tg('getChatMember', chat_id=chat[1], user_id=uid), timeout=10)
                return 'joined' if joined(result) else 'missing'
            except Exception:
                # Never log request URLs or exception payloads containing credentials.
                logging.warning('Membership check unavailable for required chat %s.', chat[1])
                return 'unavailable'
        return await asyncio.gather(*(check(chat) for chat in REQUIRED_CHATS))

    async def gate(self, uid, states):
        display = self.bot.portal.content.live()
        rows = [[{'text': name, 'url': url, 'style': 'danger'}] for name, _, url in REQUIRED_CHATS]
        verify = btn(display['verify_label'], 'membership:verify')
        verify['style'] = 'success'
        rows.append([verify])
        lines = [display['heading'], display['join_prompt'], '']
        for chat, state in zip(REQUIRED_CHATS, states):
            label = {'joined': '✅ Joined', 'missing': '❌ Join required',
                     'unavailable': '⚠️ Could not verify'}[state]
            lines.append(f'{label} — {chat[0]}')
        if 'unavailable' in states:
            lines.append('\nThe owner must add this bot as an administrator in every listed chat. '
                         'If that is already done, try again shortly.')
        lines.append('\nA pending join request needs approval before verification can pass.')
        await self.bot.say(uid, '\n'.join(lines), rows)

    def current(self, uid):
        row = self.db.execute('SELECT phase,data FROM public_drafts WHERE owner=? '
                              'ORDER BY updated DESC LIMIT 1', (uid,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def save(self, uid, phase, draft):
        self.db.execute('INSERT INTO public_drafts VALUES (?,?,?,?,?) ON CONFLICT(id) '
                        'DO UPDATE SET phase=excluded.phase,data=excluded.data,updated=excluded.updated',
                        (draft['id'], uid, phase, json.dumps(draft), time.time()))
        self.db.commit()

    async def home(self, uid):
        await self.bot.portal.home(uid)

    async def report_home(self, uid):
        await self.bot.say(uid, 'KANA NOTES\nChoose a platform for your draft.\n\n'
                           'Drafts go to the bot owner for review. No email is sent by this public flow.\n'
                           '/start · /status · /cancel', [[btn('Telegram X', 'public:telegram'),
                                                          btn('WhatsApp X', 'public:whatsapp')]])

    async def handle(self, update):
        cb = update.get('callback_query')
        msg = update.get('message') or (cb or {}).get('message')
        actor = (cb or update.get('message') or {}).get('from', {})
        uid = actor.get('id')
        if not msg or not uid or msg['chat']['type'] != 'private':
            return True
        owner = uid in self.bot.cfg.admins
        data = (cb or {}).get('data', '')
        # Acknowledge once here; owner callbacks continuing to the old handler
        # are acknowledged there instead.
        portal_callback = data.startswith(('portal:', 'portalsend:', 'portalcancel:'))
        if cb and (not owner or data == 'membership:verify' or portal_callback):
            await self.bot.tg('answerCallbackQuery', callback_query_id=cb['id'])
        states = await self.membership(uid)
        if any(state != 'joined' for state in states):
            if cb and owner and data != 'membership:verify' and not portal_callback:
                await self.bot.tg('answerCallbackQuery', callback_query_id=cb['id'])
            await self.gate(uid, states)
            return True
        if data == 'membership:verify':
            await self.bot.say(uid, '✅ Membership verified in all four chats.')
            await (self.bot.home(uid) if owner else self.home(uid))
            return True
        text = msg.get('text', '').strip() if not cb else ''
        cmd = text.split(' ')[0].split('@')[0].lower()
        if await self.bot.portal.telegram(uid, text, data, owner):
            return True
        if owner:
            if cmd == '/draft':
                ident = text.partition(' ')[2].strip()
                row = self.db.execute('SELECT owner,phase,data FROM public_drafts WHERE id=? '
                                      'AND phase=?', (ident, 'submitted')).fetchone()
                if row:
                    d = json.loads(row[2])
                    await self.bot.say(uid, self.summary(d, row[0]))
                else:
                    await self.bot.say(uid, 'Submitted draft not found. Use /draft CASE_ID.')
                return True
            return False  # Existing owner interface and sending remain owner-only.
        phase, draft = self.current(uid)
        try:
            if cb:
                parts = data.split(':')
                if data in ('public:telegram', 'public:whatsapp'):
                    draft = {'id': uuid.uuid4().hex[:12], 'platform': parts[1]}
                    self.save(uid, 'target', draft)
                    await self.bot.say(uid, 'Enter an @username or https://t.me/... link.'
                                       if parts[1] == 'telegram' else 'Enter the phone number starting with +.')
                elif (len(parts) == 3 and draft and parts[1] == draft['id']
                      and parts[0] == 'publicnote' and phase == 'notes'):
                    note = self.bot.notes.note(int(parts[2]))
                    if not note:
                        return True
                    draft['topic'] = note['display_name']
                    self.save(uid, 'details', draft)
                    await self.bot.say(uid, 'Describe what actually happened, including any relevant '
                                       'dates or message links (20–2000 characters). '
                                       'This description will be shared with the bot owner for review.')
                elif (len(parts) == 2 and draft and parts[1] == draft['id']
                      and parts[0] == 'publicsubmit' and phase == 'review'):
                    self.save(uid, 'submitted', draft)
                    notified = False
                    for admin in sorted(self.bot.cfg.admins):
                        try:
                            await self.bot.say(admin, self.summary(draft, uid))
                            notified = True
                        except Exception:
                            logging.warning('Public draft saved; owner notification unavailable.')
                    await self.bot.say(uid, 'Draft ' + draft['id'] + ' saved for owner review. '
                                       + ('The owner was notified.' if notified else
                                          'The owner notification could not be delivered.')
                                       + ' No email has been sent.')
                else:
                    await self.bot.say(uid, 'That button is no longer available. Use /start or /status.')
            elif cmd in ('/start', '/new', '/help'):
                await self.home(uid)
            elif cmd == '/status':
                await self.bot.say(uid, f"Draft {draft['id']} · {phase}\nNo email sent."
                                   if draft else 'No draft yet. Use /start.')
            elif cmd == '/cancel':
                if draft and phase != 'submitted':
                    self.save(uid, 'cancelled', draft)
                await self.bot.say(uid, 'Active draft cancelled. Submitted drafts remain with the owner.')
            elif cmd.startswith('/'):
                await self.bot.say(uid, 'Use /start, /status or /cancel. Settings and email sending are owner-only.')
            elif phase == 'target':
                draft['target'] = target(draft['platform'], text)
                self.save(uid, 'notes', draft)
                rows = [[btn(n['display_name'], f"publicnote:{draft['id']}:{i}")]
                        for i, n in enumerate(self.bot.notes.notes, 1)]
                await self.bot.say(uid, 'Choose the topic that matches your concern:', rows)
            elif phase == 'details':
                if not 20 <= len(text) <= 2000:
                    raise ValueError('Please describe what happened in 20–2000 characters.')
                draft['details'] = text
                self.save(uid, 'review', draft)
                await self.bot.say(uid, self.summary(draft, uid) + '\n\nShare this draft with the owner?',
                                   [[btn('Submit for owner review', f"publicsubmit:{draft['id']}")]])
            else:
                await self.bot.say(uid, 'Use /start to prepare a draft.')
        except (ValueError, TypeError):
            await self.bot.say(uid, 'Check your input. Use a valid account identifier, select a listed topic, '
                               'and provide a description of 20–2000 characters.')
        return True

    @staticmethod
    def summary(draft, uid):
        return (f"PUBLIC DRAFT {draft['id']}\nSubmitted by Telegram user {uid}\n"
                f"Platform: {draft['platform']}\nAccount: {draft['target']}\n"
                f"Topic: {draft['topic']}\n\nUser-provided description (unverified):\n"
                f"{draft['details']}\n\nOwner review only; no email sent.")

