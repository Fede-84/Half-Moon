import base64
import os
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

_TEST_DATABASE_NAME = "half_moon_test_db"
_TEST_DATABASE_USER = "half_moon_test_user"
_TEST_DATABASE_PORT = 55432
_LOCAL_HOSTS = {None, "localhost", "127.0.0.1", "::1"}
_UNCONFIGURED_TEST_DATABASE_URL = (
    "postgresql+psycopg://half_moon_test_user:"
    "NO_TEST_PASSWORD_CONFIGURED@127.0.0.1:55432/half_moon_test_db"
)


class DatabaseTestConfig(BaseSettings):
    database_url: str | None = Field(
        default=None,
        validation_alias="HALF_MOON_TEST_DATABASE_URL",
    )

    model_config = SettingsConfigDict(
        env_file=".env.test",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def validate_test_database_url(database_url: str) -> URL:
    try:
        parsed_url = make_url(database_url)
    except ArgumentError as error:
        raise RuntimeError("Test database URL is invalid") from error

    if parsed_url.drivername != "postgresql+psycopg":
        raise RuntimeError("Tests require PostgreSQL with the psycopg driver")

    if parsed_url.database != _TEST_DATABASE_NAME:
        raise RuntimeError("Tests may only connect to half_moon_test_db")

    if parsed_url.username != _TEST_DATABASE_USER:
        raise RuntimeError("Tests require the dedicated test database user")

    if parsed_url.host not in _LOCAL_HOSTS:
        raise RuntimeError("Tests may only connect to a local database host")

    if parsed_url.port != _TEST_DATABASE_PORT:
        raise RuntimeError("Tests may only connect to port 55432")

    return parsed_url


_database_test_config = DatabaseTestConfig()
if _database_test_config.database_url is None:
    _pytest_database_url = validate_test_database_url(_UNCONFIGURED_TEST_DATABASE_URL)
else:
    _pytest_database_url = validate_test_database_url(
        _database_test_config.database_url
    )

os.environ["DATABASE_URL"] = _pytest_database_url.render_as_string(hide_password=False)
os.environ.update(
    {
        "JWT_SECRET_KEY": "test-only-jwt-secret-key-not-for-any-environment",
        "JWT_ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "PII_ENCRYPTION_KEY": base64.urlsafe_b64encode(bytes(range(32))).decode(
            "ascii"
        ),
        "EMAIL_LOOKUP_HMAC_SECRET": "test-only-email-lookup-hmac-secret",
        "POS_CLAIM_CODE_HMAC_SECRET": "test-only-pos-claim-code-hmac-secret",
        "STRIPE_SECRET_KEY": "sk_test_half_moon_fixture_only",
        "STRIPE_WEBHOOK_SECRET": "whsec_half_moon_fixture_only",
        "STRIPE_CHECKOUT_SUCCESS_URL": (
            "http://127.0.0.1:8000/api/v1/checkout/success"
        ),
        "STRIPE_CHECKOUT_CANCEL_URL": (
            "http://127.0.0.1:8000/api/v1/checkout/cancelled"
        ),
        "GMAIL_OAUTH_CLIENT_ID": "test-client-id",
        "GMAIL_OAUTH_CLIENT_SECRET": "test-client-secret",
        "GMAIL_OAUTH_REDIRECT_URI": (
            "http://127.0.0.1:8000/api/v1/auth/gmail/callback"
        ),
        "GMAIL_SENDER_EMAIL": "test@example.invalid",
        "GMAIL_CREDENTIALS_JSON": "",
        "GMAIL_CREDENTIALS_FILE": ".secrets/test-gmail-oauth-token.json",
        "GMAIL_DELIVERY_WORKER_ENABLED": "false",
        "GMAIL_OAUTH_SETUP_ENABLED": "false",
        "BACKEND_PUBLIC_URL": "http://127.0.0.1:8000",
        "DEMO_MANAGER_PASSWORD": "test-manager-password",
        "DEMO_SALES_PASSWORD": "test-sales-password",
        "RESERVATION_DURATION_MINUTES": "3",
        "MOCK_SHIP_AFTER_SECONDS": "5",
        "MOCK_DELIVERY_AFTER_SECONDS": "5",
        "STANDARD_VAT_RATE": "0.22",
    }
)


@pytest.fixture(autouse=True)
def disable_gmail_delivery_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "gmail_delivery_worker_enabled", False)
    monkeypatch.setattr(settings, "pos_receipt_cleanup_worker_enabled", False)
    monkeypatch.setattr(settings, "refresh_session_cleanup_worker_enabled", False)
    monkeypatch.setattr(settings, "refresh_cookie_secure", False)


@pytest.fixture
def database_url_validator() -> Callable[[str], URL]:
    return validate_test_database_url


@pytest.fixture(scope="session")
def half_moon_test_database_url() -> URL:
    database_url = _database_test_config.database_url

    if database_url is None:
        raise RuntimeError("HALF_MOON_TEST_DATABASE_URL must be set in .env.test")

    return validate_test_database_url(database_url)


@pytest.fixture(scope="session")
def half_moon_test_engine(
    half_moon_test_database_url: URL,
) -> Iterator[Engine]:
    engine = create_engine(half_moon_test_database_url, pool_pre_ping=True)

    try:
        with engine.connect() as connection:
            identity = (
                connection.execute(
                    text(
                        """
                    SELECT
                        current_database() AS database_name,
                        current_user AS role_name,
                        host(inet_server_addr()) AS server_address,
                        inet_server_port() AS server_port
                    """
                    )
                )
                .mappings()
                .one()
            )

            if (
                identity["database_name"] != _TEST_DATABASE_NAME
                or identity["role_name"] != _TEST_DATABASE_USER
                or identity["server_address"] != "127.0.0.1"
                or identity["server_port"] != _TEST_DATABASE_PORT
            ):
                raise RuntimeError(
                    "Connected PostgreSQL identity is not the isolated test cluster"
                )

        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def test_db_connection(
    half_moon_test_engine: Engine,
) -> Iterator[Connection]:
    connection = half_moon_test_engine.connect()
    outer_transaction = connection.begin()

    try:
        yield connection
    finally:
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


@pytest.fixture
def test_db_session(test_db_connection: Connection) -> Iterator[Session]:
    test_session_factory = sessionmaker(
        bind=test_db_connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    with test_session_factory() as session:
        yield session


@pytest.fixture
def api_client(test_db_session: Session) -> Iterator[TestClient]:
    from backend.app.db.database import get_db
    from backend.app.main import app

    def override_get_db() -> Iterator[Session]:
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
