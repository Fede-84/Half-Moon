import hashlib
import hmac

from cryptography.fernet import Fernet

from backend.app.core.config import settings

fernet = Fernet(settings.pii_encryption_key.encode())


def encrypt_pii(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()


def decrypt_pii(value: str) -> str:
    return fernet.decrypt(value.encode()).decode()


def create_email_lookup_hmac(email: str) -> str:
    normalized_email = email.strip().lower()

    return hmac.new(
        settings.email_lookup_hmac_secret.encode(),
        normalized_email.encode(),
        hashlib.sha256,
    ).hexdigest()


def create_pos_claim_code_digest(claim_code: str) -> str:
    normalized_code = claim_code.strip().upper()
    message = f"half-moon-pos-claim-code:v1:{normalized_code}".encode()

    return hmac.new(
        settings.pos_claim_code_hmac_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
