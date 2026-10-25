"""Configuration, original note-pack rendering, and durable state."""
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = re.compile(r'\{\{([A-Za-z_]+)\}\}')
GROUPS = {
    'Scams & financial deception': list(range(1, 9)),
    'Account security & impersonation': list(range(9, 15)),
    'Harassment, threats & privacy': list(range(15, 25)),
    'Exploitation & dangerous activity': list(range(25, 31)),
}


def email(value):
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}', value):
        raise ValueError('Enter a single plain email address, without a display name.')
    return value


def target(platform, value):
    value = value.strip()
    if platform == 'whatsapp':
        value = re.sub(r'[\s()-]', '', value)
        if not re.fullmatch(r'\+[1-9]\d{7,14}', value):
            raise ValueError('Use an international number starting with +, for example +234… .')
        return value
    if re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{3,31}', value):
        return value
    url = urlsplit(value)
    if url.scheme == 'https' and url.netloc.lower() == 't.me' and re.fullmatch(r'/[A-Za-z0-9_+/-]+', url.path) and not url.query and not url.fragment:
        return value
    raise ValueError('Use an @username or an https://t.me/... account/message link.')


class Config:
    def __init__(self):
        self.token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        self.admins = {int(n.strip()) for n in os.environ.get('ADMIN_IDS', '').split(',') if n.strip()}
        if not self.token or not self.admins:
            raise ValueError('Set TELEGRAM_BOT_TOKEN and ADMIN_IDS.')
        self.client_id = os.environ.get('GMAIL_CLIENT_ID', '')
        self.client_secret = os.environ.get('GMAIL_CLIENT_SECRET', '')
        self.accounts = []
        if os.environ.get('GMAIL_SENDER') and os.environ.get('GMAIL_REFRESH_TOKEN'):
            self.accounts.append({'email': email(os.environ['GMAIL_SENDER']), 'refresh_token': os.environ['GMAIL_REFRESH_TOKEN']})
        extra = json.loads(os.environ.get('GMAIL_EXTRA_ACCOUNTS_JSON', '[]'))
        if not isinstance(extra, list) or len(extra) > 9:
            raise ValueError('GMAIL_EXTRA_ACCOUNTS_JSON must be an array of up to nine backup accounts.')
        for a in extra:
            if not isinstance(a, dict) or not isinstance(a.get('refresh_token'), str) or not a['refresh_token']:
                raise ValueError('Each additional Gmail needs email and refresh_token.')
            self.accounts.append({'email': email(a['email']), 'refresh_token': a['refresh_token'],
                                  'client_id': a.get('client_id', self.client_id), 'client_secret': a.get('client_secret', self.client_secret)})
        if len({a['email'].lower() for a in self.accounts}) != len(self.accounts):
            raise ValueError('Gmail sender addresses must be distinct.')
        self.test_email = os.environ.get('TEST_RECIPIENT_EMAIL', os.environ.get('TEST_EMAIL', '')).strip()
        self.live = {
            'telegram': os.environ.get('TELEGRAM_REPORT_EMAIL', '').strip(),
            'whatsapp': os.environ.get('WHATSAPP_REPORT_EMAIL', '').strip(),
        }
        for value in self.live.values():
            if value:
                email(value)
        if self.test_email:
            self.test_email = email(self.test_email)
            self.validate_test()
        self.data_dir = Path(os.environ.get('DATA_DIR', './data'))
        self.initial_test = os.environ.get('SEND_MODE', 'test') != 'live'

    def validate_test(self):
        if not self.test_email:
            raise ValueError('Set TEST_RECIPIENT_EMAIL to an inbox you control.')
        host = self.test_email.rsplit('@', 1)[1].lower()
        forbidden = ('telegram.org', 'whatsapp.com', 'whatsapp.net')
        if any(host == x or host.endswith('.' + x) for x in forbidden):
            raise ValueError('Test mail cannot go to Telegram or WhatsApp domains.')
        if self.test_email.lower() in {x.lower() for x in self.live.values() if x}:
            raise ValueError('Test and live recipient addresses must be different.')
        return self.test_email

    def primary(self):
        if not self.accounts:
            raise ValueError('Configure your existing GMAIL_SENDER and GMAIL_REFRESH_TOKEN.')
        return self.accounts[0]


