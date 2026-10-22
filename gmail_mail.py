"""Gmail send-only OAuth transport. Never retries message submissions."""
import base64
import os
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate

import aiohttp


class GmailAuthError(Exception):
    pass


def build_raw(case, recipients, sender):
    msg = EmailMessage(policy=SMTP)
    msg['From'] = f'MONTAi Sentinel <{sender}>'
    msg['To'] = ', '.join(recipients)
    msg['Reply-To'] = case['reply']
    msg['Subject'] = f"Incident review request — {case['phone']} — {case['id']}"
    msg['Date'] = formatdate(localtime=False)
    # A stable Message-ID aids manual reconciliation. It is NOT API deduplication.
    msg['Message-ID'] = f"<sentinel.{case['id']}@{sender.split('@')[-1]}>"
    msg.set_content(case['body'])
    for item in case['files']:
        major, minor = item['mime'].split('/', 1)
        msg.add_attachment(base64.b64decode(item['content'], validate=True), maintype=major,
                           subtype=minor, filename=item['filename'])
    return base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')


class GmailMailer:
    def __init__(self, http):
        self.http = http

    async def access_token(self):
        try:
            async with self.http.post('https://oauth2.googleapis.com/token', data={
                'client_id': os.environ['GMAIL_CLIENT_ID'],
                'client_secret': os.environ['GMAIL_CLIENT_SECRET'],
                'refresh_token': os.environ['GMAIL_REFRESH_TOKEN'],
                'grant_type': 'refresh_token',
            }) as response:
                result = await response.json()
                if response.status != 200 or not result.get('access_token'):
                    raise GmailAuthError()
                return result['access_token']
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as e:
            raise GmailAuthError() from e

    async def submit(self, token, case, recipients):
        raw = build_raw(case, recipients, os.environ['GMAIL_SENDER'])
        async with self.http.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                                  json={'raw': raw}, headers={'Authorization': 'Bearer ' + token}) as response:
            return response.status, await response.json()
