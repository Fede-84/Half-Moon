from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    encrypt_pii,
)
from backend.app.core.security import hash_password
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.refresh_session import RefreshSession, RefreshSessionToken
from backend.app.models.staff_user import StaffUser

AUTH_PATH = "/api/v1/auth"
REFRESH_COOKIE_NAME = "half_moon_refresh"
CSRF_COOKIE_NAME = "half_moon_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
AUTH_COOKIE_PATH = "/api/v1/auth"
CSRF_COOKIE_PATH = "/"


def _get_cookie_header(response, cookie_name: str) -> str:
    matching_headers = [
        header
        for header in response.headers.get_list("set-cookie")
        if header.partition("=")[0] == cookie_name
    ]

    assert len(matching_headers) == 1
    return matching_headers[0]


def _register_verified_customer(
    api_client: TestClient,
    test_db_session: Session,
) -> tuple[str, str, UUID]:
    email = f"refresh-route-{uuid4().hex}@example.com"
    password = "test-password-123"

    registration_response = api_client.post(
        f"{AUTH_PATH}/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Refresh",
            "last_name": "Route",
        },
    )

    assert registration_response.status_code == 201
    customer_id = UUID(registration_response.json()["id"])

    customer_account = (
        test_db_session.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer_id)
        .one()
    )
    customer_account.email_verified_at = datetime.now(timezone.utc)
    test_db_session.flush()

    return email, password, customer_id


def test_customer_login_refresh_and_logout_routes(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    email, password, customer_id = _register_verified_customer(
        api_client,
        test_db_session,
    )

    login_response = api_client.post(
        f"{AUTH_PATH}/login",
        json={"email": email, "password": password},
    )

    assert login_response.status_code == 200
    assert login_response.json()["token_type"] == "bearer"
    assert login_response.json()["expires_in"] > 0

    refresh_cookie_header = _get_cookie_header(
        login_response,
        REFRESH_COOKIE_NAME,
    )
    csrf_cookie_header = _get_cookie_header(
        login_response,
        CSRF_COOKIE_NAME,
    )

    assert "httponly" in refresh_cookie_header.lower()
    assert f"path={AUTH_COOKIE_PATH};" in refresh_cookie_header.lower()
    assert "domain=" not in refresh_cookie_header.lower()
    assert "samesite=lax" in refresh_cookie_header.lower()
    assert "httponly" not in csrf_cookie_header.lower()
    assert f"path={CSRF_COOKIE_PATH};" in csrf_cookie_header.lower()
    assert "domain=" not in csrf_cookie_header.lower()
    assert "samesite=lax" in csrf_cookie_header.lower()

    original_refresh_token = api_client.cookies.get(REFRESH_COOKIE_NAME)
    csrf_token = api_client.cookies.get(CSRF_COOKIE_NAME)
    assert original_refresh_token is not None
    assert csrf_token is not None

    invalid_csrf_response = api_client.post(
        f"{AUTH_PATH}/refresh",
        headers={CSRF_HEADER_NAME: "invalid-csrf-token"},
    )

    assert invalid_csrf_response.status_code == 403
    assert invalid_csrf_response.json()["detail"] == "Invalid CSRF token"
    assert api_client.cookies.get(REFRESH_COOKIE_NAME) == original_refresh_token

    refresh_session = (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.customer_id == customer_id)
        .one()
    )
    assert refresh_session.current_generation == 0
    assert refresh_session.revoked_at is None

    refresh_response = api_client.post(
        f"{AUTH_PATH}/refresh",
        headers={CSRF_HEADER_NAME: csrf_token},
    )

    assert refresh_response.status_code == 200
    assert refresh_response.json()["token_type"] == "bearer"
    assert refresh_response.json()["expires_in"] > 0

    rotated_refresh_token = api_client.cookies.get(REFRESH_COOKIE_NAME)
    rotated_csrf_token = api_client.cookies.get(CSRF_COOKIE_NAME)
    assert rotated_refresh_token is not None
    assert rotated_csrf_token is not None
    assert rotated_refresh_token != original_refresh_token

    token_history = (
        test_db_session.query(
            RefreshSessionToken.generation,
            RefreshSessionToken.state,
        )
        .filter(RefreshSessionToken.session_id == refresh_session.id)
        .order_by(RefreshSessionToken.generation)
        .all()
    )
    assert token_history == [(0, "ROTATED"), (1, "ACTIVE")]

    logout_response = api_client.post(
        f"{AUTH_PATH}/logout",
        headers={CSRF_HEADER_NAME: rotated_csrf_token},
    )

    assert logout_response.status_code == 204
    assert api_client.cookies.get(REFRESH_COOKIE_NAME) is None
    assert api_client.cookies.get(CSRF_COOKIE_NAME) is None

    refresh_clear_header = _get_cookie_header(
        logout_response,
        REFRESH_COOKIE_NAME,
    )
    csrf_clear_header = _get_cookie_header(
        logout_response,
        CSRF_COOKIE_NAME,
    )
    assert "max-age=0" in refresh_clear_header.lower()
    assert f"path={AUTH_COOKIE_PATH};" in refresh_clear_header.lower()
    assert "max-age=0" in csrf_clear_header.lower()
    assert f"path={CSRF_COOKIE_PATH};" in csrf_clear_header.lower()

    test_db_session.refresh(refresh_session)
    assert refresh_session.revoked_at is not None


