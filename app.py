"""KANA NOTES Telegram assistant and Render health server."""
import asyncio
import base64
import hmac
import logging
import os
import secrets
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from core import Config, Notes, Store, GROUPS, digest, target
from gmail_mail import Gmail, AuthError

MAX_BYTES = 8 * 1024 * 1024


def button(text, data):
    assert len(data.encode()) <= 64
    return {'text': text, 'callback_data': data}


class Bot:
    def __init__(self, config=None, store=None):
        self.cfg = config or Config()
        self.notes = Notes()
        self.store = store or Store(self.cfg.data_dir / 'kana-notes.db')
        self.last_poll = 0

    def prefs(self, uid):
        return self.store.prefs(uid, self.cfg.initial_test)

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
        p = self.prefs(uid)
        await self.say(uid, 'KANA NOTES\nChoose a platform.\n\nMode: ' + ('TEST — your verified inbox only' if p['test'] else 'LIVE — one primary sender') +
                       f"\nConfigured senders: {len(self.cfg.accounts)}/10\n10× test: {'ON' if p['multi'] else 'OFF'}\n\n/settings · /status · /verify_test · /cancel · /help", [
            [button('Telegram X', 'platform:telegram'), button('WhatsApp X', 'platform:whatsapp')],
            [button('Settings', 'settings')]])

    def cancel_draft(self, uid):
        phase, d = self.store.current(uid)
        if d and phase in ('target', 'group', 'notes', 'fields', 'evidence', 'review'):
            self.store.save(uid, 'cancelled', d)

    async def settings(self, uid):
        p = self.prefs(uid)
        await self.say(uid, f"Test mode: {'ON' if p['test'] else 'OFF'}\n10× test: {'ON' if p['multi'] else 'OFF'}\n"
                       f"Available senders: {len(self.cfg.accounts)}/10\n"
                       f"Test destination: {self.cfg.test_email or 'not configured'}\n"
                       'Ten-sender mode works only with a verified test inbox. Changing a toggle cancels an unfinished draft.', [
            [button('Toggle test / live', 'toggle:test')], [button('Toggle 10× test', 'toggle:multi')],
            [button('Verify test inbox', 'verify')], [button('Home', 'home')]])

    def test_verified(self, uid):
        self.cfg.validate_test()
        return self.store.get(f'verified:{uid}') == digest(self.cfg.test_email.lower())

    async def verify(self, uid):
        recipient = self.cfg.validate_test()
        a = self.cfg.primary()
        old = self.store.get(f'challenge:{uid}', {})
        if time.time() - old.get('created', 0) < 60:
            raise ValueError('Wait one minute before requesting another verification email.')
        code = f'{secrets.randbelow(1000000):06d}'
        record = {'hash': digest(code), 'created': time.time(), 'recipient': digest(recipient.lower()), 'tries': 0}
        self.store.put(f'challenge:{uid}', record)
        state, _ = await self.mail.send(a, recipient, '[KANA NOTES] Verify your test inbox',
                                        'Your KANA NOTES test inbox code: ' + code + '\nExpires in 10 minutes. Enter /verify_code followed by the code in your private bot chat.',
                                        'verify-' + uuid.uuid4().hex)
        if state == 'submitted':
            await self.say(uid, 'Verification email submitted. Open your test inbox, then send /verify_code followed by its six-digit code here. No report was sent.')
        else:
            await self.say(uid, 'Verification email is not confirmed sent. Check the inbox and Gmail Sent; request another after a minute if necessary.')

    async def verify_code(self, uid, code):
        self.cfg.validate_test()
        r = self.store.get(f'challenge:{uid}', {})
        if time.time() - r.get('created', 0) > 600 or r.get('tries', 5) >= 5 or r.get('recipient') != digest(self.cfg.test_email.lower()):
            raise ValueError('Verification expired or unavailable. Use /verify_test.')
        r['tries'] += 1
        self.store.put(f'challenge:{uid}', r)
        if not hmac.compare_digest(digest(code.strip()), r['hash']):
            raise ValueError('Incorrect verification code.')
        self.store.put(f'verified:{uid}', digest(self.cfg.test_email.lower()))
        self.store.put(f'challenge:{uid}', {})
        await self.say(uid, 'Test inbox verified. Use /start to prepare a test.')

    async def groups(self, uid, d):
        self.store.save(uid, 'group', d)
        await self.say(uid, 'Choose a category:', [[button(name, f"group:{d['id']}:{i}")] for i, name in enumerate(GROUPS)])

    async def field(self, uid, d):
        missing = [k for k in self.notes.fields(d['note']) if k not in d['answers']]
        if not missing:
            self.store.save(uid, 'evidence', d)
            await self.say(uid, 'Attach relevant JPEG/PNG screenshots or a PDF (four files / 8 MB total), then /preview. '
                           'Message links/references can support the case without a screenshot. Do not upload illegal or intimate imagery. /clearfiles removes draft attachments.')
            return
        d['field'] = missing[0]
        self.store.save(uid, 'fields', d)
        await self.say(uid, 'Add: ' + missing[0].replace('_', ' ') + '\nState what you actually know. If something is unknown, say so; do not guess. (Max 1500 characters.)')

    def route(self, uid, d):
        p = self.prefs(uid)
        if d['test'] != p['test'] or (d['test'] and d['multi'] != p['multi']):
            raise ValueError('Mode changed. Start a new case to see the current routing.')
        if d['test']:
            if not self.test_verified(uid):
                raise ValueError('Verify your controlled test inbox first with /verify_test.')
            destination = self.cfg.validate_test()
            accounts = self.cfg.accounts[:10] if d['multi'] else [self.cfg.primary()]
        else:
            destination = self.cfg.live[d['platform']]
            if not destination:
                raise ValueError('Configure ' + d['platform'].upper() + '_REPORT_EMAIL in Render first.')
            if self.cfg.test_email and destination.lower() == self.cfg.test_email.lower():
                raise ValueError('Live and test recipients must differ.')
            accounts = [self.cfg.primary()]
        if not accounts:
            raise ValueError('No Gmail senders configured yet.')
        return destination, accounts

    async def preview(self, uid, d):
        destination, accounts = self.route(uid, d)
        if not d['test']:
            reference = d['answers'].get('evidence_summary_or_explicitly_unavailable', '').strip()
            if not d['files'] and len(reference) < 20:
                raise ValueError('Provide relevant evidence or a meaningful evidence/reference description before submitting a live case.')
            if not self.store.get('live-used:' + digest(d['platform'] + ':' + d['target'].lower())):
                pass
            else:
                raise ValueError('A live submission for this account/content is already recorded. Check /status and your sent mail; duplicate submissions are blocked.')
        d['subject'], d['body'] = self.notes.render(d, accounts[0]['email'])
        d['recipient'] = destination
        d['senders'] = [a['email'] for a in accounts]
        self.store.save(uid, 'review', d)
        await self.say(uid, f"{'TEST ONLY' if d['test'] else 'LIVE REPORT'}\nTo: {destination}\n"
                       f"From: {', '.join(d['senders'])}\nEmails on confirmation: {len(accounts)}\nAttachments: {len(d['files'])}\n\n"
                       f"Subject: {d['subject']}\n\n{d['body']}")
        for f in d['files']:
            form = aiohttp.FormData()
            form.add_field('chat_id', str(uid))
            form.add_field('document', base64.b64decode(f['content']), filename=f['filename'], content_type=f['mime'])
            async with self.http.post(f'https://api.telegram.org/bot{self.cfg.token}/sendDocument', data=form) as r:
                result = await r.json()
                if not result.get('ok'):
                    self.store.save(uid, 'evidence', d)
                    raise ValueError('An attachment preview failed. Preview again before confirming.')
        await self.say(uid, 'Confirm this exact preview. For live submissions, confirm the details are accurate to your knowledge and you are authorized to share the evidence. Gmail acceptance is not proof of delivery or platform action.', [
            [button('Confirm and send', f"send:{d['id']}")],
            [button('Cancel', f"cancel:{d['id']}")]])

    async def submit(self, uid, d):
        destination, accounts = self.route(uid, d)
        if destination != d.get('recipient') or [a['email'] for a in accounts] != d.get('senders'):
            raise ValueError('Configuration changed. Preview again before sending.')
        if not self.store.claim(uid, d['id']):
            await self.say(uid, 'This case is no longer sendable. Use /status.')
            return
        if not d['test']:
            key = 'live-used:' + digest(d['platform'] + ':' + d['target'].lower())
            if self.store.get(key):
                self.store.save(uid, 'blocked_duplicate', d)
                raise ValueError('A live submission is already recorded for this target.')
            # Write before network access: a crash must never automatically repeat an allegation.
            self.store.put(key, d['id'])
        for a in accounts:
            self.store.attempt(d['id'], a['email'], 'sending')
        async def one(a):
            try:
                status, mid = await self.mail.send(a, destination, d['subject'], d['body'], d['id'], d['files'])
            except AuthError:
                status, mid = 'auth_failed', ''
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
        await self.say(uid, f"Case {d['id']} · {'TEST' if d['test'] else 'LIVE'}\nState: {phase}\n"
                       f"To: {d.get('recipient', 'not previewed')}\n" +
                       '\n'.join(f'{a}: {s}' + (f' · {mid}' if mid else '') for a,s,mid in results) +
                       '\n\nsubmitted = Gmail accepted the send request, not verified delivery or review. Check Gmail Sent, replies and bounce notices yourself. Uncertain sends are never automatically retried.')

    async def attach(self, uid, d, raw):
        if len(d['files']) >= 4 or len(raw) + sum(f['size'] for f in d['files']) > MAX_BYTES:
            raise ValueError('Maximum four attachments and 8 MB total.')
        if raw.startswith(b'\xff\xd8\xff'):
            ext, mime = 'jpg', 'image/jpeg'
        elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
            ext, mime = 'png', 'image/png'
        elif raw.startswith(b'%PDF-'):
            ext, mime = 'pdf', 'application/pdf'
        else:
            raise ValueError('Only JPEG, PNG or PDF evidence is supported.')
        d['files'].append({'filename': f"evidence-{len(d['files'])+1}.{ext}", 'mime': mime,
                           'size': len(raw), 'content': base64.b64encode(raw).decode()})
        self.store.save(uid, 'evidence', d)
        await self.say(uid, f"Attached {len(d['files'])}/4. Send more or /preview.")

    async def handle(self, update):
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
            elif action == 'verify':
                await self.verify(uid)
            elif action == 'toggle' and len(data) == 2 and data[1] in ('test','multi'):
                p = self.prefs(uid)
                if data[1] == 'multi' and not p['test']:
                    raise ValueError('10× mode is available only in TEST mode.')
                self.cancel_draft(uid)
                p[data[1]] = not p[data[1]]
                self.store.setprefs(uid, p)
                await self.settings(uid)
            elif action == 'platform' and len(data) == 2 and data[1] in ('telegram','whatsapp'):
                if phase in ('sending','unknown'):
                    raise ValueError('Check the previous uncertain submission first. Use /status; no resend is available.')
                self.cancel_draft(uid)
                p = self.prefs(uid)
                d = {'id': uuid.uuid4().hex[:12], 'platform': data[1], 'test': p['test'], 'multi': p['multi'], 'files': [], 'answers': {}}
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
                    if d['test']:
                        await self.preview(uid, d)
                    else:
                        d['answers'] = {'account_number': d['target'], 'reporter_contact': self.cfg.primary()['email']}
                        await self.field(uid, d)
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
        elif cmd == '/verify_test':
            await self.verify(uid)
        elif cmd == '/verify_code':
            await self.verify_code(uid, text.partition(' ')[2])
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
                # Retain minimal send ledger/hash to prevent duplicate live allegations.
            await self.say(uid, 'Latest local case details removed. Minimal submission markers remain; emails and Telegram copies are not deleted.')
        elif cmd == '/help':
            await self.say(uid, 'KANA NOTES\n/start — Telegram X / WhatsApp X\n/settings — test/live and 10× test toggles\n/verify_test — email a test-inbox verification code\n/verify_code CODE — verify ownership\n/preview — review complaint and attachments\n/status — latest case\n/cancel — cancel draft\n/delete — delete latest local case details\n\nTest notes use labelled sample placeholders, no evidence required, and go only to your verified inbox. Live mode collects actual incident fields and evidence/references and uses one primary Gmail. No automatic account bans or delivery claims.')
        elif phase == 'target':
            d['target'] = target(d['platform'], text)
            await self.groups(uid, d)
        elif phase == 'fields':
            if not 1 <= len(text) <= 1500 or '{{' in text:
                raise ValueError('Provide 1–1500 characters of actual information, without template markers.')
            if text.startswith('/'):
                raise ValueError('Send the requested incident detail, or /cancel.')
            d['answers'][d['field']] = text
            await self.field(uid, d)
        elif phase in ('evidence','review'):
            if cmd == '/preview':
                await self.preview(uid, d)
            elif cmd == '/clearfiles':
                d['files'] = []
                self.store.save(uid, 'evidence', d)
                await self.say(uid, 'Draft attachments removed. /preview again before sending.')
            elif msg.get('photo') or msg.get('document'):
                item = msg['photo'][-1] if msg.get('photo') else msg['document']
                if item.get('file_size', MAX_BYTES+1) > MAX_BYTES:
                    raise ValueError('File exceeds 8 MB.')
                meta = await self.tg('getFile', file_id=item['file_id'])
                raw = bytearray()
                async with self.http.get(f'https://api.telegram.org/file/bot{self.cfg.token}/' + meta['file_path']) as r:
                    r.raise_for_status()
                    async for chunk in r.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > MAX_BYTES:
                            raise ValueError('File exceeds 8 MB.')
                await self.attach(uid, d, bytes(raw))
            else:
                await self.say(uid, 'Use /preview or the confirmation button, or /cancel.')
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
    app = web.Application()
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
