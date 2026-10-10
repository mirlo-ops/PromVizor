"""Тесты Event Engine (Этап 3).

Запуск:

    .venv/bin/python tests/test_engine.py
    .venv/bin/python -m pytest tests/test_engine.py -v

Видео и YOLO не нужны: движок принимает `VisionResult` напрямую,
а время передаётся параметром `now`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.types import (  # noqa: E402
    DowntimeEvent,
    EventType,
    LineStatus,
    SystemStatus,
    calculate_loss,
)
from app.engine import (  # noqa: E402
    DowntimeState,
    DowntimeStarted,
    DowntimeTracker,
    EventEngine,
    format_clock,
    to_contract,
)
from app.vision import VisionResult  # noqa: E402

WORKING = LineStatus.WORKING
STOPPED = LineStatus.STOPPED
UNKNOWN = LineStatus.UNKNOWN


def vision(line: str, person: bool) -> VisionResult:
    return VisionResult(person_present=person, line_status=line)


def engine(threshold: float = 30.0, resume: float = 0.0, cost: float = 750.0) -> EventEngine:
    return EventEngine(
        camera_id=1,
        threshold_seconds=threshold,
        resume_confirm_seconds=resume,
        cost_per_minute=cost,
        clock=lambda: 0.0,
    )


# --------------------------------------------------------------------------- #
# Сценарии Handoff, раздел 6
# --------------------------------------------------------------------------- #
def test_normal_working_with_person():
    """6.1 — линия работает, рабочий на месте. Простоя нет."""
    e = engine()
    for t in range(0, 60, 5):
        assert e.update(vision(WORKING, True), now=t) == []
    assert e.state == DowntimeState.IDLE


def test_person_absent_but_line_working():
    """6.3 — рабочий ушёл, но линия работает. Простоя нет."""
    e = engine()
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(30, 150, 5):
        assert e.update(vision(WORKING, False), now=t) == []
    assert e.state == DowntimeState.IDLE


def test_line_stopped_but_person_present():
    """6.2 — линия стоит, рабочий на месте (обслуживание). Простоя нет."""
    e = engine()
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(30, 150, 5):
        assert e.update(vision(STOPPED, True), now=t) == []
    assert e.state == DowntimeState.IDLE


def test_main_downtime_scenario():
    """6.4 — линия стоит и рабочего нет дольше порога → простой."""
    e = engine(threshold=30)
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)

    started = None
    for t in range(30, 120, 5):
        events = e.update(vision(STOPPED, False), now=t)
        if events:
            started = events[0]
            break

    assert started is not None, "Простой должен быть зафиксирован"
    assert started.is_started
    # начало простоя — момент остановки, а не момент подтверждения
    assert started.at == 30.0
    assert e.is_downtime


def test_downtime_finishes_when_line_restarts():
    e = engine(threshold=30)
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(30, 120, 5):
        e.update(vision(STOPPED, False), now=t)
    assert e.is_downtime

    events = []
    for t in range(120, 150, 5):
        events.extend(e.update(vision(WORKING, True), now=t))

    finished = [ev for ev in events if not ev.is_started]
    assert finished, "Простой должен завершиться"
    assert finished[0].duration_seconds == 90.0     # с 30 до 120
    assert e.state == DowntimeState.IDLE


def test_loss_matches_formula():
    """Ущерб считается по формуле раздела 13."""
    e = engine(threshold=30)
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(30, 120, 5):
        e.update(vision(STOPPED, False), now=t)
    events = []
    for t in range(120, 150, 5):
        events.extend(e.update(vision(WORKING, True), now=t))

    loss = [ev for ev in events if not ev.is_started][0].estimated_loss
    assert loss == calculate_loss(90, 750)


# --------------------------------------------------------------------------- #
# Защита от ложных событий (раздел 23, этап 3)
# --------------------------------------------------------------------------- #
def test_short_stop_is_not_downtime():
    """Короче порога — это помеха, а не простой."""
    e = engine(threshold=30)
    for t in range(0, 10, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(10, 25, 5):           # стоит всего 15 сек
        assert e.update(vision(STOPPED, False), now=t) == []
    for t in range(25, 60, 5):
        assert e.update(vision(WORKING, True), now=t) == []
    assert e.state == DowntimeState.IDLE


def test_unknown_does_not_start_downtime():
    """UNKNOWN — нет данных. Это не STOPPED."""
    e = engine(threshold=30)
    for t in range(0, 120, 5):
        assert e.update(vision(UNKNOWN, False), now=t) == []
    assert e.state == DowntimeState.IDLE


def test_unknown_does_not_finish_downtime():
    """Нет данных — нельзя утверждать, что линия заработала."""
    e = engine(threshold=30)
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(30, 120, 5):
        e.update(vision(STOPPED, False), now=t)
    assert e.is_downtime

    for t in range(120, 200, 5):
        assert e.update(vision(UNKNOWN, False), now=t) == []
    assert e.is_downtime, "Простой не должен закрываться по неопределённым данным"


def test_resume_confirmation_prevents_flicker():
    """Микропаузы не должны плодить пары событий."""
    e = engine(threshold=30, resume=10)
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(30, 90, 5):
        e.update(vision(STOPPED, False), now=t)
    assert e.is_downtime

    # короткая пауза короче подтверждения — простой продолжается
    for t in range(90, 95, 5):
        assert e.update(vision(WORKING, True), now=t) == []
    assert e.is_downtime


def test_time_must_be_monotonic():
    e = engine()
    e.update(vision(WORKING, True), now=10.0)
    try:
        e.update(vision(WORKING, True), now=5.0)
    except ValueError:
        return
    raise AssertionError("Время назад должно приводить к ошибке")


def test_zero_threshold_starts_immediately():
    e = engine(threshold=0)
    e.update(vision(WORKING, True), now=0.0)
    events = e.update(vision(STOPPED, False), now=1.0)
    assert len(events) == 1 and events[0].is_started


# --------------------------------------------------------------------------- #
# Контракт
# --------------------------------------------------------------------------- #
def test_system_status_is_built():
    e = engine(threshold=30)
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    for t in range(30, 120, 5):
        e.update(vision(STOPPED, False), now=t)

    status = e.status(vision(STOPPED, False), now=100.0)
    assert isinstance(status, SystemStatus)
    assert status.camera_id == 1
    assert status.line_status == STOPPED
    assert status.person_present is False
    assert status.downtime_seconds == 70              # с 30 до 100
    assert status.estimated_loss == calculate_loss(70, 750)


def test_contract_event_started():
    e = engine(threshold=30)
    for t in range(0, 30, 5):
        e.update(vision(WORKING, True), now=t)
    events = None
    for t in range(30, 120, 5):
        events = e.update(vision(STOPPED, False), now=t)
        if events:
            break

    contract = to_contract(started=events[0], cost_per_minute=750)
    assert isinstance(contract, DowntimeEvent)
    assert contract.event == EventType.DOWNTIME_STARTED
    assert contract.end_time == ""
    assert contract.estimated_loss == 0.0
    assert contract.start_time == format_clock(30.0)


def test_contract_event_finished():
    from app.engine import DowntimeFinished

    finished = DowntimeFinished(camera_id=1, at=7200.0, started_at=6600.0, duration_seconds=600)
    contract = to_contract(finished=finished, cost_per_minute=750)

    assert contract.event == EventType.DOWNTIME_FINISHED
    assert contract.start_time == "01:50"
    assert contract.end_time == "02:00"
    assert contract.duration_seconds == 600
    assert contract.estimated_loss == 7500.0


def test_format_clock_wraps_at_midnight():
    assert format_clock(0) == "00:00"
    assert format_clock(60 * 60 * 13 + 300) == "13:05"
    assert format_clock(-5) == "00:00"          # защита от отрицательного


# --------------------------------------------------------------------------- #
# Таймер напрямую
# --------------------------------------------------------------------------- #
def test_tracker_rejects_bad_arguments():
    for bad in (-1,):
        try:
            DowntimeTracker(threshold_seconds=bad)
        except ValueError:
            continue
        raise AssertionError("Отрицательный порог должен приводить к ошибке")


def test_tracker_reset_clears_state():
    tracker = DowntimeTracker(threshold_seconds=1, camera_id=1)
    tracker.update(True, now=0.0)
    tracker.update(True, now=5.0)
    assert tracker.state == DowntimeState.DOWNTIME
    tracker.reset()
    assert tracker.state == DowntimeState.IDLE


def test_tracker_snapshot_during_pending():
    tracker = DowntimeTracker(threshold_seconds=30, camera_id=1)
    tracker.update(True, now=0.0)
    snap = tracker.snapshot(10.0)
    assert snap.state == DowntimeState.IDLE
    assert snap.pending_seconds == 10.0
    assert snap.downtime_seconds == 0.0


# --------------------------------------------------------------------------- #
# Раннер без pytest
# --------------------------------------------------------------------------- #
def _main() -> int:
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = []
    for name, func in tests:
        try:
            func()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"  ✗ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")

    print(f"\n{len(tests) - len(failed)}/{len(tests)} тестов пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())