def test_staff_login_sets_secure_auth_cookies(
    api_client: TestClient,
    test_db_session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "refresh_cookie_secure", True)

    email = f"staff-refresh-route-{uuid4().hex}@example.com"
    password = "test-password-123"
    staff_user = StaffUser(
        email_ciphertext=encrypt_pii(email),
        email_lookup_hmac=create_email_lookup_hmac(email),
        first_name_ciphertext=encrypt_pii("Refresh"),
        last_name_ciphertext=encrypt_pii("Staff"),
        password_hash=hash_password(password),
        role="STORE_MANAGER",
    )
    test_db_session.add(staff_user)
    test_db_session.flush()

    login_response = api_client.post(
        f"{AUTH_PATH}/staff/login",
        json={"email": email, "password": password},
    )

    assert login_response.status_code == 200
    assert login_response.json()["role"] == "STORE_MANAGER"

    refresh_cookie_header = _get_cookie_header(
        login_response,
        REFRESH_COOKIE_NAME,
    )
    csrf_cookie_header = _get_cookie_header(
        login_response,
        CSRF_COOKIE_NAME,
    )

    assert "secure" in refresh_cookie_header.lower()
    assert "httponly" in refresh_cookie_header.lower()
    assert "secure" in csrf_cookie_header.lower()
    assert "httponly" not in csrf_cookie_header.lower()

    staff_session_count = (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.staff_user_id == staff_user.id)
        .count()
    )
    assert staff_session_count == 1


def test_refresh_and_logout_reject_missing_csrf_without_mutating_session(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    email, password, customer_id = _register_verified_customer(
        api_client,
        test_db_session,
    )
    login_response = api_client.post(
        f"{AUTH_PATH}/login",
        json={"email": email, "password": password},
    )

    assert login_response.status_code == 200
    csrf_token = api_client.cookies.get(CSRF_COOKIE_NAME)
    original_refresh_token = api_client.cookies.get(REFRESH_COOKIE_NAME)
    assert csrf_token is not None
    assert original_refresh_token is not None

    refresh_session = (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.customer_id == customer_id)
        .one()
    )

    for endpoint in ("refresh", "logout"):
        response = api_client.post(f"{AUTH_PATH}/{endpoint}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Invalid CSRF token"
        assert api_client.cookies.get(REFRESH_COOKIE_NAME) == original_refresh_token

        test_db_session.refresh(refresh_session)
        assert refresh_session.current_generation == 0
        assert refresh_session.revoked_at is None

    api_client.cookies.delete(CSRF_COOKIE_NAME)

    for endpoint in ("refresh", "logout"):
        response = api_client.post(
            f"{AUTH_PATH}/{endpoint}",
            headers={CSRF_HEADER_NAME: csrf_token},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Invalid CSRF token"
        assert api_client.cookies.get(REFRESH_COOKIE_NAME) == original_refresh_token
        assert api_client.cookies.get(CSRF_COOKIE_NAME) is None

        test_db_session.refresh(refresh_session)
        assert refresh_session.current_generation == 0
        assert refresh_session.revoked_at is None


def test_customer_and_staff_profile_routes_reject_cross_role_tokens(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    email, password, customer_id = _register_verified_customer(
        api_client,
        test_db_session,
    )
    customer_login_response = api_client.post(
        f"{AUTH_PATH}/login",
        json={"email": email, "password": password},
    )

    assert customer_login_response.status_code == 200
    customer_token = customer_login_response.json()["access_token"]

    staff_email = f"profile-role-{uuid4().hex}@example.com"
    staff_password = "test-password-123"
    staff_user = StaffUser(
        email_ciphertext=encrypt_pii(staff_email),
        email_lookup_hmac=create_email_lookup_hmac(staff_email),
        first_name_ciphertext=encrypt_pii("Profile"),
        last_name_ciphertext=encrypt_pii("Boundary"),
        password_hash=hash_password(staff_password),
        role="SALES_ASSOCIATE",
    )
    test_db_session.add(staff_user)
    test_db_session.flush()

    staff_login_response = api_client.post(
        f"{AUTH_PATH}/staff/login",
        json={"email": staff_email, "password": staff_password},
    )

    assert staff_login_response.status_code == 200
    assert staff_login_response.json()["role"] == "SALES_ASSOCIATE"
    staff_token = staff_login_response.json()["access_token"]

    customer_headers = {"Authorization": f"Bearer {customer_token}"}
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    customer_profile_response = api_client.get(
        f"{AUTH_PATH}/me",
        headers=customer_headers,
    )
    assert customer_profile_response.status_code == 200
    assert customer_profile_response.json()["id"] == str(customer_id)

    assert api_client.get(
        "/api/v1/staff/me",
        headers=customer_headers,
    ).status_code == 401

    staff_profile_response = api_client.get(
        "/api/v1/staff/me",
        headers=staff_headers,
    )
    assert staff_profile_response.status_code == 200
    assert staff_profile_response.json()["id"] == str(staff_user.id)

    assert api_client.get(
        f"{AUTH_PATH}/me",
        headers=staff_headers,
    ).status_code == 401
