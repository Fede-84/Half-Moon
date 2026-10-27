import pytest

from backend.app.services.notification_worker import notification_retry_delay


@pytest.mark.parametrize(
    ("attempt_count", "expected_seconds"),
    [
        (1, 30),
        (2, 60),
        (3, 120),
        (4, 240),
        (5, 300),
        (10, 300),
    ],
)
def test_notification_retry_delay_is_capped(
    attempt_count: int,
    expected_seconds: int,
) -> None:
    assert notification_retry_delay(attempt_count).total_seconds() == expected_seconds


@pytest.mark.parametrize("attempt_count", [0, -1])
def test_notification_retry_delay_rejects_invalid_attempt_count(
    attempt_count: int,
) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        notification_retry_delay(attempt_count)
