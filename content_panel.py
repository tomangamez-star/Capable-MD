"""Owner web editor and separate opt-in notification/feedback portal."""
import asyncio
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from aiohttp import web
from core import email

DEFAULT = {
    'heading': 'ＫＡＮＡ  ＮＯＴＥＳ',
    'welcome': 'Welcome to KANA NOTES. Choose an option below.',
    'join_prompt': 'Join all four chats, then tap Verify membership.',
    'verify_label': '✅ Verify membership',
    'telegram_label': 'Telegram X', 'whatsapp_label': 'WhatsApp X',
    'send_label': 'Send', 'cancel_label': 'Cancel',
    'processing': 'Processing…', 'completion': 'Your request has been processed.',
    'feedback_prompt': 'Write your feedback (1–2000 characters). It will be sent to the shared inbox.',
    'reference_prompt': 'Enter a reference number for your notification (1–80 characters).',
    'report_label': 'Reports', 'free_usage': False,
    'entries': [{'title': f'Notification {i}', 'body': '', 'enabled': False} for i in range(1, 31)]
}
LIMITS = {'heading': 100, 'welcome': 2000, 'join_prompt': 500, 'verify_label': 48, 'telegram_label': 48, 'whatsapp_label': 48,
          'send_label': 48, 'cancel_label': 48, 'processing': 160, 'completion': 1000,
          'feedback_prompt': 500, 'reference_prompt': 500, 'report_label': 48}


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Content fields do not match the editor schema.')
    for key, limit in LIMITS.items():
        if not isinstance(value[key], str) or not 1 <= len(value[key].strip()) <= limit:
            raise ValueError(f'{key}: enter 1–{limit} characters.')
        if '\x00' in value[key]:
            raise ValueError('Null characters are not supported.')
    if type(value['free_usage']) is not bool:
        raise ValueError('Free usage must be on or off.')
    entries = value['entries']
    if not isinstance(entries, list) or len(entries) != 30:
        raise ValueError('Keep exactly 30 notification entries.')
    for i, n in enumerate(entries, 1):
        if not isinstance(n, dict) or set(n) != {'title', 'body', 'enabled'}:
            raise ValueError(f'Entry {i}: invalid fields.')
        if not isinstance(n['title'], str) or not 1 <= len(n['title'].strip()) <= 80:
            raise ValueError(f'Entry {i}: title must be 1–80 characters.')
        if any(c in n['title'] for c in ('\r', '\n', '\x00')):
            raise ValueError(f'Entry {i}: title must fit on one line.')
        if not isinstance(n['body'], str) or len(n['body']) > 3000 or '\x00' in n['body']:
            raise ValueError(f'Entry {i}: body must be at most 3000 characters.')
        if type(n['enabled']) is not bool or (n['enabled'] and not n['body'].strip()):
            raise ValueError(f'Entry {i}: enabled entries need message text.')
    if value['free_usage'] and not any(n['enabled'] for n in entries):
        raise ValueError('Enable at least one notification entry before turning free usage on.')
    return value


class Content:
    def __init__(self, db):
        self.db = db
        db.executescript('''
        CREATE TABLE IF NOT EXISTS display_content(id INTEGER PRIMARY KEY,revision INTEGER,draft TEXT,published TEXT);
        CREATE TABLE IF NOT EXISTS display_history(id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL,content TEXT);
        CREATE TABLE IF NOT EXISTS portal_requests(id TEXT PRIMARY KEY,owner INTEGER,state TEXT,data TEXT,created REAL,result TEXT);
        CREATE TABLE IF NOT EXISTS portal_inbox(address TEXT PRIMARY KEY,verified INTEGER,code_hash TEXT,expires REAL,attempts INTEGER);
        ''')
        raw = json.dumps(DEFAULT)
        db.execute('INSERT OR IGNORE INTO display_content VALUES (1,0,?,?)', (raw, raw))
        db.execute("UPDATE portal_requests SET state='unknown' WHERE state='sending'")
        db.commit()

    def state(self):
        rev, draft, published = self.db.execute('SELECT revision,draft,published FROM display_content WHERE id=1').fetchone()
        history = [{'id': i, 'created': t} for i, t in self.db.execute(
            'SELECT id,created FROM display_history ORDER BY id DESC LIMIT 20')]
        return {'revision': rev, 'draft': json.loads(draft), 'published': json.loads(published), 'history': history}

    def live(self):
        return json.loads(self.db.execute('SELECT published FROM display_content WHERE id=1').fetchone()[0])

    def change(self, expected, value, publish=False):
        validate(value)
        raw = json.dumps(value)
        with self.db:
            current = self.db.execute('SELECT revision FROM display_content WHERE id=1').fetchone()[0]
            if type(expected) is not int or expected != current:
                raise ValueError('Another edit was saved. Reload the panel before saving again.')
            if publish:
                self.db.execute('INSERT INTO display_history(created,content) VALUES (?,?)', (time.time(), raw))
                self.db.execute('UPDATE display_content SET revision=revision+1,draft=?,published=? WHERE id=1', (raw, raw))
            else:
                self.db.execute('UPDATE display_content SET revision=revision+1,draft=? WHERE id=1', (raw,))
        return self.state()


