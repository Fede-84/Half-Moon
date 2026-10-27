from base64 import urlsafe_b64decode
from email.parser import BytesParser
from email.policy import default
from unittest.mock import Mock

import pytest
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp

from backend.app.core.config import settings
from backend.app.integrations.gmail import client as gmail_client
from backend.app.integrations.gmail.client import (
    GmailDeliveryError,
    send_gmail_message,
)


def _test_credentials() -> Credentials:
    return Credentials(
        token="test-access-token",
        refresh_token="test-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
    )


def test_send_gmail_message_encodes_and_sends_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = _test_credentials()
    service = Mock()
    send_request = service.users.return_value.messages.return_value.send
    send_request.return_value.execute.return_value = {"id": "test-message-id"}
    build_mock = Mock(return_value=service)

    def load_test_credentials() -> Credentials:
        return credentials

    monkeypatch.setattr(settings, "gmail_sender_email", "sender@example.com")
    monkeypatch.setattr(gmail_client, "load_gmail_credentials", load_test_credentials)
    monkeypatch.setattr(gmail_client, "build", build_mock)

    message_id = send_gmail_message(
        "recipient@example.com",
        "Verify your email",
        "Open the verification link.",
    )

    assert message_id == "test-message-id"
    build_mock.assert_called_once()
    build_call = build_mock.call_args
    authorized_http = build_call.kwargs["http"]
    assert isinstance(authorized_http, AuthorizedHttp)
    assert authorized_http.credentials is credentials
    assert authorized_http.http.timeout == 30
    assert "credentials" not in build_call.kwargs

    send_request.assert_called_once()
    request_kwargs = send_request.call_args.kwargs
    assert request_kwargs["userId"] == "me"

    raw_message = request_kwargs["body"]["raw"]
    parsed_message = BytesParser(policy=default).parsebytes(
        urlsafe_b64decode(raw_message)
    )
    assert parsed_message["To"] == "recipient@example.com"
    assert parsed_message["From"] == "sender@example.com"
    assert parsed_message["Subject"] == "Verify your email"
    assert parsed_message.get_content().strip() == "Open the verification link."


def test_send_gmail_message_requires_sender_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gmail_sender_email", None)

    with pytest.raises(GmailDeliveryError, match="sender email is not configured"):
        send_gmail_message(
            "recipient@example.com",
            "Verify your email",
            "Open the verification link.",
        )


def test_send_gmail_message_requires_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Mock()
    send_request = service.users.return_value.messages.return_value.send
    send_request.return_value.execute.return_value = {"threadId": "test-thread-id"}

    def load_test_credentials() -> Credentials:
        return _test_credentials()

    monkeypatch.setattr(settings, "gmail_sender_email", "sender@example.com")
    monkeypatch.setattr(gmail_client, "load_gmail_credentials", load_test_credentials)
    monkeypatch.setattr(gmail_client, "build", Mock(return_value=service))

    with pytest.raises(GmailDeliveryError, match="did not return a message ID"):
        send_gmail_message(
            "recipient@example.com",
            "Verify your email",
            "Open the verification link.",
        )
