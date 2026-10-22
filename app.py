"""MONTAi Sentinel: owner-only, reviewed incident email submissions."""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
CATEGORIES = ['Scam / fraud', 'Cyberbullying / harassment', 'Threats / extortion',
              'Suspected trafficking / exploitation', 'Impersonation',
              'Spam / abusive automation', 'Other harmful conduct',
              'My account: lost or stolen phone / deactivation request']
RECIPIENTS = ['support@whatsapp.com', 'abuse@whatsapp.com']
MAX_TOTAL = 8 * 1024 * 1024
MAX_FILES = 4


def phone(value):
    value = re.sub(r'[\s()-]', '', value)
    if not re.fullmatch(r'\+[1-9]\d{7,14}', value):
        raise ValueError('Use an international number with +, for example +234… (8–15 digits).')
    return value


def selections(value):
    parts = value.replace(' ', '').split(',')
    if not parts or any(not p.isdigit() or not 1 <= int(p) <= len(CATEGORIES) for p in parts):
        raise ValueError('Select category numbers such as 1,2,4.')
    return sorted(set(int(p) for p in parts))


def complaint(d):
    labels = '\n'.join('- ' + CATEGORIES[n-1] for n in d['categories'])
    return (f"MONTAi Sentinel — incident submission\nCase: {d['id']}\n"
            f"Account concerned: {d['phone']}\nReporter contact: {d['reply']}\n\n"
            f"Categories selected by reporter (allegations for review):\n{labels}\n\n"
            f"Reporter’s description:\n{d['details']}\n\n"
            f"Evidence attached: {len(d['files'])} file(s).\n"
            "Please review the supplied evidence and take any action you consider appropriate. "
            "This submission does not establish guilt or request automatic suspension.\n"
            "The reporter confirmed that the information is accurate to their knowledge and "
            "that they are authorized to share the evidence.\n")


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, owner INTEGER, state TEXT, data TEXT, updated REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        # A crash during provider submission has an uncertain outcome; never auto-resend.
        self.db.execute("UPDATE cases SET state='unknown' WHERE state='sending'")
        self.db.commit()

    def save(self, owner, state, d):
        self.db.execute('INSERT INTO cases VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,data=excluded.data,updated=excluded.updated',
                        (d['id'], owner, state, json.dumps(d), time.time()))
        self.db.commit()

    def current(self, owner):
        row = self.db.execute('SELECT state,data FROM cases WHERE owner=? ORDER BY updated DESC LIMIT 1', (owner,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def claim(self, owner, case_id):
        cur = self.db.execute("UPDATE cases SET state='sending',updated=? WHERE id=? AND owner=? AND state='review'", (time.time(), case_id, owner))
        self.db.commit()
        return cur.rowcount == 1

    def get_offset(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
        return int(row[0]) if row else 0

    def offset(self, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('offset',?)", (str(value),))
        self.db.commit()


class Bot:
    def __init__(self):
        self.token = os.environ['TELEGRAM_BOT_TOKEN']
        self.owners = {int(x.strip()) for x in os.environ['ADMIN_IDS'].split(',') if x.strip()}
        if not self.owners:
            raise ValueError('ADMIN_IDS must contain your numeric Telegram user ID.')
        self.store = Store(str(Path(os.getenv('DATA_DIR', './data')) / 'sentinel.db'))
        self.mode = os.getenv('SEND_MODE', 'test')
        if self.mode not in ('test', 'live'):
            raise ValueError('SEND_MODE must be test or live')
        self.ready = False

    async def tg(self, method, **body):
        async with self.http.post(f'https://api.telegram.org/bot{self.token}/{method}', json=body) as r:
            data = await r.json()
            if not data.get('ok'):
                raise RuntimeError('Telegram request failed: ' + method)
            return data['result']

    async def say(self, uid, text, buttons=None):
        # Plain text prevents user evidence from becoming HTML/Markdown instructions.
        for i in range(0, len(text), 3900):
            kw = {'reply_markup': {'inline_keyboard': buttons}} if buttons and i + 3900 >= len(text) else {}
            await self.tg('sendMessage', chat_id=uid, text=text[i:i+3900], **kw)

    async def categories(self, uid, d):
        buttons = [[{'text': ('✓ ' if n in d['categories'] else '') + f'{n}. {label}', 'callback_data': f"cat:{d['id']}:{n}"}]
                   for n, label in enumerate(CATEGORIES, 1)]
        buttons.append([{'text': 'Continue', 'callback_data': f"next:{d['id']}"}])
        await self.say(uid, 'Choose categories, then Continue. You can also send numbers such as 1,2,4.', buttons)

    async def preview(self, uid, d):
        if 8 in d['categories'] and not d.get('own_account'):
            await self.say(uid, 'This category is only for your own account. Send /ownaccount to confirm ownership, or /cancel.')
            return
        if 8 not in d['categories'] and not d['files']:
            await self.say(uid, 'Add at least one genuine screenshot or PDF before previewing this abuse report.')
            return
        d['body'] = complaint(d)
        self.store.save(uid, 'review', d)
        recipients = RECIPIENTS if self.mode == 'live' else [os.getenv('TEST_EMAIL', '(TEST_EMAIL not configured)')]
        await self.say(uid, f"PREVIEW · {self.mode.upper()}\nTo: {', '.join(recipients)}\n"
                       'WhatsApp mailbox handling is unverified. Sending does not confirm acceptance or action.\n\n' + d['body'])
        for f in d['files']:
            raw = base64.b64decode(f['content'])
            form = aiohttp.FormData()
            form.add_field('chat_id', str(uid))
            form.add_field('document', raw, filename=f['filename'], content_type=f['mime'])
            async with self.http.post(f'https://api.telegram.org/bot{self.token}/sendDocument', data=form) as r:
                result = await r.json()
                if not result.get('ok'):
                    self.store.save(uid, 'evidence', d)
                    await self.say(uid, 'Could not display an evidence attachment. Preview again before confirming.')
                    return
        await self.say(uid, 'Confirm only if the details are accurate to your knowledge and you are authorized to share every attachment.',
                       [[{'text': 'Confirm and send', 'callback_data': f"send:{d['id']}"}],
                        [{'text': 'Edit details', 'callback_data': f"edit:{d['id']}"}, {'text': 'Cancel', 'callback_data': f"cancel:{d['id']}"}]])

    async def attach(self, uid, d, raw, filename, mime):
        if len(d['files']) >= MAX_FILES or sum(f['size'] for f in d['files']) + len(raw) > MAX_TOTAL:
            raise ValueError('Maximum 4 attachments and 8 MB total per case.')
        if raw.startswith(b'\xff\xd8\xff'):
            ext, mime = '.jpg', 'image/jpeg'
        elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
            ext, mime = '.png', 'image/png'
        elif raw.startswith(b'%PDF-'):
            ext, mime = '.pdf', 'application/pdf'
        else:
            raise ValueError('Only genuine JPEG, PNG or PDF files are supported.')
        digest = hashlib.sha256(raw).hexdigest()
        if any(f['sha256'] == digest for f in d['files']):
            raise ValueError('That evidence is already attached.')
        d['files'].append({'filename': f'evidence-{len(d["files"])+1}{ext}', 'mime': mime,
                           'size': len(raw), 'sha256': digest, 'source_name': Path(filename).name,
                           'content': base64.b64encode(raw).decode()})
        self.store.save(uid, 'evidence', d)
        await self.say(uid, f"Evidence attached ({len(d['files'])}/4). Send more, /clearfiles, or /preview.")

    async def send(self, uid, d):
        api_key = os.getenv('RESEND_API_KEY')
        sender = os.getenv('MAIL_FROM')
        targets = RECIPIENTS if self.mode == 'live' else [os.getenv('TEST_EMAIL', '')]
        if not api_key or not sender or not all(targets):
            await self.say(uid, 'Mail is not configured. Set RESEND_API_KEY, MAIL_FROM, and TEST_EMAIL for test mode. Nothing was sent.')
            return
        if not self.store.claim(uid, d['id']):
            await self.say(uid, 'This preview is no longer sendable. Use /status.')
            return
        payload = {'from': sender, 'to': targets, 'reply_to': d['reply'],
                   'subject': f"Incident review request — {d['phone']} — {d['id']}",
                   'text': d['body'], 'attachments': [{'filename': f['filename'], 'content': f['content']} for f in d['files']]}
        d['recipients'] = targets
        try:
            async with self.http.post('https://api.resend.com/emails', json=payload,
                                      headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': 'sentinel/' + d['id']}) as r:
                result = await r.json()
                if r.status in (200, 201) and result.get('id'):
                    d['provider_id'] = result['id']
                    self.store.save(uid, 'submitted', d)
                    message = f"Email accepted by Resend for sending. ID: {result['id']}\nTo: {', '.join(targets)}\nThis does not confirm WhatsApp receipt, review, or action."
                elif 400 <= r.status < 500 and r.status not in (408, 409, 429):
                    d['provider_error'] = str(result.get('name', 'rejected'))[:100]
                    self.store.save(uid, 'rejected', d)
                    message = 'Mail provider rejected the request. Nothing is confirmed sent. Check your sender domain and Resend dashboard. Use /status.'
                else:
                    self.store.save(uid, 'unknown', d)
                    message = 'Submission outcome is uncertain. Check the Resend dashboard before creating another report. No automatic retry.'
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            self.store.save(uid, 'unknown', d)
            message = 'Submission outcome is uncertain. Check the Resend dashboard. No automatic retry.'
        await self.say(uid, message)

    async def handle(self, update):
        cb = update.get('callback_query')
        msg = update.get('message') or (cb or {}).get('message')
        actor = (cb or update.get('message') or {}).get('from', {})
        uid = actor.get('id')
        if not msg or uid not in self.owners or msg['chat']['type'] != 'private':
            if cb:
                await self.tg('answerCallbackQuery', callback_query_id=cb['id'], text='Owner-only private bot.')
            return
        state, d = self.store.current(uid)
        text = msg.get('text', '').strip() if not cb else ''
        if cb:
            await self.tg('answerCallbackQuery', callback_query_id=cb['id'])
            parts = cb.get('data', '').split(':')
            if len(parts) < 2 or not d or parts[1] != d['id']:
                return
            action = parts[0]
            if action == 'send' and state == 'review':
                await self.send(uid, d)
            elif action == 'cat' and state == 'categories' and len(parts) == 3:
                n = int(parts[2])
                if 1 <= n <= len(CATEGORIES):
                    d['categories'] = sorted(set(d['categories']) ^ {n})
                    self.store.save(uid, state, d)
                    await self.categories(uid, d)
            elif action == 'next' and state == 'categories':
                if not d['categories']:
                    await self.say(uid, 'Select at least one category.')
                else:
                    self.store.save(uid, 'details', d)
                    await self.say(uid, 'Describe what happened, when, your connection to the account, and which evidence supports each selected category (30–2500 characters).')
            elif action == 'edit' and state == 'review':
                self.store.save(uid, 'details', d)
                await self.say(uid, 'Send the replacement incident description.')
            elif action == 'cancel' and state in ('review', 'categories'):
                self.store.save(uid, 'cancelled', d)
                await self.say(uid, 'Cancelled. Nothing sent.')
            return
        command = text.split(' ')[0].split('@')[0].lower()
        if command in ('/start', '/help'):
            await self.say(uid, 'MONTAi Sentinel\nSocial safety and reporting assistant.\n\n/new — prepare a case\n/preview — review complaint and attachments\n/status — latest case and provider status\n/evidence — list named local evidence\n/attach filename — attach a named file\n/clearfiles — remove draft evidence\n/ownaccount — confirm an own-account request\n/cancel — discard draft\n/delete — remove latest local case\n\nOnly genuine, incident-specific evidence. Category labels are not proof. Mailbox handling is unverified; this bot cannot promise bans. Mode: ' + self.mode)
            return
        if command == '/status':
            if not d:
                await self.say(uid, 'No cases yet. Use /new.')
                return
            status = f"Case {d['id']}\nState: {state}\nProvider ID: {d.get('provider_id', 'none')}"
            if d.get('provider_id') and os.getenv('RESEND_API_KEY'):
                try:
                    async with self.http.get('https://api.resend.com/emails/' + d['provider_id'], headers={'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY']}) as r:
                        event = await r.json()
                        status += '\nProvider event: ' + str(event.get('last_event', 'unavailable'))
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    status += '\nProvider status unavailable.'
            await self.say(uid, status + '\nDelivery is not confirmation of review or account action. Replies go to your supplied contact email; they are not read by this bot.')
            return
        if command == '/delete':
            if state == 'sending':
                await self.say(uid, 'Submission in progress. Check /status first.')
                return
            if d:
                self.store.db.execute('DELETE FROM cases WHERE id=? AND owner=?', (d['id'], uid))
                self.store.db.commit()
            await self.say(uid, 'Latest local case deleted. Emails and Telegram copies are not recalled.')
            return
        if command == '/new':
            if state == 'unknown':
                await self.say(uid, 'Previous submission outcome is uncertain. Check Resend first; /delete the local case only after resolving it.')
                return
            d = {'id': uuid.uuid4().hex[:12], 'categories': [], 'files': []}
            self.store.save(uid, 'phone', d)
            await self.say(uid, 'Enter the account’s international number, starting with +.')
            return
        if command == '/evidence':
            root = Path('evidence')
            names = [p.name for p in root.iterdir() if p.is_file() and p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.pdf')]
            await self.say(uid, 'Named evidence:\n' + ('\n'.join(names[:50]) or '(none)') + '\nUse /attach filename in an evidence draft. Keep chat evidence out of public repositories.')
            return
        if not d or state in ('submitted', 'rejected', 'cancelled', 'unknown'):
            await self.say(uid, 'Use /new to start a case, or /status.')
            return
        if command == '/cancel':
            self.store.save(uid, 'cancelled', d)
            await self.say(uid, 'Cancelled. Nothing sent.')
        elif state == 'phone':
            d['phone'] = phone(text)
            self.store.save(uid, 'reply', d)
            await self.say(uid, 'Enter your own contact email for replies. This will be shared with the recipients.')
        elif state == 'reply':
            if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', text) or len(text) > 254:
                raise ValueError('Enter a valid contact email.')
            d['reply'] = text
            self.store.save(uid, 'categories', d)
            await self.categories(uid, d)
        elif state == 'categories':
            d['categories'] = selections(text)
            self.store.save(uid, 'details', d)
            await self.say(uid, 'Describe what happened, when, your connection to the account, and evidence for each category (30–2500 characters).')
        elif state == 'details':
            if not 30 <= len(text) <= 2500:
                raise ValueError('Use 30–2500 characters describing the actual incident.')
            d['details'] = text
            self.store.save(uid, 'evidence', d)
            await self.say(uid, 'Attach up to 4 JPEG/PNG screenshots or PDFs (8 MB total), or /attach filename. Then /preview. Do not upload illegal imagery; describe it and use the appropriate reporting channel.')
        elif state == 'evidence':
            if command == '/ownaccount':
                d['own_account'] = True
                self.store.save(uid, state, d)
                await self.say(uid, 'Own-account declaration recorded. Use /preview.')
            elif command == '/clearfiles':
                d['files'] = []
                self.store.save(uid, state, d)
                await self.say(uid, 'Draft attachments removed.')
            elif command == '/preview':
                await self.preview(uid, d)
            elif command == '/attach':
                name = text.partition(' ')[2].strip()
                root = Path('evidence').resolve()
                p = (root / name).resolve()
                if not name or p.parent != root or not p.is_file() or p.stat().st_size > MAX_TOTAL:
                    raise ValueError('Choose a valid local evidence filename, up to 8 MB.')
                await self.attach(uid, d, p.read_bytes(), p.name, '')
            elif msg.get('photo') or msg.get('document'):
                item = msg['photo'][-1] if msg.get('photo') else msg['document']
                if item.get('file_size', MAX_TOTAL + 1) > MAX_TOTAL:
                    raise ValueError('File exceeds 8 MB.')
                meta = await self.tg('getFile', file_id=item['file_id'])
                async with self.http.get(f'https://api.telegram.org/file/bot{self.token}/' + meta['file_path']) as r:
                    r.raise_for_status()
                    data = bytearray()
                    async for chunk in r.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_TOTAL:
                            raise ValueError('File exceeds 8 MB.')
                await self.attach(uid, d, bytes(data), item.get('file_name', 'screenshot.jpg'), '')
            else:
                await self.say(uid, 'Send evidence or /preview.')
        elif state == 'review':
            if command == '/preview':
                await self.preview(uid, d)
            else:
                await self.say(uid, 'Use the preview buttons to send, edit, or cancel.')

    async def run(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as self.http:
            # Polling and webhooks cannot run together. Keep pending updates for deduplication.
            await self.tg('deleteWebhook', drop_pending_updates=False)
            self.ready = True
            offset = self.store.get_offset()
            while True:
                try:
                    updates = await self.tg('getUpdates', offset=offset, timeout=30, allowed_updates=['message', 'callback_query'])
                    for update in updates:
                        try:
                            await self.handle(update)
                        except ValueError as e:
                            uid = update.get('message', {}).get('from', {}).get('id')
                            if uid in self.owners:
                                await self.say(uid, str(e))
                        except Exception:
                            logging.error('Update processing failed; private payload omitted.')
                        offset = update['update_id'] + 1
                        self.store.offset(offset)
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError):
                    logging.warning('Telegram polling unavailable; retrying.')
                    await asyncio.sleep(5)


async def main():
    bot = Bot()
    app = web.Application()
    async def health(_):
        return web.json_response({'service': 'MONTAi Sentinel', 'ready': bot.ready}, status=200 if bot.ready else 503)
    app.router.add_get('/', health)
    app.router.add_get('/healthz', health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', '10000'))).start()
    try:
        await bot.run()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    asyncio.run(main())
