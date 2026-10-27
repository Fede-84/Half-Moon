from datetime import datetime, timedelta, timezone


def utc_deadline_after_14_days(anchor: datetime) -> datetime:
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("14-day deadline anchor must be timezone-aware")

    return anchor.astimezone(timezone.utc) + timedelta(days=14)
