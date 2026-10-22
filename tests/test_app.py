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
    async def test_test_routing_duplicate_taps_and_uncertain_outcome(self):
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self): return {'id':'mock-provider-id'}
        class HTTP:
            def __init__(self): self.calls = []
            def post(self, url, **kw):
                self.calls.append(kw)
                return Response()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
            'TELEGRAM_BOT_TOKEN':'fake', 'ADMIN_IDS':'42', 'DATA_DIR':root,
            'SEND_MODE':'test', 'TEST_EMAIL':'mine@example.com',
            'RESEND_API_KEY':'fake', 'MAIL_FROM':'reports@example.com'}):
            bot = Bot()
            bot.http = HTTP()
            async def say(*args, **kw): pass
            bot.say = say
            d = {'id':'test-case', 'reply':'me@example.com', 'phone':'+2348012345678', 'body':'Reviewed complaint', 'files':[]}
            bot.store.save(42,'review',d)
            await bot.send(42,d)
            await bot.send(42,d)
            self.assertEqual(len(bot.http.calls),1)
            self.assertEqual(bot.http.calls[0]['json']['to'],['mine@example.com'])
            self.assertEqual(bot.store.current(42)[0],'submitted')
            bot.mode = 'live'
            d = dict(d, id='live-case')
            bot.store.save(42,'review',d)
            await bot.send(42,d)
            self.assertEqual(bot.http.calls[-1]['json']['to'],['support@whatsapp.com','abuse@whatsapp.com'])
            self.assertIn('Idempotency-Key',bot.http.calls[-1]['headers'])
            class BrokenHTTP:
                def post(self,*args,**kw): raise asyncio.TimeoutError()
            bot.http = BrokenHTTP()
            d = dict(d,id='uncertain-case')
            bot.store.save(42,'review',d)
            await bot.send(42,d)
            self.assertEqual(bot.store.current(42)[0],'unknown')
            self.assertFalse(bot.store.claim(42,d['id']))
            bot.store.db.close()

if __name__ == '__main__': unittest.main()
