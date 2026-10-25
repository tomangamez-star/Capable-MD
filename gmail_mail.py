"""Send-only Gmail OAuth, with no automatic retries of message submissions.

Every refusal from Google is reported verbatim. A refresh token can be
rejected for several different reasons - expired, revoked, issued to a
different OAuth client, or simply blank - and the operator cannot renew the
right thing without knowing which. Nothing here is allowed to collapse those
into one opaque message.
"""
import base64
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate

TOKEN_URL = 'https://oauth2.googleapis.com/token'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'


class AuthError(Exception):
    """Google refused the credentials before anything was submitted.

    ``reason`` holds Google's own ``error`` / ``error_description`` text, so a
    per-sender failure can be named instead of guessed at.
    """

    def __init__(self, message, reason=''):
        super().__init__(message)
        self.reason = reason or message


class TransportError(Exception):
    """Google never answered: nothing was submitted and the outcome is unknown."""


def reason_from(status, body):
    """One short line quoting Google's own explanation, for the operator."""
    if not isinstance(body, dict):
        return 'HTTP ' + str(status)
    error = body.get('error')
    if isinstance(error, dict):                      # Gmail API error envelope
        code = str(error.get('code') or status)
        parts = []
        for item in error.get('errors') or []:
            if isinstance(item, dict) and item.get('reason'):
                parts.append(str(item['reason']))
                break
        if error.get('message'):
            parts.append(str(error['message']).strip())
        return ('HTTP ' + code + ' ' + ': '.join(parts)) if parts else ('HTTP ' + code)
    if isinstance(error, str) and error.strip():     # OAuth token-error envelope
        detail = str(body.get('error_description') or '').strip()
        return (error.strip() + ': ' + detail) if detail else error.strip()
    return 'HTTP ' + str(status)


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

    def _client(self, account):
        """This account's OAuth client, falling back to the shared one.

        An empty or whitespace-only override counts as absent: posting a blank
        client_id can only ever come back as an opaque refusal.
        """
        def pick(key, fallback):
            value = account.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            return (fallback or '').strip()
        return (pick('client_id', getattr(self.config, 'client_id', '')),
                pick('client_secret', getattr(self.config, 'client_secret', '')))

    async def token(self, account):
        """Exchange a refresh token for an access token.

        Raises AuthError, carrying Google's reason, when Google says no. Raises
        TransportError when Google never answered - that is not an auth failure
        and must not be reported as one.
        """
        client_id, client_secret = self._client(account)
        try:
            async with self.http.post(TOKEN_URL, data={
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': (account.get('refresh_token') or '').strip(),
                'grant_type': 'refresh_token',
            }) as r:
                status = r.status
                try:
                    body = await r.json()
                except Exception:
                    body = {}
        except Exception as exc:
            raise TransportError('Token endpoint unreachable') from exc
        if status == 200 and isinstance(body, dict) and body.get('access_token'):
            return body['access_token']
        # Raised outside the try: Google's reason must never be re-wrapped.
        raise AuthError('Google refused the refresh token for ' + str(account.get('email', '')),
                        reason_from(status, body))

    async def send(self, account, recipient, subject, body, case_id):
        raw = raw_message(account['email'], recipient, subject, body, case_id)
        try:
            token = await self.token(account)  # Authorization happens before submission.
        except TransportError:
            return 'unknown', 'no answer from Google'
        try:
            async with self.http.post(SEND_URL, headers={'Authorization': 'Bearer ' + token},
                                      json={'raw': raw}) as r:
                status = r.status
                try:
                    result = await r.json()
                except Exception:
                    result = {}
        except Exception:
            return 'unknown', 'no answer from Google'
        if status == 200 and isinstance(result, dict) and result.get('id'):
            return 'submitted', str(result['id'])
        if 400 <= status < 500 and status not in (408, 409, 429):
            return 'rejected', reason_from(status, result)
        return 'unknown', reason_from(status, result)
