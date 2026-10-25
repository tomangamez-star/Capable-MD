import asyncio, base64, json, os, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from email import policy
from email.parser import BytesParser
from core import Config, Notes, Store, digest, target
from app import Bot, button
from gmail_mail import Gmail, raw_message, AuthError

SENDERS = [{'email': f'sender{i}@gmail.com', 'refresh_token': f'token{i}'} for i in range(1, 11)]


def env(**over):
    base = {'TELEGRAM_BOT_TOKEN': 'fake', 'ADMIN_IDS': '42', 'GMAIL_CLIENT_ID': 'fake',
            'GMAIL_CLIENT_SECRET': 'fake', 'GMAIL_SENDER': 'sender1@gmail.com',
            'GMAIL_REFRESH_TOKEN': 'token1', 'DATA_DIR': './data',
            'GMAIL_EXTRA_ACCOUNTS_JSON': json.dumps(SENDERS[1:])}
    base.update(over)
    return base


class CoreTests(unittest.TestCase):
    def test_original_pack_bytes(self):
        root = Path(__file__).resolve().parents[1] / 'assets'
        with zipfile.ZipFile(root / 'original-report-notes.zip') as z:
            for n in z.namelist():
                self.assertEqual(z.read(n), (root / 'report-pack' / n).read_bytes())

    def test_all_templates_for_both_platforms(self):
        notes = Notes()
        for platform in ('telegram', 'whatsapp'):
            for n in range(1, 31):
                d = {'platform': platform, 'note': n, 'target': '@example', 'answers': {}}
                with self.assertRaises(ValueError):
                    notes.render(d, 'me@example.com')
                d['answers'] = {k: 'Actual facts supplied' for k in notes.fields(n)}
                sub, body = notes.render(d, 'me@example.com')
                self.assertNotIn('{{', body)
                self.assertNotIn('{{', sub)
                self.assertIn('To Telegram’s review team,' if platform == 'telegram' else 'To WhatsApp’s review team,', body)
        self.assertIn('what_I_paid_or_was_asked_to_pay', notes.fields(1))

    def test_target_validation(self):
        self.assertEqual(target('telegram', '@example'), '@example')
        self.assertEqual(target('telegram', 'https://t.me/example/12'), 'https://t.me/example/12')
        self.assertEqual(target('whatsapp', '+234 801 234 5678'), '+2348012345678')
        for value in ('https://evil.com/user', 'https://t.me@evil.com/a', '123', '@x'):
            with self.assertRaises(ValueError):
                target('telegram', value)
        self.assertLessEqual(len(button('Note', 'note:123456789012:30')['callback_data']), 64)

    def test_mime_attachment_bytes(self):
        f = {'filename': 'proof.pdf', 'mime': 'application/pdf', 'content': base64.b64encode(b'%PDF-proof').decode()}
        raw = raw_message('me@gmail.com', 'test@example.com', 'Title', 'Body', 'case', [f])
        m = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(str(m['To']), 'test@example.com')
        self.assertEqual(list(m.iter_attachments())[0].get_payload(decode=True), b'%PDF-proof')

    def test_crash_and_claim(self):
        with tempfile.TemporaryDirectory() as td:
            s = Store(Path(td) / 'db')
            s.save(42, 'review', {'id': 'x'})
            self.assertFalse(s.claim(7, 'x'))
            self.assertTrue(s.claim(42, 'x'))
            self.assertFalse(s.claim(42, 'x'))
            s.attempt('x', 'me@example.com', 'sending')
            s.db.close()
            s = Store(Path(td) / 'db')
            self.assertEqual(s.current(42)[0], 'unknown')
            self.assertEqual(s.attempts('x')[0][1], 'unknown')
            s.db.close()