class Notes:
    def __init__(self, path=None):
        path = path or Path(__file__).parent / 'assets/report-pack/report_notes.json'
        self.notes = json.loads(Path(path).read_text())['templates']
        if len(self.notes) != 30:
            raise ValueError('Expected the approved 30-note pack.')

    def note(self, number):
        return self.notes[int(number)-1] if 1 <= int(number) <= 30 else None

    def fields(self, number):
        n = self.note(number)
        # Original pack metadata misses names containing capital I; derive from actual text.
        return list(dict.fromkeys(FIELDS.findall(n['subject_template'] + '\n' + n['body_template'])))

    def render(self, d, contact):
        n = self.note(d['note'])
        values = dict(d.get('answers', {}))
        values['account_number'] = d['target']
        values['reporter_contact'] = contact
        if d['test']:
            for key in self.fields(d['note']):
                values.setdefault(key, '[TEST SAMPLE: no incident fact asserted]')
        missing = [k for k in self.fields(d['note']) if not values.get(k, '').strip()]
        if missing:
            raise ValueError('Complete the incident fields before previewing.')
        subject = FIELDS.sub(lambda m: values[m[1]], n['subject_template'])
        body = FIELDS.sub(lambda m: values[m[1]], n['body_template'])
        if d['platform'] == 'telegram':
            body = body.replace('To WhatsApp’s review team,', 'To Telegram’s review team,')
            body = body.replace('Account concerned:', 'Telegram account or content concerned:')
        if d['test']:
            subject = '[KANA NOTES TEST ONLY] ' + subject
            body = ('TEST ONLY — This is a template/transport test, not an allegation or a takedown request. '
                    'Do not forward to a platform abuse team. Any displayed identifier is test input; '
                    'no wrongdoing by its owner is asserted.\n\n' + body)
        body += '\nPrepared with KANA NOTES.\n'
        if '{{' in body or '{{' in subject:
            raise ValueError('Unresolved template marker; correct the input.')
        return subject, body


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,owner INTEGER,phase TEXT,data TEXT,updated REAL);
        CREATE TABLE IF NOT EXISTS settings(owner INTEGER PRIMARY KEY,data TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS attempts(case_id TEXT,sender TEXT,state TEXT,message_id TEXT,PRIMARY KEY(case_id,sender));
        ''')
        self.db.execute("UPDATE cases SET phase='unknown' WHERE phase='sending'")
        self.db.execute("UPDATE attempts SET state='unknown' WHERE state='sending'")
        self.db.commit()

    def prefs(self, owner, initial=True):
        row = self.db.execute('SELECT data FROM settings WHERE owner=?', (owner,)).fetchone()
        return json.loads(row[0]) if row else {'test': initial, 'multi': False}

    def setprefs(self, owner, p):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (owner, json.dumps(p)))
        self.db.commit()

    def save(self, owner, phase, d):
        self.db.execute('INSERT INTO cases VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET phase=excluded.phase,data=excluded.data,updated=excluded.updated',
                        (d['id'], owner, phase, json.dumps(d), time.time()))
        self.db.commit()

    def current(self, owner):
        row = self.db.execute('SELECT phase,data FROM cases WHERE owner=? ORDER BY updated DESC LIMIT 1', (owner,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def claim(self, owner, ident):
        cur = self.db.execute("UPDATE cases SET phase='sending',updated=? WHERE id=? AND owner=? AND phase='review'", (time.time(), ident, owner))
        self.db.commit()
        return cur.rowcount == 1

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def attempt(self, case, sender, state, ident=''):
        self.db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?)', (case, sender, state, ident))
        self.db.commit()

    def attempts(self, ident):
        return self.db.execute('SELECT sender,state,message_id FROM attempts WHERE case_id=?', (ident,)).fetchall()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()
