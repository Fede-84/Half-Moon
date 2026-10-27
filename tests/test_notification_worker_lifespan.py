import asyncio
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.core.config import settings
from backend.app.main import app


def test_enabled_lifespan_starts_and_stops_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notification_started = Event()
    fulfillment_started = Event()
    receipt_cleanup_started = Event()
    notification_stopped = Event()
    fulfillment_stopped = Event()
    receipt_cleanup_stopped = Event()

    async def fake_notification_worker(stop_event: asyncio.Event) -> None:
        notification_started.set()
        await stop_event.wait()
        notification_stopped.set()

    async def fake_fulfillment_worker(stop_event: asyncio.Event) -> None:
        fulfillment_started.set()
        await stop_event.wait()
        fulfillment_stopped.set()

    async def fake_receipt_cleanup_worker(
        stop_event: asyncio.Event,
    ) -> None:
        receipt_cleanup_started.set()
        await stop_event.wait()
        receipt_cleanup_stopped.set()

    monkeypatch.setattr(settings, "gmail_delivery_worker_enabled", True)
    monkeypatch.setattr(settings, "pos_receipt_cleanup_worker_enabled", True)
    monkeypatch.setattr(
        main_module,
        "_run_notification_worker",
        fake_notification_worker,
    )
    monkeypatch.setattr(
        main_module,
        "_run_fulfillment_worker",
        fake_fulfillment_worker,
    )
    monkeypatch.setattr(
        main_module,
        "_run_pos_receipt_cleanup_worker",
        fake_receipt_cleanup_worker,
    )

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert notification_started.wait(timeout=2)
        assert fulfillment_started.wait(timeout=2)
        assert receipt_cleanup_started.wait(timeout=2)

    assert notification_stopped.wait(timeout=2)
    assert fulfillment_stopped.wait(timeout=2)
    assert receipt_cleanup_stopped.wait(timeout=2)


def test_receipt_cleanup_worker_runs_when_gmail_worker_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_cleanup_started = Event()
    receipt_cleanup_stopped = Event()

    async def fake_receipt_cleanup_worker(
        stop_event: asyncio.Event,
    ) -> None:
        receipt_cleanup_started.set()
        await stop_event.wait()
        receipt_cleanup_stopped.set()

    monkeypatch.setattr(settings, "gmail_delivery_worker_enabled", False)
    monkeypatch.setattr(settings, "pos_receipt_cleanup_worker_enabled", True)
    monkeypatch.setattr(
        main_module,
        "_run_pos_receipt_cleanup_worker",
        fake_receipt_cleanup_worker,
    )

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert receipt_cleanup_started.wait(timeout=2)

    assert receipt_cleanup_stopped.wait(timeout=2)


def test_supervisor_restarts_worker_after_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restart_started = asyncio.Event()
    start_count = 0

    async def fail_once_then_wait(stop_event: asyncio.Event) -> None:
        nonlocal start_count
        start_count += 1
        if start_count == 1:
            raise RuntimeError("unexpected worker failure")
        restart_started.set()
        await stop_event.wait()

    async def exercise_supervisor() -> None:
        stop_event = asyncio.Event()
        monkeypatch.setattr(
            main_module,
            "WORKER_RESTART_INITIAL_BACKOFF_SECONDS",
            0.01,
        )
        monkeypatch.setattr(
            main_module,
            "WORKER_RESTART_MAX_BACKOFF_SECONDS",
            0.01,
        )
        supervisor_task = asyncio.create_task(
            main_module._supervise_background_worker(
                fail_once_then_wait,
                stop_event,
                "Test",
            )
        )

        await asyncio.wait_for(restart_started.wait(), timeout=2)
        stop_event.set()
        await asyncio.wait_for(supervisor_task, timeout=2)

    asyncio.run(exercise_supervisor())
    assert start_count == 2
