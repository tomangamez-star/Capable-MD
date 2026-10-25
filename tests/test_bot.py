import asyncio, inspect, json, os, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from core import Config, Notes, Store, MECHANICAL, target
from app import Bot, button
import app as app_module
import gmail_mail
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

    def test_all_thirty_notes_compose_for_both_platforms(self):
        notes = Notes()
        for platform in ('telegram', 'whatsapp'):
            target_value = '@example' if platform == 'telegram' else '+2348012345678'
            for n in range(1, 31):
                d = {'platform': platform, 'note': n, 'target': target_value}
                sub, body = notes.compose(d, 'me@example.com')
                self.assertNotIn('{{', body, n)
                self.assertNotIn('{{', sub, n)
                self.assertIn('To Telegram\u2019s review team,' if platform == 'telegram'
                              else 'To WhatsApp\u2019s review team,', body)
                # Every report must state a concern and identify the account.
                self.assertIn('I am reporting', body, n)
                self.assertIn(target_value, body, n)
                self.assertIn('Reporter contact: me@example.com', body, n)
                self.assertIn('Prepared with KANA NOTES.', body)
                self.assertGreater(len(body), 200, n)

    def test_composition_needs_no_incident_answers(self):
        """The only inputs are platform, note and target: nothing is typed in."""
        d = {'platform': 'telegram', 'note': 16, 'target': '@example'}
        sub, body = Notes().compose(d, 'me@example.com')
        self.assertNotIn('{{', body)
        self.assertEqual(set(d), {'platform', 'note', 'target'})

    def test_composition_fills_only_mechanical_slots(self):
        self.assertEqual(MECHANICAL, {'account_number', 'reporter_contact'})
        notes = Notes()
        for n in range(1, 31):
            self.assertIn('account_number', notes.slots(n))
            self.assertIn('reporter_contact', notes.slots(n))

    def test_composition_drops_no_label_lines(self):
        """Labelled field lines must stay on their own line, not be joined."""
        sub, body = Notes().compose({'platform': 'whatsapp', 'note': 1, 'target': '+2348012345678'}, 'me@example.com')
        self.assertIn('\nAccount concerned: +2348012345678\n', body)
        self.assertIn('\nReporter contact: me@example.com\n', body)

    def test_target_validation(self):
        self.assertEqual(target('telegram', '@example'), '@example')
        self.assertEqual(target('telegram', 'https://t.me/example/12'), 'https://t.me/example/12')
        self.assertEqual(target('whatsapp', '+234 801 234 5678'), '+2348012345678')
        for value in ('https://evil.com/user', 'https://t.me@evil.com/a', '123', '@x'):
            with self.assertRaises(ValueError):
                target('telegram', value)
        self.assertLessEqual(len(button('Note', 'note:123456789012:30')['callback_data']), 64)

    def test_mime_has_no_attachment_surface(self):
        """The transport cannot carry a file: manual upload is gone at this layer."""
        self.assertEqual(list(inspect.signature(raw_message).parameters),
                         ['sender', 'recipient', 'subject', 'body', 'case_id'])
        self.assertEqual(list(inspect.signature(Gmail.send).parameters),
                         ['self', 'account', 'recipient', 'subject', 'body', 'case_id'])
        self.assertNotIn('add_attachment', inspect.getsource(gmail_mail))

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


