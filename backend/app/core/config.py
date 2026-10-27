from decimal import Decimal
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(default="", min_length=1)
    jwt_secret_key: str = Field(default="", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=15, gt=0)
    customer_refresh_session_days: int = Field(default=30, gt=0)
    staff_refresh_session_hours: int = Field(default=12, gt=0)
    refresh_cookie_secure: bool = True
    pii_encryption_key: str = Field(default="", min_length=44)
    email_lookup_hmac_secret: str = Field(default="", min_length=32)
    pos_claim_code_hmac_secret: str = Field(default="", min_length=32)
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_checkout_success_url: str = ""
    stripe_checkout_cancel_url: str = ""
    gmail_oauth_client_id: str | None = None
    gmail_oauth_client_secret: SecretStr | None = None
    gmail_oauth_redirect_uri: str = "http://localhost:8000/api/v1/auth/gmail/callback"
    gmail_sender_email: str | None = None
    gmail_credentials_json: SecretStr | None = None
    gmail_credentials_file: Path = Path(".secrets/gmail-oauth-token.json")
    gmail_delivery_worker_enabled: bool = False
    gmail_oauth_setup_enabled: bool = False
    pos_receipt_cleanup_worker_enabled: bool = True
    refresh_session_cleanup_worker_enabled: bool = True
    backend_public_url: str = "http://127.0.0.1:8000"
    frontend_public_url: str = "http://localhost:5173"
    demo_manager_password: str = Field(default="", min_length=8)
    demo_sales_password: str = Field(default="", min_length=8)
    reservation_duration_minutes: int = Field(default=3, gt=0)
    mock_ship_after_seconds: int = Field(default=5, gt=0)
    mock_delivery_after_seconds: int = Field(default=5, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )
    standard_vat_rate: Decimal = Field(
        default=Decimal("0.22"),
        ge=0,
        le=1,
    )

    @model_validator(mode="after")
    def apply_checkout_return_url_defaults(self) -> Self:
        frontend_base_url = self.frontend_public_url.strip().rstrip("/")
        if not frontend_base_url:
            raise ValueError("frontend_public_url must not be blank")

        if not self.stripe_checkout_success_url:
            self.stripe_checkout_success_url = (
                f"{frontend_base_url}/checkout/success"
            )
        if not self.stripe_checkout_cancel_url:
            self.stripe_checkout_cancel_url = (
                f"{frontend_base_url}/checkout/cancelled"
            )

        return self


settings = Settings()