def button(text, data, style='primary'):
    return {'text': text, 'callback_data': data, 'style': style}


class Portal:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.store.db
        self.content = Content(self.db)
        self.password = os.getenv('ADMIN_PANEL_PASSWORD', '')
        self.sessions = {}
        self.failures = {}
        self.challenges = {}

    async def display(self, uid, text, rows=None, heading=None):
        """Native rich button rows; plain inline fallback for unsupported servers."""
        esc = html.escape
        rich = (f'<h2>{esc(heading)}</h2>' if heading else '')
        rich += '<blockquote>' + esc(text).replace('\n', '<br/>') + '</blockquote>'
        for row in rows or []:
            rich += '<tg-button-row align="center">'
            for b in row:
                kind = 'url' if 'url' in b else 'callback_data'
                attr = 'url' if kind == 'url' else 'data'
                val = b['url'] if kind == 'url' else b['callback_data']
                rich += (f'<tg-button type="{kind}" style="{b.get("style", "primary")}" '
                         f'{attr}="{esc(val, quote=True)}">{esc(b["text"])}</tg-button>')
            rich += '</tg-button-row>'
        try:
            return await self.bot.tg('sendRichMessage', chat_id=uid, rich_message={'html': rich})
        except RuntimeError:
            return await self.bot.say(uid, ((heading + '\n\n') if heading else '') + text, rows)

    async def home(self, uid):
        c = self.content.live()
        rows = [[button(c['telegram_label'], 'portal:telegram'), button(c['whatsapp_label'], 'portal:whatsapp')],
                [button(c['report_label'], 'portal:reports')]]
        if uid in self.bot.cfg.admins:
            rows.append([button('Admin panel · /admin', 'portal:admin')])
        await self.display(uid, c['welcome'], rows, c['heading'])

    def recipient(self):
        raw = os.getenv('NOTIFICATION_EMAIL') or os.getenv('TEST_RECIPIENT_EMAIL') or ''
        try:
            return email(raw)
        except ValueError:
            return ''

    def verified(self):
        address = self.recipient()
        row = self.db.execute('SELECT verified FROM portal_inbox WHERE address=?', (address,)).fetchone()
        return bool(address and row and row[0])

    def primary(self):
        wanted = os.getenv('GMAIL_SENDER', '').strip().lower()
        return next((a for a in self.bot.cfg.senders() if a['email'].lower() == wanted), None)

    def register(self, app):
        app.router.add_get('/admin', self.page)
        app.router.add_get('/admin/', self.page)
        app.router.add_get('/admin/app.js', self.asset)
        app.router.add_get('/admin/style.css', self.asset)
        app.router.add_post('/admin/login', self.login)
        app.router.add_get('/admin/api/state', self.api)
        app.router.add_post('/admin/api/{action}', self.api)

    @staticmethod
    def headers():
        return {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
                'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}

    async def page(self, request):
        return web.Response(text=(Path(__file__).parent / 'panel/index.html').read_text(),
                            content_type='text/html', headers=self.headers())

    async def asset(self, request):
        name = request.path.rsplit('/', 1)[-1]
        return web.Response(text=(Path(__file__).parent / 'panel' / name).read_text(),
                            content_type='text/javascript' if name.endswith('.js') else 'text/css', headers=self.headers())

    async def login(self, request):
        if len(self.password) < 16:
            return web.json_response({'error': 'Set ADMIN_PANEL_PASSWORD to at least 16 characters in Render.'}, status=503, headers=self.headers())
        now = time.time()
        self.failures = {k: v for k, v in self.failures.items() if v[1] > now}
        self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
        key = request.remote or 'unknown'
        count, until = self.failures.get(key, (0, now + 600))
        if count >= 8:
            return web.json_response({'error': 'Too many login attempts. Try again in ten minutes.'}, status=429, headers=self.headers())
        try:
            body = await request.json()
            supplied = body.get('password', '')
            valid = isinstance(supplied, str) and hmac.compare_digest(supplied.encode(), self.password.encode())
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            self.failures[key] = (count + 1, until)
            return web.json_response({'error': 'Incorrect password.'}, status=401, headers=self.headers())
        self.failures.pop(key, None)
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = {'csrf': secrets.token_urlsafe(32), 'expires': now + 28800}
        # Bound sessions without affecting persistent content.
        if len(self.sessions) > 100:
            self.sessions.pop(next(iter(self.sessions)))
        response = web.json_response({'ok': True}, headers=self.headers())
        response.set_cookie('kana_admin', sid, secure=True, httponly=True, samesite='Strict', max_age=28800, path='/admin')
        return response

    async def api(self, request):
        session = self.sessions.get(request.cookies.get('kana_admin', ''))
        if not session or session['expires'] <= time.time():
            return web.json_response({'error': 'Sign in to continue.'}, status=401, headers=self.headers())
        if request.method == 'POST' and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
            return web.json_response({'error': 'Session check failed. Reload the panel.'}, status=403, headers=self.headers())
        action = request.match_info.get('action', 'state')
        try:
            body = await request.json() if request.method == 'POST' else {}
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object.')
            if action in ('save', 'publish'):
                self.content.change(body.get('revision'), body.get('content'), action == 'publish')
            elif action == 'rollback':
                ident = body.get('id')
                if type(ident) is not int:
                    raise ValueError('Choose a saved version.')
                row = self.db.execute('SELECT content FROM display_history WHERE id=?', (ident,)).fetchone()
                if not row:
                    raise ValueError('Version not found.')
                self.content.change(body.get('revision'), json.loads(row[0]), True)
            elif action == 'logout':
                self.sessions.pop(request.cookies.get('kana_admin', ''), None)
                response = web.json_response({'ok': True}, headers=self.headers())
                response.del_cookie('kana_admin', path='/admin')
                return response
            elif action == 'verify-send':
                await self.send_verification()
            elif action == 'verify-code':
                self.verify_code(str(body.get('code', '')))
            elif action != 'state':
                raise ValueError('Unknown action.')
            result = self.content.state()
            result.update(csrf=session['csrf'], inbox=self.recipient(), inbox_verified=self.verified(),
                          primary_ready=bool(self.primary()))
            return web.json_response(result, headers=self.headers())
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400, headers=self.headers())
        except Exception:
            return web.json_response({'error': 'Operation failed. Check server configuration; no automatic resend was made.'}, status=502, headers=self.headers())

    async def send_verification(self):
        address, account = self.recipient(), self.primary()
        if not address or not account or not getattr(self.bot, 'mail', None):
            raise ValueError('Set NOTIFICATION_EMAIL, GMAIL_SENDER and its working Gmail credentials first.')
        row = self.db.execute('SELECT expires FROM portal_inbox WHERE address=?', (address,)).fetchone()
        if row and row[0] > time.time() + 540:
            raise ValueError('Wait one minute before requesting another code.')
        code = str(secrets.randbelow(900000) + 100000)
        hashed = hashlib.sha256(code.encode()).hexdigest()
        self.db.execute('INSERT INTO portal_inbox VALUES (?,0,?,?,0) ON CONFLICT(address) DO UPDATE SET '
                        'verified=0,code_hash=excluded.code_hash,expires=excluded.expires,attempts=0',
                        (address, hashed, time.time() + 600))
        self.db.commit()
        result, _ = await self.bot.mail.send(account, address, 'KANA NOTES inbox confirmation',
                                              'Your confirmation code is ' + code + '. It expires in 10 minutes.\n'
                                              'Enter this only in your KANA NOTES owner dashboard.', uuid.uuid4().hex)
        if result != 'submitted':
            raise ValueError('Gmail did not confirm accepting the verification email. Check Sent and inbox before requesting again.')

    def verify_code(self, code):
        address = self.recipient()
        row = self.db.execute('SELECT code_hash,expires,attempts FROM portal_inbox WHERE address=?', (address,)).fetchone()
        if not row or row[1] < time.time() or row[2] >= 6:
            raise ValueError('Code expired or too many attempts. Request a new code.')
        self.db.execute('UPDATE portal_inbox SET attempts=attempts+1 WHERE address=?', (address,))
        self.db.commit()
        if not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), row[0]):
            raise ValueError('Incorrect confirmation code.')
        self.db.execute("UPDATE portal_inbox SET verified=1,code_hash='',expires=0 WHERE address=?", (address,))
        self.db.commit()

    def current(self, uid):
        row = self.db.execute('SELECT id,state,data FROM portal_requests WHERE owner=? ORDER BY created DESC LIMIT 1', (uid,)).fetchone()
        return (row[0], row[1], json.loads(row[2])) if row else (None, None, None)

    async def telegram(self, uid, text, data, owner):
        """Called only after PublicAccess has verified all memberships."""
        c = self.content.live()
        command = text.split(' ')[0].split('@')[0].lower()
        if command == '/admin' or data == 'portal:admin':
            if not owner:
                await self.bot.say(uid, 'Owner-only panel.')
            else:
                await self.bot.say(uid, 'Open your Render service URL followed by /admin. Sign in with ADMIN_PANEL_PASSWORD.')
            return True
        if command == '/freeusage':
            if not owner:
                await self.bot.say(uid, 'Owner-only command.')
            else:
                val = text.partition(' ')[2].strip().lower()
                if val not in ('on', 'off'):
                    await self.bot.say(uid, 'Use /freeusage on or /freeusage off.')
                else:
                    c['free_usage'] = val == 'on'
                    try:
                        self.content.change(self.content.state()['revision'], c, True)
                        await self.bot.say(uid, 'Free usage ' + val + '. Published immediately.')
                    except ValueError as exc:
                        await self.bot.say(uid, str(exc))
            return True
        if command in ('/start', '/new') or data == 'portal:home':
            # Starting again abandons only a pending portal request.
            self.db.execute("UPDATE portal_requests SET state='cancelled' WHERE owner=? AND state IN ('input','review')", (uid,))
            self.db.commit()
            await self.home(uid)
            return True
        if command == '/report' or data == 'portal:reports':
            self.db.execute("UPDATE portal_requests SET state='cancelled' WHERE owner=? AND state IN ('input','review')", (uid,))
            self.db.commit()
            await (self.bot.report_home(uid) if owner else self.bot.public_access.report_home(uid))
            return True
        if data in ('portal:telegram', 'portal:whatsapp') or command == '/feedback':
            free = (c['free_usage'] or owner) and command != '/feedback'
            d = {'mode': 'notification' if free else 'feedback',
                 'platform': data.partition(':')[2] if data else 'feedback'}
            ident = uuid.uuid4().hex[:12]
            self.db.execute("UPDATE portal_requests SET state='cancelled' WHERE owner=? AND state IN ('input','review')", (uid,))
            self.db.execute('INSERT INTO portal_requests VALUES (?,?,?,?,?,?)',
                            (ident, uid, 'input', json.dumps(d), time.time(), ''))
            self.db.commit()
            await self.display(uid, c['reference_prompt'] if free else c['feedback_prompt'])
            return True
        ident, phase, d = self.current(uid)
        if data.startswith('portalsend:'):
            if not ident or data != 'portalsend:' + ident or phase != 'review':
                await self.bot.say(uid, 'This request is no longer sendable. Use /start.')
            else:
                await self.deliver(uid, ident, d)
            return True
        if data.startswith('portalcancel:'):
            if ident and data == 'portalcancel:' + ident and phase in ('input', 'review'):
                self.db.execute("UPDATE portal_requests SET state='cancelled' WHERE id=? AND owner=?", (ident, uid))
                self.db.commit()
            await self.bot.say(uid, 'Request cancelled.')
            return True
        if command == '/cancel' and phase in ('input', 'review'):
            self.db.execute("UPDATE portal_requests SET state='cancelled' WHERE id=?", (ident,))
            self.db.commit()
            await self.bot.say(uid, 'Request cancelled.')
            return True
        if command == '/notification_status':
            row = self.db.execute('SELECT result FROM portal_requests WHERE id=?', (ident,)).fetchone()
            await self.bot.say(uid, f'Request {ident} · {phase}\n{row[0]}' if ident else 'No notification request yet.')
            return True
        if text and not command.startswith('/') and phase == 'input':
            limit = 80 if d['mode'] == 'notification' else 2000
            if not 1 <= len(text) <= limit:
                await self.bot.say(uid, f'Enter 1–{limit} characters.')
                return True
            if d['mode'] == 'notification':
                entries = [n for n in c['entries'] if n['enabled'] and n['body'].strip()]
                if not entries:
                    await self.bot.say(uid, 'No notification content has been published yet.')
                    return True
                n = secrets.choice(entries)
                d.update(subject=n['title'], body=n['body'], reference=text)
            else:
                d.update(subject='KANA NOTES feedback', body=text, reference='')
            d['recipient'] = self.recipient()
            self.db.execute("UPDATE portal_requests SET state='review',data=? WHERE id=?", (json.dumps(d), ident))
            self.db.commit()
            await self.display(uid, d['subject'] + '\n\n' + d['body'],
                               [[button(c['send_label'], 'portalsend:' + ident, 'success'),
                                 button(c['cancel_label'], 'portalcancel:' + ident, 'danger')]], 'PREVIEW')
            return True
        if data.startswith(('portal:', 'portalsend:', 'portalcancel:')):
            await self.bot.say(uid, 'Use /start to open the current menu.')
            return True
        return False

    async def deliver(self, uid, ident, d):
        if d.get('recipient') != self.recipient():
            await self.bot.say(uid, 'The notification inbox changed. Use /start and review a fresh request.')
            return
        if not self.verified() or not self.primary():
            await self.bot.say(uid, 'The owner needs to configure and verify the notification inbox in /admin first.')
            return
        now = time.time()
        # Separate notification flow: one primary sender, one confirmed inbox.
        recent = self.db.execute("SELECT COUNT(*) FROM portal_requests WHERE owner=? AND created>? AND state IN ('sending','submitted','unknown')", (uid, now - 60)).fetchone()[0]
        total = self.db.execute("SELECT COUNT(*) FROM portal_requests WHERE created>? AND state IN ('sending','submitted','unknown')", (now - 3600,)).fetchone()[0]
        if recent or total >= 20:
            await self.bot.say(uid, 'Please wait before sending another notification.')
            return
        cur = self.db.execute("UPDATE portal_requests SET state='sending',created=? WHERE id=? AND owner=? AND state='review'", (now, ident, uid))
        self.db.commit()
        if cur.rowcount != 1:
            await self.bot.say(uid, 'This request has already been processed.')
            return
        c = self.content.live()
        task = asyncio.create_task(self.busy(uid, c['processing']))
        state = 'unknown'
        try:
            body = (d['body'] + f'\n\nKANA NOTES notification {ident}\nSubmitted by Telegram user {uid}\n'
                    + ('Reference: ' + d['reference'] + '\n' if d['reference'] else ''))
            state, _ = await self.bot.mail.send(self.primary(), self.recipient(), d['subject'], body, ident)
        except Exception:
            state = 'failed'
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.db.execute('UPDATE portal_requests SET state=?,result=? WHERE id=?', (state, state, ident))
        self.db.commit()
        if state == 'submitted':
            await self.display(uid, c['completion'] + '\n\nGmail accepted this message. Delivery is not yet confirmed.\nRequest: ' + ident)
        else:
            await self.display(uid, 'Message status: ' + state + '. No automatic retry was made.\nRequest: ' + ident)

    async def busy(self, uid, text):
        draft = secrets.randbelow(2147483646) + 1
        while True:
            try:
                await self.bot.tg('sendMessageDraft', chat_id=uid, draft_id=draft, text=text)
            except Exception:
                try:
                    await self.bot.tg('sendChatAction', chat_id=uid, action='typing')
                except Exception:
                    pass
            await asyncio.sleep(4)
