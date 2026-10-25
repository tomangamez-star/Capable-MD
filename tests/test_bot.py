import asyncio, base64, os, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from email import policy
from email.parser import BytesParser
from core import Config, Notes, Store, digest, target
from app import Bot, button
from gmail_mail import Gmail, raw_message, AuthError

class CoreTests(unittest.TestCase):
 def test_original_pack_bytes(self):
  root=Path(__file__).resolve().parents[1]/'assets'
  with zipfile.ZipFile(root/'original-report-notes.zip') as z:
   for n in z.namelist(): self.assertEqual(z.read(n),(root/'report-pack'/n).read_bytes())
 def test_all_templates_for_both_platforms(self):
  notes=Notes()
  for platform in ('telegram','whatsapp'):
   for n in range(1,31):
    d={'platform':platform,'note':n,'target':'@example','test':True}
    sub,body=notes.render(d,'me@example.com')
    self.assertIn('TEST ONLY',sub); self.assertNotIn('{{',body)
    self.assertIn('To Telegram’s review team,' if platform=='telegram' else 'To WhatsApp’s review team,',body)
    d['test']=False
    with self.assertRaises(ValueError): notes.render(d,'me@example.com')
    d['answers']={k:'Actual facts supplied' for k in notes.fields(n)}
    self.assertNotIn('{{',notes.render(d,'me@example.com')[1])
  self.assertIn('what_I_paid_or_was_asked_to_pay',notes.fields(1))
 def test_target_validation(self):
  self.assertEqual(target('telegram','@example'),'@example')
  self.assertEqual(target('telegram','https://t.me/example/12'),'https://t.me/example/12')
  self.assertEqual(target('whatsapp','+234 801 234 5678'),'+2348012345678')
  for value in ('https://evil.com/user','https://t.me@evil.com/a','123','@x'):
   with self.assertRaises(ValueError): target('telegram',value)
  self.assertLessEqual(len(button('Note','note:123456789012:30')['callback_data']),64)
 def test_mime_attachment_bytes(self):
  f={'filename':'proof.pdf','mime':'application/pdf','content':base64.b64encode(b'%PDF-proof').decode()}
  raw=raw_message('me@gmail.com','test@example.com','Title','Body','case',[f])
  m=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
  self.assertEqual(str(m['To']),'test@example.com')
  self.assertEqual(list(m.iter_attachments())[0].get_payload(decode=True),b'%PDF-proof')
 def test_crash_and_claim(self):
  with tempfile.TemporaryDirectory() as td:
   s=Store(Path(td)/'db'); s.save(42,'review',{'id':'x'})
   self.assertFalse(s.claim(7,'x')); self.assertTrue(s.claim(42,'x')); self.assertFalse(s.claim(42,'x'))
   s.attempt('x','me@example.com','sending'); s.db.close(); s=Store(Path(td)/'db')
   self.assertEqual(s.current(42)[0],'unknown'); self.assertEqual(s.attempts('x')[0][1],'unknown'); s.db.close()

class BotTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'fake','ADMIN_IDS':'42','GMAIL_CLIENT_ID':'fake','GMAIL_CLIENT_SECRET':'fake',
   'GMAIL_SENDER':'primary@gmail.com','GMAIL_REFRESH_TOKEN':'fake','TEST_RECIPIENT_EMAIL':'mytest@example.com',
   'TELEGRAM_REPORT_EMAIL':'abuse@telegram.org','WHATSAPP_REPORT_EMAIL':'abuse@whatsapp.com','DATA_DIR':self.tmp.name,
   'SEND_MODE':'test','GMAIL_EXTRA_ACCOUNTS_JSON':'[]'})
  self.env.start(); self.bot=Bot(); self.messages=[]
  async def say(uid,text,rows=None): self.messages.append((text,rows))
  async def tg(*a,**kw): return True
  self.bot.say=say; self.bot.tg=tg
  class Mail:
   def __init__(self): self.sent=[]; self.result='submitted'; self.active=0; self.peak=0; self.auth_fail=False
   async def send(self,*args):
    self.sent.append(args)
    if self.auth_fail: raise AuthError()
    self.active+=1; self.peak=max(self.peak,self.active); await asyncio.sleep(0); self.active-=1
    return self.result,'mock-id'
  self.bot.mail=Mail()
 async def asyncTearDown(self):
  self.bot.store.db.close(); self.env.stop(); self.tmp.cleanup()
 async def cb(self,data,uid=42,kind='private'):
  await self.bot.handle({'callback_query':{'id':'cb','from':{'id':uid},'data':data,'message':{'chat':{'type':kind}}}})
 async def msg(self,text):
  await self.bot.handle({'message':{'from':{'id':42},'chat':{'type':'private'},'text':text}})
 def case(self,test=True,multi=False):
  return {'id':'case','test':test,'multi':multi,'platform':'telegram','target':'@example','note':1,'files':[],
   'answers':{k:'I observed this in the chat on the stated date.' for k in self.bot.notes.fields(1)}}
 def verify(self): self.bot.store.put('verified:42',digest(self.bot.cfg.test_email.lower()))
 async def test_owner_private_only(self):
  await self.cb('platform:telegram',uid=7); await self.cb('platform:whatsapp',kind='group')
  self.assertIsNone(self.bot.store.current(42)[1])
 async def test_quick_test_flow(self):
  self.verify(); await self.cb('platform:telegram'); await self.msg('@example')
  _,d=self.bot.store.current(42); await self.cb('group:'+d['id']+':0'); await self.cb('note:'+d['id']+':1')
  phase,d=self.bot.store.current(42); self.assertEqual(phase,'review'); self.assertIn('TEST ONLY',d['body'])
  self.assertEqual(d['recipient'],'mytest@example.com'); self.assertEqual(len(self.bot.mail.sent),0)
  await self.cb('send:'+d['id']); await self.cb('send:'+d['id']); self.assertEqual(len(self.bot.mail.sent),1)
 async def test_inbox_verification(self):
  with self.assertRaises(ValueError): self.bot.route(42,self.case())
  await self.bot.verify(42); code=self.bot.mail.sent[-1][3].split('code: ')[1].split('\n')[0]
  with self.assertRaises(ValueError): await self.bot.verify_code(42,'wrong')
  await self.bot.verify_code(42,code); self.assertTrue(self.bot.test_verified(42))
  self.bot.cfg.test_email='changed@example.com'; self.assertFalse(self.bot.test_verified(42))
 async def test_forbidden_test_destinations(self):
  for a in ('abuse@telegram.org','report@support.whatsapp.com','a@whatsapp.net'):
   self.bot.cfg.test_email=a
   with self.assertRaises(ValueError): self.bot.cfg.validate_test()
  self.bot.cfg.test_email='other@example.com'; self.bot.cfg.live['telegram']='other@example.com'
  with self.assertRaises(ValueError): self.bot.cfg.validate_test()
 async def test_ten_senders_only_to_verified_test(self):
  self.verify(); self.bot.cfg.accounts=[{'email':f'sender{i}@gmail.com','refresh_token':'fake'} for i in range(10)]
  self.bot.store.setprefs(42,{'test':True,'multi':True}); d=self.case(multi=True)
  await self.bot.preview(42,d); await self.bot.submit(42,d); await self.bot.submit(42,d)
  self.assertEqual(len(self.bot.mail.sent),10); self.assertEqual({x[1] for x in self.bot.mail.sent},{'mytest@example.com'})
  self.assertGreater(self.bot.mail.peak,1)
 async def test_live_single_sender_and_duplicate_block(self):
  self.bot.store.setprefs(42,{'test':False,'multi':True}); d=self.case(test=False)
  await self.bot.preview(42,d); self.assertEqual(d['senders'],['primary@gmail.com']); self.assertEqual(d['recipient'],'abuse@telegram.org')
  await self.bot.submit(42,d); self.assertEqual(len(self.bot.mail.sent),1)
  d=self.case(test=False); d['id']='new'
  with self.assertRaises(ValueError): await self.bot.preview(42,d)
  d['target']='@elsewhere'; d['answers']={}
  with self.assertRaises(ValueError): await self.bot.preview(42,d)
 async def test_stale_mode_and_destination_preview(self):
  self.verify(); d=self.case(); await self.bot.preview(42,d)
  self.bot.store.setprefs(42,{'test':False,'multi':False})
  with self.assertRaises(ValueError): await self.bot.submit(42,d)
  self.bot.store.setprefs(42,{'test':True,'multi':False}); self.bot.cfg.test_email='changed@example.com'; self.verify()
  with self.assertRaises(ValueError): await self.bot.submit(42,d)
  self.assertEqual(len(self.bot.mail.sent),0)
 async def test_uncertain_not_retried(self):
  self.verify(); d=self.case(); await self.bot.preview(42,d); self.bot.mail.result='unknown'
  await self.bot.submit(42,d); await self.bot.submit(42,d)
  self.assertEqual(len(self.bot.mail.sent),1); self.assertEqual(self.bot.store.current(42)[0],'unknown')
 async def test_multi_toggle_rejected_live(self):
  self.bot.store.setprefs(42,{'test':False,'multi':False})
  with self.assertRaises(ValueError): await self.cb('toggle:multi')

class GmailTests(unittest.IsolatedAsyncioTestCase):
 async def test_transport_auth_and_errors(self):
  class R:
   def __init__(self,s,d): self.status=s; self.d=d
   async def __aenter__(self): return self
   async def __aexit__(self,*a): pass
   async def json(self): return self.d
  class H:
   def __init__(self): self.auth=200; self.status=200; self.timeout=False
   def post(self,url,**kw):
    if url.endswith('/token'): return R(self.auth,{'access_token':'mock'})
    if self.timeout: raise asyncio.TimeoutError()
    return R(self.status,{'id':'mock-id'} if self.status==200 else {})
  class C: client_id='id'; client_secret='secret'
  h=H(); g=Gmail(h,C()); a={'email':'me@gmail.com','refresh_token':'fake'}
  self.assertEqual((await g.send(a,'test@example.com','T','B','id'))[0],'submitted')
  h.status=403; self.assertEqual((await g.send(a,'test@example.com','T','B','id'))[0],'rejected')
  h.timeout=True; self.assertEqual((await g.send(a,'test@example.com','T','B','id'))[0],'unknown')
  h.auth=400
  with self.assertRaises(AuthError): await g.send(a,'test@example.com','T','B','id')

if __name__=='__main__': unittest.main()
