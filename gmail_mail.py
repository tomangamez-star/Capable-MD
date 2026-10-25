"""Send-only Gmail OAuth, with no automatic retries of message submissions."""
import base64
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate

class AuthError(Exception):
    pass


def raw_message(sender, recipient, subject, body, case_id):
    msg = EmailMessage(policy=SMTP)
    msg['From'] = f'KANA NOTES <{sender}>'
    msg['To'] = recipient
    msg['Reply-To'] = sender
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=False)
    # Stable for reconciliation; Gmail does not promise deduplication by Message-ID.
    msg['Message-ID'] = f'<kana.{case_id}@{sender.split("@")[1]}>'
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')


class Gmail:
    def __init__(self, http, config):
        self.http, self.config = http, config

    async def token(self, account):
        try:
            async with self.http.post('https://oauth2.googleapis.com/token', data={
                'client_id': account.get('client_id', self.config.client_id),
                'client_secret': account.get('client_secret', self.config.client_secret),
                'refresh_token': account['refresh_token'], 'grant_type': 'refresh_token',
            }) as r:
                result = await r.json()
                if r.status != 200 or not result.get('access_token'):
                    raise AuthError('Google authorization failed')
                return result['access_token']
        except Exception as exc:
            raise AuthError('Google authorization failed; check or renew the credentials') from exc

    async def send(self, account, recipient, subject, body, case_id):
        raw = raw_message(account['email'], recipient, subject, body, case_id)
        token = await self.token(account)  # Authorization failures happen before submission.
        try:
            async with self.http.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                                      headers={'Authorization': 'Bearer ' + token}, json={'raw': raw}) as r:
                result = await r.json()
                if r.status == 200 and result.get('id'):
                    return 'submitted', str(result['id'])
                if 400 <= r.status < 500 and r.status not in (408,409,429):
                    return 'rejected', f'HTTP {r.status}'
                return 'unknown', ''
        except Exception:
            return 'unknown', ''
