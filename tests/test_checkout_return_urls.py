import pytest

from backend.app.core.config import Settings, settings as app_settings


def make_settings(**overrides: str) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://user:pass@localhost/half_moon_test_db",
        "jwt_secret_key": "j" * 32,
        "pii_encryption_key": "p" * 44,
        "email_lookup_hmac_secret": "e" * 32,
        "pos_claim_code_hmac_secret": "c" * 32,
        "demo_manager_password": "manager-password",
        "demo_sales_password": "sales-password",
        "frontend_public_url": "https://shop.example.test/",
        "stripe_checkout_success_url": "",
        "stripe_checkout_cancel_url": "",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_checkout_return_urls_default_to_frontend_public_url() -> None:
    settings = make_settings()

    assert (
        settings.stripe_checkout_success_url
        == "https://shop.example.test/checkout/success"
    )
    assert (
        settings.stripe_checkout_cancel_url
        == "https://shop.example.test/checkout/cancelled"
    )


def test_explicit_checkout_return_urls_are_preserved() -> None:
    settings = make_settings(
        stripe_checkout_success_url="https://returns.example.test/paid",
        stripe_checkout_cancel_url="https://returns.example.test/cancelled",
    )

    assert (
        settings.stripe_checkout_success_url
        == "https://returns.example.test/paid"
    )
    assert (
        settings.stripe_checkout_cancel_url
        == "https://returns.example.test/cancelled"
    )


@pytest.mark.parametrize(
    ("return_path", "frontend_path", "preserves_session_id"),
    [
        ("/api/v1/checkout/success", "/checkout/success", True),
        ("/api/v1/checkout/cancelled", "/checkout/cancelled", False),
    ],
)
def test_checkout_return_routes_redirect_to_frontend(
    api_client,
    monkeypatch: pytest.MonkeyPatch,
    return_path: str,
    frontend_path: str,
    preserves_session_id: bool,
) -> None:
    monkeypatch.setattr(
        app_settings,
        "frontend_public_url",
        "https://shop.example.test/",
    )

    response = api_client.get(
        return_path,
        params={"session_id": "cs_test_return_fixture"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    expected_query = (
        "?session_id=cs_test_return_fixture" if preserves_session_id else ""
    )
    assert (
        response.headers["location"]
        == f"https://shop.example.test{frontend_path}{expected_query}"
    )