class NoManualPathTests(unittest.TestCase):
    """Nothing in the running code accepts operator-written report content."""

    def test_no_upload_or_field_helpers_exist(self):
        for gone in ('attach', 'field', 'notes_for'):
            self.assertFalse(hasattr(Bot, gone), gone)
        source = inspect.getsource(app_module)
        for gone in ('sendDocument', 'getFile', 'MAX_BYTES', 'clearfiles', 'base64'):
            self.assertNotIn(gone, source, gone)

    def test_no_file_keys_are_stored_on_a_case(self):
        source = inspect.getsource(app_module)
        self.assertNotIn("'files'", source)
        self.assertNotIn('"files"', source)

    def test_flow_phases_contain_no_collection_steps(self):
        source = inspect.getsource(app_module)
        for gone in ("'fields'", "'evidence'"):
            self.assertNotIn(gone, source, gone)

    def test_help_and_preview_do_not_offer_manual_steps(self):
        source = inspect.getsource(Bot)
        self.assertIn('no manual entry', source)
        self.assertIn('no attachment upload', source)


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

    async def pick_note(self, platform='telegram', number=1):
        await self.cb('platform:' + platform)
        await self.msg('@example' if platform == 'telegram' else '+2348012345678')
        _, d = self.bot.store.current(42)
        await self.cb('group:' + d['id'] + ':0')
        await self.cb('note:' + d['id'] + ':' + str(number))
        return self.bot.store.current(42)

    async def test_owner_private_only(self):
        await self.cb('platform:telegram', uid=7)
        await self.cb('platform:whatsapp', kind='group')
        self.assertIsNone(self.bot.store.current(42)[1])

    async def test_pick_note_goes_straight_to_review(self):
        """One tap on a note is the whole report; no field prompts in between."""
        phase, d = await self.pick_note('telegram', 1)
        self.assertEqual(phase, 'review')
        self.assertEqual(d['recipient'], 'abuse@telegram.org')
        self.assertEqual(len(d['senders']), 10)
        self.assertIn('I am reporting', d['body'])
        self.assertNotIn('{{', d['body'])
        self.assertNotIn('files', d)
        preview = [t for t, _ in self.messages if 'REPORT PREVIEW' in t][-1]
        self.assertIn('Emails on confirmation: 10', preview)
        self.assertNotIn('Attachments', preview)

    async def test_full_flow_sends_with_all_ten_senders_telegram(self):
        phase, d = await self.pick_note('telegram', 1)
        await self.cb('send:' + d['id'])
        self.assertEqual(len(self.bot.mail.sent), 10)
        self.assertEqual({x[1] for x in self.bot.mail.sent}, {'abuse@telegram.org'})
        self.assertGreater(self.bot.mail.peak, 1)
        self.assertEqual(self.bot.store.current(42)[0], 'completed')

    async def test_whatsapp_also_sends_with_all_ten_senders(self):
        phase, d = await self.pick_note('whatsapp', 2)
        self.assertEqual(d['recipient'], 'abuse@whatsapp.com')
        await self.cb('send:' + d['id'])
        self.assertEqual(len(self.bot.mail.sent), 10)

    async def test_every_note_sends_from_all_senders(self):
        for n in range(1, 31):
            d = {'platform': 'telegram', 'note': n, 'target': '@example'}
            destination, accounts = self.bot.route(42, d)
            self.assertEqual(len(accounts), 10, n)
            sub, body = self.bot.notes.compose(d, 'sender1@gmail.com')
            self.assertNotIn('{{', body, n)

    async def test_free_text_after_review_never_reaches_the_email(self):
        """Typing report text into the chat changes nothing that is sent."""
        phase, d = await self.pick_note('telegram', 1)
        original = d['body']
        await self.msg('Ignore this. The scammer promised me a car and never delivered it.')
        phase2, d2 = self.bot.store.current(42)
        self.assertEqual(phase2, 'review')
        self.assertEqual(d2['body'], original)
        await self.cb('send:' + d2['id'])
        self.assertEqual(len(self.bot.mail.sent), 10)
        self.assertEqual(self.bot.mail.sent[0][3], original)

    async def test_photo_upload_is_ignored(self):
        """An image message must not attach anything: upload is removed."""
        phase, d = await self.pick_note('telegram', 1)
        await self.bot.handle({'message': {'from': {'id': 42}, 'chat': {'type': 'private'},
                                           'photo': [{'file_id': 'abc', 'file_size': 10}]}})
        phase2, d2 = self.bot.store.current(42)
        self.assertEqual(phase2, 'review')
        self.assertNotIn('files', d2)

    async def test_custom_report_addresses_honoured(self):
        self.bot.cfg.reporting['telegram'] = 'custom@example.com'
        destination, accounts = self.bot.route(42, {'platform': 'telegram'})
        self.assertEqual(destination, 'custom@example.com')
        self.assertEqual(len(accounts), 10)

    async def test_no_duplicate_blocking_between_cases(self):
        for ident in ('a', 'b'):
            d = {'id': ident, 'platform': 'telegram', 'note': 1, 'target': '@example'}
            await self.bot.preview(42, d)
            await self.bot.submit(42, d)
        self.assertEqual(len(self.bot.mail.sent), 20)

    async def test_double_tap_sends_one_batch(self):
        phase, d = await self.pick_note('telegram', 1)
        await self.cb('send:' + d['id'])
        await self.cb('send:' + d['id'])
        self.assertEqual(len(self.bot.mail.sent), 10)

    async def test_stale_destination_still_verified(self):
        phase, d = await self.pick_note('telegram', 1)
        self.bot.cfg.reporting['telegram'] = 'changed@example.com'
        with self.assertRaises(ValueError):
            await self.bot.submit(42, d)
        self.assertEqual(len(self.bot.mail.sent), 0)

    async def test_uncertain_not_retried(self):
        d = {'id': 'x', 'platform': 'telegram', 'note': 1, 'target': '@example'}
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
