from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


class GmailApiClient:
    def __init__(self, *, client_id: str, client_secret: str, token_uri: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_uri = token_uri

    def _creds(self, *, refresh_token: str) -> Credentials:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=self._token_uri,
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )
        creds.refresh(Request())
        return creds

    def send_email(
        self,
        *,
        refresh_token: str,
        to_address: str,
        subject: str,
        body: str,
        attachments: Iterable[Attachment],
        reply_to: Optional[str] = None,
    ) -> str:
        msg = EmailMessage()
        msg["To"] = to_address
        msg["Subject"] = subject or "(no subject)"
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body or "")

        for att in attachments:
            maintype, subtype = _split_content_type(att.content_type)
            msg.add_attachment(att.data, maintype=maintype, subtype=subtype, filename=att.filename)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service = build("gmail", "v1", credentials=self._creds(refresh_token=refresh_token), cache_discovery=False)
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return sent.get("id", "") or ""


def guess_content_type(filename: str) -> str:
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


def _split_content_type(content_type: str) -> tuple[str, str]:
    if not content_type or "/" not in content_type:
        return ("application", "octet-stream")
    maintype, subtype = content_type.split("/", 1)
    maintype = maintype or "application"
    subtype = subtype or "octet-stream"
    return (maintype, subtype)
