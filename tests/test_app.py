import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch
from app import Store, Bot, complaint, phone, selections

class CoreTests(unittest.TestCase):
    def test_phone_and_selection_validation(self):
        self.assertEqual(phone('+234 801 234 5678'), '+2348012345678')
        self.assertEqual(selections('1, 2,1,4'), [1,2,4])
        for value in ('', '0', '1,9', '1,abc'):
            with self.assertRaises(ValueError): selections(value)
        with self.assertRaises(ValueError): phone('08012345678')

    def test_complaint_preserves_facts_without_inventing_allegations(self):
        text = complaint({'id':'case', 'phone':'+2348012345678', 'reply':'me@example.com',
                          'categories':[1,4], 'details':'I received an unsolicited money request.', 'files':[]})
        self.assertIn('I received an unsolicited money request.', text)
        self.assertIn('allegations for review', text)
        self.assertNotIn('committed human trafficking', text)

    def test_atomic_send_and_crash_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            path = root + '/state.db'
            store = Store(path)
            store.save(1, 'review', {'id':'abc'})
            self.assertFalse(store.claim(2, 'abc'))
            self.assertTrue(store.claim(1, 'abc'))
            self.assertFalse(store.claim(1, 'abc'))
            store.offset(99)
            store.db.close()
            reopened = Store(path)
            self.assertEqual(reopened.current(1)[0], 'unknown')
            self.assertEqual(reopened.get_offset(), 99)
            reopened.db.close()

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'fake', 'ADMIN_IDS':'42', 'DATA_DIR':self.tmp.name, 'SEND_MODE':'test'})
        self.env.start()
        self.bot = Bot()
        self.messages = []
        async def say(uid, text, buttons=None): self.messages.append(text)
        self.bot.say = say
    async def asyncTearDown(self):
        self.bot.store.db.close()
        self.env.stop()
        self.tmp.cleanup()
    async def message(self, text, uid=42, chat_type='private'):
        await self.bot.handle({'message':{'from':{'id':uid}, 'chat':{'type':chat_type}, 'text':text}})
    async def test_unauthorized_and_group_access(self):
        await self.message('/new', uid=7)
        await self.message('/new', chat_type='group')
        self.assertIsNone(self.bot.store.current(42)[1])
    async def test_draft_flow_and_evidence_required(self):
        for text in ['/new', '+2348012345678', 'me@example.com', '1,2', 'On Monday I received threatening requests for money in this chat.']:
            await self.message(text)
        state, d = self.bot.store.current(42)
        self.assertEqual(state, 'evidence')
        await self.message('/preview')
        self.assertIn('at least one genuine', self.messages[-1])
        await self.bot.attach(42, d, b'\xff\xd8\xfftest', 'test.jpg', '')
        with self.assertRaises(ValueError):
            await self.bot.attach(42, d, b'\xff\xd8\xfftest', 'again.jpg', '')
        with self.assertRaises(ValueError):
            await self.message('/attach ../../.env')
    async def test_own_account_requires_explicit_declaration(self):
        d = {'id':'own', 'phone':'+2348012345678', 'reply':'me@example.com', 'categories':[8], 'details':'My own phone was stolen yesterday.', 'files':[]}
        self.bot.store.save(42,'evidence',d)
        await self.bot.preview(42,d)
        self.assertEqual(self.bot.store.current(42)[0], 'evidence')
        await self.message('/ownaccount')
        await self.message('/preview')
        self.assertEqual(self.bot.store.current(42)[0], 'review')
        self.assertIn('TEST', self.messages[-2])

class MailTests(unittest.IsolatedAsyncioTestCase):
    async def test_gmail_routing_mime_and_duplicate_protection(self):
        from email import policy
        from email.parser import BytesParser
        import base64
        class Response:
            status = 200
            def __init__(self, result): self.result = result
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self): return self.result
        class HTTP:
            def __init__(self): self.calls = []; self.break_send = False; self.auth_fail = False
            def post(self, url, **kw):
                self.calls.append((url,kw))
                if url.endswith('/token'):
                    r = Response({'access_token':'fake'})
                    if self.auth_fail: r.status = 400
                    return r
                if self.break_send: raise asyncio.TimeoutError()
                return Response({'id':'gmail-test-id'})
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
            'TELEGRAM_BOT_TOKEN':'fake','ADMIN_IDS':'42','DATA_DIR':root,'SEND_MODE':'test',
            'TEST_EMAIL':'mine@example.com','GMAIL_CLIENT_ID':'fake','GMAIL_CLIENT_SECRET':'fake',
            'GMAIL_REFRESH_TOKEN':'fake','GMAIL_SENDER':'mine@gmail.com'}):
            bot = Bot(); bot.http = HTTP()
            async def say(*a,**kw): pass
            bot.say = say
            d = {'id':'case','reply':'reply@example.com','phone':'+2348012345678','body':'Reviewed text','files':[
                {'filename':'test.pdf','mime':'application/pdf','content':base64.b64encode(b'%PDF-test').decode()}]}
            bot.store.save(42,'review',d)
            await bot.send(42,d); await bot.send(42,d)
            sends = [kw for url,kw in bot.http.calls if url.endswith('/send')]
            self.assertEqual(len(sends),1)
            message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(sends[0]['json']['raw']))
            self.assertEqual(str(message['To']),'mine@example.com')
            self.assertEqual(str(message['Reply-To']),'reply@example.com')
            self.assertEqual(list(message.iter_attachments())[0].get_payload(decode=True),b'%PDF-test')
            self.assertEqual(bot.store.current(42)[0],'submitted')
            bot.mode='live'; d=dict(d,id='live')
            bot.store.save(42,'review',d); await bot.send(42,d)
            message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(bot.http.calls[-1][1]['json']['raw']))
            self.assertEqual(str(message['To']),'support@whatsapp.com, abuse@whatsapp.com')
            bot.http.auth_fail=True; d=dict(d,id='auth')
            bot.store.save(42,'review',d); await bot.send(42,d)
            self.assertEqual(bot.store.current(42)[0],'review')
            bot.http.auth_fail=False; bot.http.break_send=True; d=dict(d,id='unknown')
            bot.store.save(42,'review',d); await bot.send(42,d)
            self.assertEqual(bot.store.current(42)[0],'unknown')
            self.assertFalse(bot.store.claim(42,'unknown'))
            bot.store.db.close()

if __name__ == '__main__': unittest.main()
