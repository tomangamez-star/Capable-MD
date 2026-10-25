"""Configuration, original note-pack composition, and durable state."""
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

# Official abuse addresses used when no override is supplied. A configured
# address is only ever a routing preference: it can never stop the bot sending.
DEFAULT_REPORTS = {'telegram': 'abuse@telegram.org', 'whatsapp': 'abuse@whatsapp.com'}
MAX_SENDERS = 10

# The only slots this bot can fill without asking the operator for anything: the
# account under report, and the first sender as the reply address. Every other
# slot in the pack describes an incident fact, and facts are never typed in.
MECHANICAL = frozenset({'account_number', 'reporter_contact'})


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
    """Configuration with no address-based gating.

    Every configured sender is used for every report type. No value in the
    environment can prevent boot or sending: an unusable entry is skipped, a
    missing report address falls back to the official default, and both are
    recorded in ``warnings`` for /settings. Credentials are still mandatory —
    without a bot token and an admin there is nothing to run.
    """

    def __init__(self):
        self.warnings = []
        self.token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        self.admins = {int(n.strip()) for n in os.environ.get('ADMIN_IDS', '').split(',') if n.strip()}
        if not self.token or not self.admins:
            raise ValueError('Set TELEGRAM_BOT_TOKEN and ADMIN_IDS.')
        self.client_id = os.environ.get('GMAIL_CLIENT_ID', '')
        self.client_secret = os.environ.get('GMAIL_CLIENT_SECRET', '')

        raw = []
        if os.environ.get('GMAIL_SENDER') and os.environ.get('GMAIL_REFRESH_TOKEN'):
            raw.append({'email': os.environ['GMAIL_SENDER'],
                        'refresh_token': os.environ['GMAIL_REFRESH_TOKEN']})
        extra_env = (os.environ.get('GMAIL_EXTRA_ACCOUNTS_JSON') or '').strip() or '[]'
        try:
            extra = json.loads(extra_env)
        except json.JSONDecodeError as exc:
            raise ValueError('GMAIL_EXTRA_ACCOUNTS_JSON must be valid JSON.') from exc
        if not isinstance(extra, list):
            raise ValueError('GMAIL_EXTRA_ACCOUNTS_JSON must be a JSON array of sender accounts.')
        for a in extra:
            if not isinstance(a, dict) or not isinstance(a.get('refresh_token'), str) or not a['refresh_token']:
                self.warnings.append('Skipped a GMAIL_EXTRA_ACCOUNTS_JSON entry with no refresh_token.')
                continue
            raw.append(a)

        accounts, seen = [], set()
        for a in raw:
            try:
                address = email(a['email'])
            except (KeyError, TypeError, ValueError):
                self.warnings.append('Skipped a sender whose address is not a plain email address.')
                continue
            if address.lower() in seen:
                self.warnings.append('Skipped duplicate sender ' + address + '.')
                continue
            seen.add(address.lower())
            accounts.append({'email': address, 'refresh_token': a['refresh_token'],
                             'client_id': a.get('client_id', self.client_id),
                             'client_secret': a.get('client_secret', self.client_secret)})
        if len(accounts) > MAX_SENDERS:
            self.warnings.append(
                f'{len(accounts)} senders configured; using the first {MAX_SENDERS} for every report.')
        self.accounts = accounts[:MAX_SENDERS]
        if not self.accounts:
            self.warnings.append('No Gmail sender configured; reports cannot be sent until one is set.')

        # Routing only. A malformed or missing override never blocks sending.
        self.reporting = {}
        for platform, fallback in DEFAULT_REPORTS.items():
            key = platform.upper() + '_REPORT_EMAIL'
            value = (os.environ.get(key) or '').strip()
            if not value:
                self.reporting[platform] = fallback
                continue
            try:
                self.reporting[platform] = email(value)
            except ValueError:
                self.warnings.append(key + ' is not a valid address; using ' + fallback + '.')
                self.reporting[platform] = fallback

        self.data_dir = Path(os.environ.get('DATA_DIR', './data'))

    def senders(self):
        """Every configured sender, used for every report type."""
        return list(self.accounts)


