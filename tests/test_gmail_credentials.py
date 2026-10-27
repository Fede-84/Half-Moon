import json

import pytest
from pydantic import SecretStr

from backend.app.core.config import settings
from backend.app.core.data_protection import decrypt_pii, encrypt_pii
from backend.app.integrations.gmail import credentials as gmail_credentials
from backend.app.integrations.gmail.credentials import (
    GmailCredentialsError,
    load_gmail_credentials,
)
from backend.app.services.gmail_oauth import GMAIL_SEND_SCOPE


def _authorized_user_info() -> dict[str, object]:
    return {
        "token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "scopes": [GMAIL_SEND_SCOPE],
    }


def test_load_gmail_credentials_from_json_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "gmail_credentials_json",
        SecretStr(json.dumps(_authorized_user_info())),
    )

    credentials = load_gmail_credentials()

    assert credentials.refresh_token == "test-refresh-token"
    assert credentials.scopes == [GMAIL_SEND_SCOPE]


def test_load_gmail_credentials_from_file_when_json_setting_is_blank(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        encrypt_pii(json.dumps(_authorized_user_info())),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "gmail_credentials_json", SecretStr(""))
    monkeypatch.setattr(settings, "gmail_credentials_file", credentials_file)

    credentials = load_gmail_credentials()

    assert credentials.refresh_token == "test-refresh-token"
    assert credentials.scopes == [GMAIL_SEND_SCOPE]


def test_load_gmail_credentials_migrates_plaintext_file_to_encrypted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_file = tmp_path / ".secrets" / "credentials.json"
    credentials_file.parent.mkdir()
    credentials_file.write_text(
        json.dumps(_authorized_user_info()),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "gmail_credentials_json", SecretStr(""))
    monkeypatch.setattr(settings, "gmail_credentials_file", credentials_file)

    credentials = load_gmail_credentials()
    encrypted_contents = credentials_file.read_text(encoding="utf-8")

    assert credentials.refresh_token == "test-refresh-token"
    assert decrypt_pii(encrypted_contents) == json.dumps(_authorized_user_info())
    assert credentials_file.stat().st_mode & 0o777 == 0o600
    assert credentials_file.parent.stat().st_mode & 0o777 == 0o700


def test_load_gmail_credentials_rejects_undecryptable_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text("not-valid-ciphertext", encoding="utf-8")
    monkeypatch.setattr(settings, "gmail_credentials_json", SecretStr(""))
    monkeypatch.setattr(settings, "gmail_credentials_file", credentials_file)

    with pytest.raises(GmailCredentialsError, match="configured encryption key"):
        load_gmail_credentials()


def test_load_gmail_credentials_maps_decryption_value_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text("not-plaintext-json", encoding="utf-8")
    monkeypatch.setattr(settings, "gmail_credentials_json", SecretStr(""))
    monkeypatch.setattr(settings, "gmail_credentials_file", credentials_file)

    def fail_decryption(_value: str) -> str:
        raise ValueError("invalid encryption configuration")

    monkeypatch.setattr(gmail_credentials, "decrypt_pii", fail_decryption)

    with pytest.raises(GmailCredentialsError, match="configured encryption key"):
        load_gmail_credentials()


def test_load_gmail_credentials_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gmail_credentials_json", SecretStr("{"))

    with pytest.raises(GmailCredentialsError, match="valid JSON"):
        load_gmail_credentials()
