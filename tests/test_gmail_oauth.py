import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from google_auth_oauthlib.flow import Flow
from pydantic import SecretStr

from backend.app.core.config import settings
from backend.app.core.data_protection import decrypt_pii
from backend.app.main import app
from backend.app.services.gmail_oauth import (
    GMAIL_SEND_SCOPE,
    GmailOAuthSetupError,
    create_gmail_oauth_flow,
    save_gmail_credentials,
)


@pytest.fixture
def gmail_oauth_flow(monkeypatch: pytest.MonkeyPatch) -> Flow:
    monkeypatch.setattr(settings, "gmail_oauth_client_id", "test-client-id")
    monkeypatch.setattr(
        settings,
        "gmail_oauth_client_secret",
        SecretStr("test-client-secret"),
    )
    monkeypatch.setattr(
        settings,
        "gmail_oauth_redirect_uri",
        "http://localhost:8000/api/v1/auth/gmail/callback",
    )
    return create_gmail_oauth_flow()


def test_oauth_flow_requests_only_gmail_send(gmail_oauth_flow: Flow) -> None:
    authorization_url, state = gmail_oauth_flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    query = parse_qs(urlparse(authorization_url).query)

    assert query["scope"] == [GMAIL_SEND_SCOPE]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/gmail/callback"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["state"] == [state]
    code_verifier = gmail_oauth_flow.code_verifier
    assert code_verifier is not None
    assert query["code_challenge_method"] == ["S256"]

    callback_flow = create_gmail_oauth_flow(
        state=state,
        code_verifier=code_verifier,
    )
    assert callback_flow.code_verifier == code_verifier


def test_saving_credentials_uses_restricted_file_permissions(
    gmail_oauth_flow: Flow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / ".secrets" / "gmail-oauth-token.json"
    monkeypatch.setattr(settings, "gmail_credentials_file", credentials_path)
    gmail_oauth_flow.oauth2session.token = {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "token_type": "Bearer",
        "scope": GMAIL_SEND_SCOPE,
        "expires_at": time.time() + 3600,
    }

    saved_path = save_gmail_credentials(gmail_oauth_flow)
    saved_credentials = json.loads(
        decrypt_pii(saved_path.read_text(encoding="utf-8"))
    )

    assert saved_path == credentials_path
    assert saved_credentials["refresh_token"] == "test-refresh-token"
    assert credentials_path.stat().st_mode & 0o777 == 0o600
    assert credentials_path.parent.stat().st_mode & 0o777 == 0o700


def test_saving_credentials_rejects_missing_refresh_token(
    gmail_oauth_flow: Flow,
) -> None:
    gmail_oauth_flow.oauth2session.token = {
        "access_token": "test-access-token",
        "token_type": "Bearer",
        "scope": GMAIL_SEND_SCOPE,
        "expires_at": time.time() + 3600,
    }

    with pytest.raises(GmailOAuthSetupError, match="refresh token"):
        save_gmail_credentials(gmail_oauth_flow)


def test_gmail_oauth_start_is_disabled_when_setup_flag_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gmail_oauth_setup_enabled", False)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/gmail/start",
            follow_redirects=False,
        )

    assert response.status_code == 404


def test_gmail_oauth_start_rejects_non_loopback_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gmail_oauth_setup_enabled", True)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/gmail/start",
            follow_redirects=False,
        )

    assert response.status_code == 403
