import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.stripe_webhook_event import StripeWebhookEvent
from backend.app.services.reservations import release_expired_reservations


def test_pytest_database_url_targets_isolated_test_server():
    database_url = make_url(os.environ["DATABASE_URL"])

    assert database_url.database == "half_moon_test_db"
    assert database_url.username == "half_moon_test_user"
    assert database_url.host == "127.0.0.1"
    assert database_url.port == 55432


def test_guard_accepts_dedicated_local_test_database(database_url_validator):
    database_url = database_url_validator(
        "postgresql+psycopg://half_moon_test_user@localhost:55432/half_moon_test_db"
    )

    assert database_url.database == "half_moon_test_db"
    assert database_url.username == "half_moon_test_user"


def test_guard_rejects_development_database(database_url_validator):
    with pytest.raises(RuntimeError, match="only connect to half_moon_test_db"):
        database_url_validator(
            "postgresql+psycopg://half_moon_test_user@localhost:55432/half_moon_db"
        )


def test_guard_rejects_remote_database_host(database_url_validator):
    with pytest.raises(RuntimeError, match="local database host"):
        database_url_validator(
            "postgresql+psycopg://half_moon_test_user@example.com:55432/"
            "half_moon_test_db"
        )


def test_guard_rejects_non_test_database_user(database_url_validator):
    with pytest.raises(RuntimeError, match="dedicated test database user"):
        database_url_validator(
            "postgresql+psycopg://coco@localhost:55432/half_moon_test_db"
        )


def test_guard_rejects_other_postgres_port(database_url_validator):
    with pytest.raises(RuntimeError, match="only connect to port 55432"):
        database_url_validator(
            "postgresql+psycopg://half_moon_test_user@localhost:5432/half_moon_test_db"
        )


def test_test_engine_connects_to_isolated_cluster(half_moon_test_engine):
    assert half_moon_test_engine.url.database == "half_moon_test_db"
    assert half_moon_test_engine.url.username == "half_moon_test_user"
    assert half_moon_test_engine.url.host == "127.0.0.1"
    assert half_moon_test_engine.url.port == 55432


def test_session_commit_is_rolled_back_with_outer_transaction(
    half_moon_test_engine,
    test_db_connection,
    test_db_session,
):
    stripe_event_id = f"evt_test_isolation_{uuid4()}"
    test_db_session.add(
        StripeWebhookEvent(
            stripe_event_id=stripe_event_id,
            event_type="test.isolation",
        )
    )
    test_db_session.commit()

    assert (
        test_db_session.scalar(
            select(StripeWebhookEvent.id).where(
                StripeWebhookEvent.stripe_event_id == stripe_event_id
            )
        )
        is not None
    )
    assert test_db_connection.in_transaction()

    test_db_connection.rollback()

    with half_moon_test_engine.connect() as verification_connection:
        assert (
            verification_connection.scalar(
                select(StripeWebhookEvent.id).where(
                    StripeWebhookEvent.stripe_event_id == stripe_event_id
                )
            )
            is None
        )


@pytest.mark.parametrize(
    ("operation", "helper"),
    [
        ("commit", commit_or_rollback),
        ("flush", flush_or_rollback),
    ],
)
def test_database_operation_helpers_rollback_before_reraising(
    test_db_session,
    monkeypatch,
    operation,
    helper,
):
    database_error = SQLAlchemyError(f"{operation} failed")
    rollback_calls = 0

    def fail_operation():
        raise database_error

    def record_rollback():
        nonlocal rollback_calls
        rollback_calls += 1

    monkeypatch.setattr(test_db_session, operation, fail_operation)
    monkeypatch.setattr(test_db_session, "rollback", record_rollback)

    with pytest.raises(SQLAlchemyError) as error:
        helper(test_db_session)

    assert error.value is database_error
    assert rollback_calls == 1


def test_expired_reservation_cleanup_does_not_commit(test_db_session, monkeypatch):
    def fail_commit():
        pytest.fail("reservation cleanup must not commit its caller's session")

    monkeypatch.setattr(test_db_session, "commit", fail_commit)

    assert release_expired_reservations(test_db_session) == {
        "expired_reservations": 0,
    }
