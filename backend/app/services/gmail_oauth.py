import os
import tempfile
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from backend.app.core.config import settings
from backend.app.core.data_protection import encrypt_pii

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class GmailOAuthSetupError(RuntimeError):
    pass


def create_gmail_oauth_flow(
    state: str | None = None,
    code_verifier: str | None = None,
) -> Flow:
    client_id = settings.gmail_oauth_client_id
    client_secret_setting = settings.gmail_oauth_client_secret

    if not client_id or not client_id.strip() or client_secret_setting is None:
        raise GmailOAuthSetupError(
            "Gmail OAuth client ID and client secret must be configured"
        )

    client_secret = client_secret_setting.get_secret_value()
    if not client_secret:
        raise GmailOAuthSetupError(
            "Gmail OAuth client ID and client secret must be configured"
        )

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.gmail_oauth_redirect_uri],
            }
        },
        scopes=[GMAIL_SEND_SCOPE],
        state=state,
        code_verifier=code_verifier,
    )
    flow.redirect_uri = settings.gmail_oauth_redirect_uri
    return flow


def save_gmail_credentials(flow: Flow) -> Path:
    credentials = flow.credentials

    if not isinstance(credentials, Credentials):
        raise GmailOAuthSetupError(
            "Google returned an unsupported OAuth credential type"
        )

    if not credentials.refresh_token:
        raise GmailOAuthSetupError(
            "Google did not return a refresh token; credentials were not saved"
        )

    credentials_path = settings.gmail_credentials_file
    credentials_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(credentials_path.parent, 0o700)
    encrypted_credentials = encrypt_pii(credentials.to_json())

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".gmail-oauth-",
        dir=credentials_path.parent,
        text=True,
    )

    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as credentials_file:
            credentials_file.write(encrypted_credentials)
            credentials_file.flush()
            os.fsync(credentials_file.fileno())

        os.replace(temporary_name, credentials_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)

    return credentials_path
