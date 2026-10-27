from base64 import urlsafe_b64encode
from email.message import EmailMessage

from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.http import build_http

from backend.app.core.config import settings
from backend.app.integrations.gmail.credentials import load_gmail_credentials


class GmailDeliveryError(RuntimeError):
    pass


def send_gmail_message(
    recipient_email: str,
    subject: str,
    text_body: str,
) -> str:
    sender_email = settings.gmail_sender_email
    if not sender_email or not sender_email.strip():
        raise GmailDeliveryError("Gmail sender email is not configured")

    message = EmailMessage()
    message["To"] = recipient_email
    message["From"] = sender_email
    message["Subject"] = subject
    message.set_content(text_body)

    raw_message = urlsafe_b64encode(message.as_bytes()).decode("ascii")

    credentials = load_gmail_credentials()
    http = build_http()
    http.timeout = 30
    authorized_http = AuthorizedHttp(credentials, http=http)

    service = build(
        "gmail",
        "v1",
        http=authorized_http,
        cache_discovery=False,
    )
    response = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw_message})
        .execute()
    )

    if not isinstance(response, dict):
        raise GmailDeliveryError("Gmail API returned an invalid send response")

    message_id = response.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise GmailDeliveryError("Gmail API did not return a message ID")

    return message_id
