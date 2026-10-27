import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import InvalidToken
from google.oauth2.credentials import Credentials

from backend.app.core.config import settings
from backend.app.core.data_protection import decrypt_pii, encrypt_pii


class GmailCredentialsError(RuntimeError):
    pass


def _write_encrypted_credentials(path: Path, serialized_credentials: str) -> None:
    encrypted_credentials = encrypt_pii(serialized_credentials)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".gmail-oauth-",
        dir=path.parent,
        text=True,
    )

    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as credentials_file:
            credentials_file.write(encrypted_credentials)
            credentials_file.flush()
            os.fsync(credentials_file.fileno())

        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_gmail_credentials() -> Credentials:
    credentials_json_setting = settings.gmail_credentials_json
    serialized_credentials = (
        credentials_json_setting.get_secret_value()
        if credentials_json_setting is not None
        else ""
    )

    if not serialized_credentials.strip():
        credentials_path = settings.gmail_credentials_file
        try:
            file_contents = credentials_path.read_text(encoding="utf-8")
        except OSError as error:
            raise GmailCredentialsError(
                "Gmail credentials file could not be read"
            ) from error

        try:
            json.loads(file_contents)
        except json.JSONDecodeError:
            try:
                serialized_credentials = decrypt_pii(file_contents)
            except (InvalidToken, ValueError) as error:
                raise GmailCredentialsError(
                    "Gmail credentials file is neither valid plaintext JSON nor "
                    "decryptable with the configured encryption key"
                ) from error
        else:
            try:
                _write_encrypted_credentials(credentials_path, file_contents)
            except OSError as error:
                raise GmailCredentialsError(
                    "Plaintext Gmail credentials could not be securely migrated"
                ) from error
            serialized_credentials = file_contents

    try:
        credential_info = json.loads(serialized_credentials)
    except json.JSONDecodeError as error:
        raise GmailCredentialsError(
            "Gmail credential data is not valid JSON"
        ) from error

    if not isinstance(credential_info, dict):
        raise GmailCredentialsError("Gmail credential data must be a JSON object")

    try:
        credentials = Credentials.from_authorized_user_info(credential_info)
    except (KeyError, TypeError, ValueError) as error:
        raise GmailCredentialsError(
            "Gmail credential data is incomplete or invalid"
        ) from error

    if not credentials.refresh_token:
        raise GmailCredentialsError("Gmail credentials do not contain a refresh token")

    return credentials