class NoGatingTests(unittest.TestCase):
    """No configured email address may block boot or sending."""

    def test_all_ten_senders_loaded(self):
        with patch.dict(os.environ, env(), clear=True):
            cfg = Config()
        self.assertEqual([a['email'] for a in cfg.accounts], [s['email'] for s in SENDERS])
        self.assertEqual(len(cfg.senders()), 10)

    def test_no_test_recipient_needed_to_boot(self):
        e = env()
        e.pop('GMAIL_EXTRA_ACCOUNTS_JSON')
        with patch.dict(os.environ, e, clear=True):
            cfg = Config()          # must not raise without a test inbox
        self.assertFalse(hasattr(cfg, 'test_email'))

    def test_bogus_report_address_falls_back(self):
        with patch.dict(os.environ, env(TELEGRAM_REPORT_EMAIL='not-an-email',
                                        WHATSAPP_REPORT_EMAIL=''), clear=True):
            cfg = Config()
        self.assertEqual(cfg.reporting['telegram'], 'abuse@telegram.org')
        self.assertEqual(cfg.reporting['whatsapp'], 'abuse@whatsapp.com')
        self.assertTrue(any('TELEGRAM_REPORT_EMAIL' in w for w in cfg.warnings))

    def test_test_address_shared_with_report_address_is_harmless(self):
        with patch.dict(os.environ, env(TELEGRAM_REPORT_EMAIL='sender1@gmail.com',
                                        TEST_RECIPIENT_EMAIL='sender1@gmail.com'), clear=True):
            cfg = Config()          # no test/live divergence check can fire
        self.assertEqual(cfg.reporting['telegram'], 'sender1@gmail.com')

    def test_duplicate_and_malformed_senders_skipped_not_fatal(self):
        extra = [{'email': 'sender1@gmail.com', 'refresh_token': 'dup'},
                 {'email': 'broken', 'refresh_token': 'x'},
                 {'refresh_token': 'no-email'}]
        with patch.dict(os.environ, env(GMAIL_EXTRA_ACCOUNTS_JSON=json.dumps(extra)), clear=True):
            cfg = Config()
        self.assertEqual([a['email'] for a in cfg.accounts], ['sender1@gmail.com'])
        self.assertEqual(len(cfg.warnings), 3)

    def test_no_sender_is_a_warning_not_a_crash(self):
        e = {'TELEGRAM_BOT_TOKEN': 'fake', 'ADMIN_IDS': '42'}
        with patch.dict(os.environ, e, clear=True):
            cfg = Config()
        self.assertEqual(cfg.accounts, [])
        self.assertTrue(any('No Gmail sender' in w for w in cfg.warnings))

    def test_more_than_ten_senders_truncated_with_warning(self):
        extra = [{'email': f's{i}@gmail.com', 'refresh_token': 't'} for i in range(2, 13)]
        with patch.dict(os.environ, env(GMAIL_EXTRA_ACCOUNTS_JSON=json.dumps(extra)), clear=True):
            cfg = Config()
        self.assertEqual(len(cfg.accounts), 10)
        self.assertTrue(any('first 10' in w for w in cfg.warnings))

    def test_missing_token_still_mandatory(self):
        with patch.dict(os.environ, {'ADMIN_IDS': '42'}, clear=True):
            with self.assertRaises(ValueError):
                Config()


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, env(DATA_DIR=self.tmp.name), clear=True)
        self.env.start()
        self.bot = Bot()
        self.messages = []
        async def say(uid, text, rows=None): self.messages.append((text, rows))
        async def tg(*a, **kw): return True
        self.bot.say = say
        self.bot.tg = tg
        class Mail:
            def __init__(self):
                self.sent = []; self.result = 'submitted'; self.active = 0; self.peak = 0
            async def send(self, *args):
                self.sent.append(args)
                self.active += 1; self.peak = max(self.peak, self.active)
                await asyncio.sleep(0); self.active -= 1
                return self.result, 'mock-id'
        self.bot.mail = Mail()

    async def asyncTearDown(self):
        self.bot.store.db.close(); self.env.stop(); self.tmp.cleanup()

    async def cb(self, data, uid=42, kind='private'):
        await self.bot.handle({'callback_query': {'id': 'cb', 'from': {'id': uid}, 'data': data,
                                                 'message': {'chat': {'type': kind}}}})

    async def msg(self, text):
        await self.bot.handle({'message': {'from': {'id': 42}, 'chat': {'type': 'private'}, 'text': text}})

    def case(self, platform='telegram'):
        return {'id': 'case', 'platform': platform, 'target': '@example', 'note': 1, 'files': [],
                'answers': {k: 'I observed this in the chat on the stated date.' for k in self.bot.notes.fields(1)}}

    async def fill_fields(self):
        """Walk the incident-field prompts until the case leaves the fields phase."""
        for _ in range(40):
            phase, _d = self.bot.store.current(42)
            if phase != 'fields':
                return
            await self.msg('I observed this in the chat on the stated date.')
        self.fail('field collection did not terminate')

    async def test_owner_private_only(self):
        await self.cb('platform:telegram', uid=7)
        await self.cb('platform:whatsapp', kind='group')
        self.assertIsNone(self.bot.store.current(42)[1])

    async def test_full_flow_uses_all_ten_senders_test_platform(self):
        await self.cb('platform:telegram'); await self.msg('@example')
        _, d = self.bot.store.current(42)
        await self.cb('group:' + d['id'] + ':0'); await self.cb('note:' + d['id'] + ':1')
        await self.fill_fields()
        await self.msg('/preview')
        phase, d = self.bot.store.current(42)
        self.assertEqual(phase, 'review')
        self.assertEqual(d['recipient'], 'abuse@telegram.org')
        self.assertEqual(len(d['senders']), 10)
        await self.cb('send:' + d['id']); await self.cb('send:' + d['id'])
        self.assertEqual(len(self.bot.mail.sent), 10)
        self.assertEqual({x[1] for x in self.bot.mail.sent}, {'abuse@telegram.org'})
        self.assertGreater(self.bot.mail.peak, 1)
        self.assertEqual(self.bot.store.current(42)[0], 'completed')

    async def test_whatsapp_also_uses_all_ten_senders(self):
        await self.cb('platform:whatsapp'); await self.msg('+2348012345678')
        _, d = self.bot.store.current(42)
        await self.cb('group:' + d['id'] + ':0'); await self.cb('note:' + d['id'] + ':2')
        await self.fill_fields()
        await self.msg('/preview')
        phase, d = self.bot.store.current(42)
        self.assertEqual(phase, 'review')
        self.assertEqual(d['recipient'], 'abuse@whatsapp.com')
        await self.cb('send:' + d['id'])
        self.assertEqual(len(self.bot.mail.sent), 10)

    async def test_every_note_number_uses_all_senders(self):
        for n in range(1, 31):
            d = self.case(); d['note'] = n
            d['answers'] = {k: 'Stated fact.' for k in self.bot.notes.fields(n)}
            _, accounts = self.bot.route(42, d)
            self.assertEqual(len(accounts), 10, n)

    async def test_custom_report_addresses_honoured(self):
        self.bot.cfg.reporting['telegram'] = 'custom@example.com'
        destination, accounts = self.bot.route(42, self.case())
        self.assertEqual(destination, 'custom@example.com')
        self.assertEqual(len(accounts), 10)

    async def test_no_duplicate_blocking_between_cases(self):
        for ident in ('a', 'b'):
            d = self.case(); d['id'] = ident
            await self.bot.preview(42, d)
            await self.bot.submit(42, d)
        self.assertEqual(len(self.bot.mail.sent), 20)

    async def test_stale_destination_still_verified(self):
        d = self.case()
        await self.bot.preview(42, d)
        self.bot.cfg.reporting['telegram'] = 'changed@example.com'
        with self.assertRaises(ValueError):
            await self.bot.submit(42, d)
        self.assertEqual(len(self.bot.mail.sent), 0)

    async def test_uncertain_not_retried(self):
        d = self.case()
        await self.bot.preview(42, d)
        self.bot.mail.result = 'unknown'
        await self.bot.submit(42, d)
        await self.bot.submit(42, d)
        self.assertEqual(len(self.bot.mail.sent), 10)
        self.assertEqual(self.bot.store.current(42)[0], 'unknown')

    async def test_settings_shows_senders_and_warnings(self):
        await self.msg('/settings')
        text = self.messages[-1][0]
        self.assertIn('Senders: 10/10', text)
        self.assertIn('sender1@gmail.com', text)
        self.assertIn('abuse@telegram.org', text)


class GmailTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_auth_and_errors(self):
        class R:
            def __init__(self, s, d): self.status = s; self.d = d
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def json(self): return self.d
        class H:
            def __init__(self): self.auth = 200; self.status = 200; self.timeout = False
            def post(self, url, **kw):
                if url.endswith('/token'):
                    return R(self.auth, {'access_token': 'mock'})
                if self.timeout: raise asyncio.TimeoutError()
                return R(self.status, {'id': 'mock-id'} if self.status == 200 else {})
        class C: client_id = 'id'; client_secret = 'secret'
        h = H(); g = Gmail(h, C()); a = {'email': 'me@gmail.com', 'refresh_token': 'fake'}
        self.assertEqual((await g.send(a, 'test@example.com', 'T', 'B', 'id'))[0], 'submitted')
        h.status = 403
        self.assertEqual((await g.send(a, 'test@example.com', 'T', 'B', 'id'))[0], 'rejected')
        h.timeout = True
        self.assertEqual((await g.send(a, 'test@example.com', 'T', 'B', 'id'))[0], 'unknown')
        h.auth = 400
        with self.assertRaises(AuthError):
            await g.send(a, 'test@example.com', 'T', 'B', 'id')


if __name__ == '__main__':
    unittest.main()