class Notes:
    """The approved 30-note pack, composed without any manual report text.

    The pack's templates interleave fixed report wording with incident-detail
    slots such as ``{{promised_item_or_service}}``. This bot collects no free
    text, uploads no files and has no compose mode, so a slot cannot be filled.
    Rather than invent a fact or ship an unfilled marker, composition keeps
    every sentence whose slots the bot fills mechanically (the account under
    report and the reply address) and drops the sentences that would need
    operator-supplied detail. The result is always grammatical, always true to
    what was actually supplied, and never asserts an unverified fact.
    """

    def __init__(self, path=None):
        path = path or Path(__file__).parent / 'assets/report-pack/report_notes.json'
        self.notes = json.loads(Path(path).read_text())['templates']
        if len(self.notes) != 30:
            raise ValueError('Expected the approved 30-note pack.')

    def note(self, number):
        return self.notes[int(number)-1] if 1 <= int(number) <= 30 else None

    def slots(self, number):
        n = self.note(number)
        # Original pack metadata misses names containing capital I; derive from actual text.
        return list(dict.fromkeys(FIELDS.findall(n['subject_template'] + '\n' + n['body_template'])))

    @staticmethod
    def _mechanical(sentence):
        """True when every slot in this sentence is one the bot can fill itself."""
        return set(FIELDS.findall(sentence)) <= MECHANICAL

    @staticmethod
    def _fill(text, values):
        return FIELDS.sub(lambda m: values.get(m[1], ''), text)

    def _body(self, template, values):
        """Keep the pack's fixed wording; drop sentences that need incident facts.

        Line structure is preserved, because the pack places one labelled field
        per line and joining a whole paragraph would run the labels together.
        """
        paragraphs = []
        for block in template.split('\n\n'):
            lines = []
            for line in block.split('\n'):
                kept = [p for p in re.split(r'(?<=\.)\s+', line.strip()) if self._mechanical(p)]
                if kept:
                    lines.append(' '.join(self._fill(p, values) for p in kept))
            if lines:
                paragraphs.append('\n'.join(lines))
        return '\n\n'.join(paragraphs)

    @staticmethod
    def _concern(display_name):
        """A truthful one-line statement of the reported category.

        Taken from the pack's own note name, so it reports the category the
        operator chose and never asserts an incident fact nobody supplied.
        """
        phrase = display_name.lower()
        article = 'an' if phrase[0] in 'aeiou' else 'a'
        return f'I am reporting the account identified below for {article} {phrase}.'

    def compose(self, d, contact):
        """Build the exact outgoing message. No operator text, no files."""
        n = self.note(d['note'])
        values = {'account_number': d['target'], 'reporter_contact': contact}
        subject = self._fill(n['subject_template'], values).strip()
        if not subject or '{{' in subject:
            subject = 'Request for review: ' + n['display_name'] + ' — ' + d['target']
        body = self._body(n['body_template'], values)
        if not re.search(r'\bI am reporting\b', body):
            # A few notes lead with a sentence that only reads correctly beside
            # incident detail. Without a stated concern the report is unusable,
            # so state the category right after the salutation.
            concern = self._concern(n['display_name'])
            head, sep, tail = body.partition('\n\n')
            body = head + sep + concern + sep + tail if sep else concern + sep + body
        if d['platform'] == 'telegram':
            body = body.replace('To WhatsApp’s review team,', 'To Telegram’s review team,')
            body = body.replace('Account concerned:', 'Telegram account or content concerned:')
        body += '\nPrepared with KANA NOTES.\n'
        if '{{' in body or not body.strip():
            raise ValueError('Note ' + str(d['note']) + ' composed to an unusable report.')
        return subject, body


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,owner INTEGER,phase TEXT,data TEXT,updated REAL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS attempts(case_id TEXT,sender TEXT,state TEXT,message_id TEXT,PRIMARY KEY(case_id,sender));
        ''')
        self.db.execute("UPDATE cases SET phase='unknown' WHERE phase='sending'")
        self.db.execute("UPDATE attempts SET state='unknown' WHERE state='sending'")
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
