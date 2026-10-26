"""KANA NOTES Telegram assistant and Render health server.

The only report this bot can produce is one of the 30 notes shipped in
``assets/report-pack``. There is no manual composition path: no free-text
incident fields, no image or document upload, no attachment transport and no
custom mode. The operator picks a note, sees the exact message, and confirms.
"""
import asyncio
import logging
import os
import time
import uuid

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from core import Config, Notes, Store, GROUPS, target
from gmail_mail import Gmail, AuthError
from public_access import PublicAccess
from content_panel import Portal


def button(text, data):
    assert len(data.encode()) <= 64
    return {'text': text, 'callback_data': data}


class Bot:
    def __init__(self, config=None, store=None):
        self.cfg = config or Config()
        self.notes = Notes()
        self.store = store or Store(self.cfg.data_dir / 'kana-notes.db')
        self.last_poll = 0
        self.public_access = PublicAccess(self)
        self.portal = Portal(self)

    async def tg(self, method, **body):
        async with self.http.post(f'https://api.telegram.org/bot{self.cfg.token}/{method}', json=body) as r:
            result = await r.json()
            if not result.get('ok'):
                raise RuntimeError('Telegram request failed: ' + method)
            return result['result']

    async def say(self, uid, text, rows=None):
        for i in range(0, len(text), 3800):
            kw = {'reply_markup': {'inline_keyboard': rows}} if rows and i+3800 >= len(text) else {}
            await self.tg('sendMessage', chat_id=uid, text=text[i:i+3800], **kw)

    async def home(self, uid):
        await self.portal.home(uid)

    async def report_home(self, uid):
        await self.say(uid, 'KANA NOTES\nChoose a platform.\n\n'
                       f'Senders: {len(self.cfg.senders())}/10 configured\n'
                       'Every report is one of the 30 shipped notes, sent from all senders.\n\n'
                       '/settings · /status · /cancel · /help', [
            [button('Telegram X', 'platform:telegram'), button('WhatsApp X', 'platform:whatsapp')],
            [button('Settings', 'settings')]])

    def cancel_draft(self, uid):
        phase, d = self.store.current(uid)
        if d and phase in ('target', 'group', 'notes', 'review'):
            self.store.save(uid, 'cancelled', d)

    async def settings(self, uid):
        senders = self.cfg.senders()
        lines = [f'Senders: {len(senders)}/10',
                 *(s['email'] for s in senders),
                 '',
                 'Report destinations:',
                 *(f"  {k.title()}: {v}" for k, v in self.cfg.reporting.items())]
        if self.cfg.warnings:
            lines += ['', 'Warnings:'] + ['  ' + w for w in self.cfg.warnings]
        lines += ['', self.cfg.diagnostics()]
        await self.say(uid, '\n'.join(lines), [
            [button('Home', 'home')]])

    async def groups(self, uid, d):
        self.store.save(uid, 'group', d)
        await self.say(uid, 'Choose a category:', [[button(name, f"group:{d['id']}:{i}")] for i, name in enumerate(GROUPS)])

    def route(self, uid, d):
        """Destination plus every configured sender, for every report type."""
        destination = self.cfg.reporting.get(d['platform'])
        if not destination:
            raise ValueError('No report address available for ' + d['platform'] + '.')
        accounts = self.cfg.senders()
        if not accounts:
            raise ValueError('No Gmail senders configured yet.')
        return destination, accounts

    async def preview(self, uid, d):
        destination, accounts = self.route(uid, d)
        d['subject'], d['body'] = self.notes.compose(d, accounts[0]['email'])
        d['recipient'] = destination
        d['senders'] = [a['email'] for a in accounts]
        self.store.save(uid, 'review', d)
        await self.say(uid, f"REPORT PREVIEW\nNote: {self.notes.note(d['note'])['display_name']}\n"
                       f'To: {destination}\n'
                       f"From: {', '.join(d['senders'])}\nEmails on confirmation: {len(accounts)}\n\n"
                       f"Subject: {d['subject']}\n\n{d['body']}")
        await self.say(uid, 'Confirm to send exactly this text. Confirm the details are accurate to your knowledge. Gmail acceptance is not proof of delivery or platform action.', [
            [button('Confirm and send', f"send:{d['id']}")],
            [button('Cancel', f"cancel:{d['id']}")]])

    async def submit(self, uid, d):
        destination, accounts = self.route(uid, d)
        if destination != d.get('recipient') or [a['email'] for a in accounts] != d.get('senders'):
            raise ValueError('Configuration changed. Pick the note again before sending.')
        if not self.store.claim(uid, d['id']):
            await self.say(uid, 'This case is no longer sendable. Use /status.')
            return
        for a in accounts:
            self.store.attempt(d['id'], a['email'], 'sending')
        async def one(a):
            try:
                status, mid = await self.mail.send(a, destination, d['subject'], d['body'], d['id'])
            except AuthError as exc:
                # Keep Google's own reason: "expired or revoked", "invalid_client"
                # and "unauthorized_client" need three different repairs.
                status, mid = 'auth_failed', getattr(exc, 'reason', '') or str(exc)
            except Exception:
                status, mid = 'unknown', ''
            self.store.attempt(d['id'], a['email'], status, mid)
        await asyncio.gather(*(one(a) for a in accounts))
        rows = self.store.attempts(d['id'])
        phase = 'unknown' if any(r[1] == 'unknown' for r in rows) else 'completed'
        self.store.save(uid, phase, d)
        await self.status(uid)

    async def status(self, uid):
        phase, d = self.store.current(uid)
        if not d:
            await self.say(uid, 'No case yet. Use /start.')
            return
        results = self.store.attempts(d['id'])
        tally = {}
        for _a, _s, _m in results:
            tally[_s] = tally.get(_s, 0) + 1
        summary = 'Results: ' + ', '.join(f'{v} {k}' for k, v in sorted(tally.items()))
        if tally.get('auth_failed'):
            summary += ('\n' + str(tally['auth_failed']) + ' sender(s) could not authorize. The reason after '
                        "each address is Google's own error: expired_or_revoked means re-run the OAuth "
                        'flow for that address, invalid_client means its client_id/client_secret is '
                        'wrong, unauthorized_client means the OAuth app does not cover it. /settings '
                        'shows whether the tokens are distinct.')
        await self.say(uid, f"Case {d['id']} · {d['platform'].title()}\nState: {phase}\n"
                       f"To: {d.get('recipient', 'not previewed')}\n" +
                       '\n'.join(f'{a}: {s}' + (f' · {mid}' if mid else '') for a,s,mid in results) +
                       '\n' + summary +
                       '\n\nsubmitted = Gmail accepted the send request, not verified delivery or review. Check Gmail Sent, replies and bounce notices yourself. Uncertain sends are never automatically retried.')

    async def handle(self, update):
        if await self.public_access.handle(update):
            return
        cb = update.get('callback_query')
        msg = update.get('message') or (cb or {}).get('message')
        actor = (cb or update.get('message') or {}).get('from', {})
        uid = actor.get('id')
        if not msg or uid not in self.cfg.admins or msg['chat']['type'] != 'private':
            if cb:
                await self.tg('answerCallbackQuery', callback_query_id=cb['id'], text='Private owner-only bot.')
            return
        phase, d = self.store.current(uid)
        if cb:
            await self.tg('answerCallbackQuery', callback_query_id=cb['id'])
            data = cb.get('data', '').split(':')
            action = data[0]
            if action == 'home':
                await self.home(uid)
            elif action == 'settings':
                await self.settings(uid)
            elif action == 'platform' and len(data) == 2 and data[1] in ('telegram','whatsapp'):
                if phase in ('sending','unknown'):
                    raise ValueError('Check the previous uncertain submission first. Use /status; no resend is available.')
                self.cancel_draft(uid)
                d = {'id': uuid.uuid4().hex[:12], 'platform': data[1]}
                self.store.save(uid, 'target', d)
                await self.say(uid, 'Enter an @username or https://t.me/... message link.' if data[1] == 'telegram' else 'Enter the WhatsApp number in international format, starting with +.')
            elif len(data) >= 2 and d and data[1] == d['id']:
                if action == 'group' and phase in ('group','notes') and len(data) == 3:
                    i = int(data[2])
                    if not 0 <= i < len(GROUPS):
                        return
                    name = list(GROUPS)[i]
                    self.store.save(uid, 'notes', d)
                    await self.say(uid, name + ' — select a note:', [[button(self.notes.note(n)['display_name'], f"note:{d['id']}:{n}")] for n in GROUPS[name]])
                elif action == 'note' and phase == 'notes' and len(data) == 3:
                    number = int(data[2])
                    if not self.notes.note(number):
                        return
                    d['note'] = number
                    await self.preview(uid, d)
                elif action == 'send' and phase == 'review':
                    await self.submit(uid, d)
                elif action == 'cancel' and phase == 'review':
                    self.store.save(uid, 'cancelled', d)
                    await self.say(uid, 'Cancelled; nothing sent.')
            return
        text = msg.get('text', '').strip()
        cmd = text.split(' ')[0].split('@')[0].lower()
        if cmd in ('/start','/new'):
            await self.home(uid)
        elif cmd == '/settings':
            await self.settings(uid)
        elif cmd == '/status':
            await self.status(uid)
        elif cmd == '/cancel':
            self.cancel_draft(uid)
            await self.say(uid, 'Draft cancelled. Submitted emails cannot be recalled. Use /start.')
        elif cmd == '/delete':
            if phase == 'sending':
                raise ValueError('Submission is running. Check /status first.')
            if d:
                self.store.db.execute('DELETE FROM cases WHERE id=? AND owner=?', (d['id'],uid))
                self.store.db.commit()
                # Retain the send ledger so a crash cannot silently repeat a submission.
            await self.say(uid, 'Latest local case details removed. Send records remain; emails and Telegram copies are not deleted.')
        elif cmd == '/help':
            await self.say(uid, 'KANA NOTES\n/start — Telegram X / WhatsApp X\n/settings — senders and report addresses\n/status — latest case\n/cancel — cancel draft\n/delete — delete latest local case details\n\n'
                           'Reports come only from the shipped 30-note pack: choose a note, review the exact text, confirm. There is no manual entry, no attachment upload and no custom mode. Every report is emailed from all configured Gmail senders to the platform abuse address. No automatic account bans or delivery claims.')
        elif phase == 'target':
            d['target'] = target(d['platform'], text)
            await self.groups(uid, d)
        elif phase == 'review':
            await self.say(uid, 'Use the Confirm or Cancel button, or /cancel. Reports are composed only from the 30 shipped notes; text and files sent here are not used.')
        else:
            await self.say(uid, 'Use /start to choose a platform.')

    async def run(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as self.http:
            self.mail = Gmail(self.http, self.cfg)
            await self.tg('deleteWebhook', drop_pending_updates=False)
            offset = self.store.get('offset', 0)
            while True:
                try:
                    updates = await self.tg('getUpdates', offset=offset, timeout=30, allowed_updates=['message','callback_query'])
                    self.last_poll = time.time()
                    for update in updates:
                        try:
                            await self.handle(update)
                        except (ValueError, AuthError) as exc:
                            actor = update.get('callback_query') or update.get('message') or {}
                            uid = actor.get('from', {}).get('id')
                            if uid in self.cfg.admins:
                                await self.say(uid, str(exc))
                        except Exception:
                            logging.error('Update processing failed; sensitive payload omitted.')
                        offset = update['update_id'] + 1
                        self.store.put('offset', offset)
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError):
                    logging.warning('Polling unavailable; retrying in five seconds.')
                    await asyncio.sleep(5)


async def main():
    load_dotenv()
    bot = Bot()
    app = web.Application(client_max_size=256 * 1024)
    bot.portal.register(app)
    async def health(_):
        ready = time.time() - bot.last_poll < 180
        return web.json_response({'service':'KANA NOTES','ready':ready}, status=200 if ready else 503)
    app.router.add_get('/healthz', health)
    app.router.add_get('/', health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT','10000'))).start()
    try:
        await bot.run()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    asyncio.run(main())


