"""Тесты Этапа 4 — Экономика: таймер, стоимость минуты, расчёт ущерба.

Запуск:

    .venv/bin/python tests/test_economics.py
    .venv/bin/python -m pytest tests/test_economics.py -v

Видео и YOLO не нужны: движок принимает `VisionResult`, а время
передаётся параметром `now`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.env import env_bool, env_float, env_int, env_str, parse_env  # noqa: E402
from app.core.types import (  # noqa: E402
    DowntimeEvent,
    EventType,
    LineStatus,
    calculate_loss,
)
from app.engine import (  # noqa: E402
    EventEngine,
    average_loss,
    build_statistics,
    format_loss,
    loss_for_minutes,
    loss_for_seconds,
    total_loss,
)
from app.vision import VisionResult  # noqa: E402

WORKING = LineStatus.WORKING
STOPPED = LineStatus.STOPPED

#: Стоимость минуты из раздела 13 Handoff
COST = 750.0


# --------------------------------------------------------------------------- #
# Формула ущерба (раздел 13)
# --------------------------------------------------------------------------- #
def test_formula_matches_handoff_example():
    """Пример из Handoff: 100 минут × 750 ₽ = 75 000 ₽."""
    assert calculate_loss(100 * 60, COST) == 75_000.0


def test_formula_minutes_and_seconds_agree():
    assert loss_for_minutes(100, COST) == loss_for_seconds(6000, COST) == 75_000.0


def test_formula_rounds_to_copecks():
    """1 секунда простоя: 750/60 = 12.5 ₽."""
    assert calculate_loss(1, COST) == 12.5
    # полсекунды округляются до целых секунд
    assert calculate_loss(0.4, COST) == 0.0


def test_negative_duration_is_protected():
    assert calculate_loss(-100, COST) == 0.0


def test_zero_cost_gives_zero_loss():
    assert calculate_loss(3600, 0.0) == 0.0


# --------------------------------------------------------------------------- #
# Агрегация
# --------------------------------------------------------------------------- #
def finished_event(seconds: int, cost: float = COST) -> DowntimeEvent:
    return DowntimeEvent(
        camera_id=1,
        event=EventType.DOWNTIME_FINISHED,
        start_time="10:00",
        end_time="10:05",
        duration_seconds=seconds,
        estimated_loss=calculate_loss(seconds, cost),
    )


def started_event() -> DowntimeEvent:
    return DowntimeEvent(
        camera_id=1,
        event=EventType.DOWNTIME_STARTED,
        start_time="10:00",
        end_time="",
        duration_seconds=0,
        estimated_loss=0.0,
    )


def test_total_loss_sums_events():
    # 60 с = 750 ₽, 120 с = 1500 ₽, вместе 2250 ₽.
    # Складывать по событиям и умножать суммарное время на стоимость
    # минуты дают одно и то же, потому что формула линейна.
    assert total_loss([60, 120], COST) == 2_250.0
    assert total_loss([60, 120], COST) == calculate_loss(180, COST)


def test_statistics_counts_only_finished():
    """Начатый простой не имеет ни длительности, ни ущерба."""
    events = [started_event(), finished_event(60), finished_event(120)]
    stats = build_statistics(events, COST)

    assert stats.events_count == 2
    assert stats.total_downtime_seconds == 180
    assert stats.total_loss == calculate_loss(180, COST)


def test_statistics_on_empty_list():
    stats = build_statistics([], COST)
    assert stats.events_count == 0
    assert stats.total_loss == 0.0


def test_average_loss_per_event():
    events = [finished_event(60), finished_event(180)]
    stats = build_statistics(events, COST)
    average = average_loss([e.duration_seconds for e in events], COST)
    assert average == calculate_loss(stats.total_downtime_seconds // stats.events_count, COST)


def test_average_loss_on_empty():
    assert average_loss([], COST) == 0.0


def test_format_loss_uses_spaces():
    assert format_loss(75_000) == "75 000 ₽"
    assert format_loss(1875) == "1 875 ₽"


# --------------------------------------------------------------------------- #
# Стоимость минуты из окружения
# --------------------------------------------------------------------------- #
def test_parse_env_basics():
    parsed = parse_env(
        "\n".join([
            "# комментарий",
            "COST_PER_MINUTE=1200",
            "RTSP_URL=rtsp://cam:554/stream",
            'TOKEN="quoted value"',
            "SINGLE='single'",
            "export EXPORTED=yes",
            "EMPTY=",
            "BROKEN_LINE",
        ])
    )
    assert parsed["COST_PER_MINUTE"] == "1200"
    assert parsed["RTSP_URL"] == "rtsp://cam:554/stream"
    assert parsed["TOKEN"] == "quoted value"
    assert parsed["SINGLE"] == "single"
    assert parsed["EXPORTED"] == "yes"
    assert parsed["EMPTY"] == ""
    assert "BROKEN_LINE" not in parsed


def test_env_typed_readers():
    saved = {k: os.environ.get(k) for k in ("T_STR", "T_FLOAT", "T_INT", "T_BOOL")}
    try:
        os.environ["T_STR"] = "hello"
        os.environ["T_FLOAT"] = "12.5"
        os.environ["T_INT"] = "42"
        os.environ["T_BOOL"] = "yes"
        assert env_str("T_STR") == "hello"
        assert env_float("T_FLOAT", 0.0) == 12.5
        assert env_int("T_INT", 0) == 42
        assert env_bool("T_BOOL") is True
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_env_falls_back_on_bad_value():
    """Мусор в переменной не должен ломать запуск."""
    saved = os.environ.get("T_BAD")
    try:
        os.environ["T_BAD"] = "не число"
        assert env_float("T_BAD", 750.0) == 750.0
        assert env_int("T_BAD", 30) == 30
        assert env_int("T_MISSING_ABC", 7) == 7
    finally:
        if saved is None:
            os.environ.pop("T_BAD", None)
        else:
            os.environ["T_BAD"] = saved


def test_env_file_loads_and_env_wins(tmp=None):
    """Файл читается, но реальное окружение имеет приоритет."""
    directory = Path(tempfile.mkdtemp())
    env_file = directory / ".env"
    env_file.write_text("COST_PER_MINUTE=1200\n", encoding="utf-8")

    from app.core.env import load_env

    saved = os.environ.get("COST_PER_MINUTE")
    try:
        os.environ.pop("COST_PER_MINUTE", None)
        load_env(str(env_file))
        assert os.environ["COST_PER_MINUTE"] == "1200"

        # Перекрываем окружением — файл не должен его испортить
        os.environ["COST_PER_MINUTE"] = "9999"
        load_env(str(env_file))
        assert os.environ["COST_PER_MINUTE"] == "9999"
    finally:
        if saved is None:
            os.environ.pop("COST_PER_MINUTE", None)
        else:
            os.environ["COST_PER_MINUTE"] = saved


def test_missing_env_file_is_not_an_error():
    from app.core.env import load_env
    assert load_env("/no/such/file/.env") == {}


# --------------------------------------------------------------------------- #
# Ущерб на реальных событиях движка
# --------------------------------------------------------------------------- #
def _engine_with_cost(cost: float) -> EventEngine:
    return EventEngine(
        camera_id=1,
        threshold_seconds=5,
        resume_confirm_seconds=0,
        cost_per_minute=cost,
        clock=lambda: 0.0,
    )


def _run_downtime(engine: EventEngine, offset: float = 0.0):
    """Прогоняет один цикл простоя и возвращает события движка."""
    started = finished = None
    for x in range(0, 10):
        engine.update(VisionResult(True, WORKING), now=offset + x)
    for x in range(10, 60):
        events = engine.update(VisionResult(False, STOPPED), now=offset + x)
        if events:
            started = events[0]
            break
    for x in range(60, 140):
        events = engine.update(VisionResult(True, WORKING), now=offset + x)
        if events:
            finished = events[0]
            break
    return started, finished


def test_loss_on_real_engine_events():
    engine = _engine_with_cost(COST)
    started, finished = _run_downtime(engine)

    assert started is not None and finished is not None
    # Ущерб взят из того же расчёта, что и в контракте
    expected = calculate_loss(int(round(finished.duration_seconds)), COST)
    assert finished.estimated_loss == expected


def test_cost_change_changes_loss():
    """Стоимость минуты влияет на итог."""
    _, cheap = _run_downtime(_engine_with_cost(750.0))
    _, pricey = _run_downtime(_engine_with_cost(1500.0))

    assert pricey.estimated_loss == cheap.estimated_loss * 2


def test_engine_event_to_contract():
    """Результат движка сразу готов для БД и API."""
    engine = _engine_with_cost(COST)
    started, finished = _run_downtime(engine)

    start_contract = started.to_contract(COST)
    assert isinstance(start_contract, DowntimeEvent)
    assert start_contract.event == EventType.DOWNTIME_STARTED
    assert start_contract.end_time == ""
    assert start_contract.estimated_loss == 0.0

    finish_contract = finished.to_contract(COST)
    assert finish_contract.event == EventType.DOWNTIME_FINISHED
    assert finish_contract.duration_seconds > 0
    assert finish_contract.estimated_loss == calculate_loss(
        finish_contract.duration_seconds, COST
    )


def test_statistics_from_engine_events():
    engine = _engine_with_cost(COST)
    contract_events = []
    for k in range(3):
        started, finished = _run_downtime(engine, offset=float(k * 1000))
        contract_events.append(started.to_contract(COST))
        contract_events.append(finished.to_contract(COST))

    stats = build_statistics(contract_events, COST)
    assert stats.events_count == 3
    assert stats.total_downtime_seconds > 0
    assert stats.total_loss == calculate_loss(stats.total_downtime_seconds, COST)


def test_status_loss_matches_contract_loss():
    """Пока простой идёт, ущерб в SystemStatus считается по той же
    формуле, что и в контрактном событии.

    Проверять надо ВО ВРЕМЯ простоя: после события FINISHED простоя
    уже нет, и `status()` закономерно возвращает нулевой ущерб.
    """
    engine = _engine_with_cost(COST)

    status = None
    for x in range(0, 10):
        engine.update(VisionResult(True, WORKING), now=float(x))
    for x in range(10, 60):
        engine.update(VisionResult(False, STOPPED), now=float(x))
        status = engine.status(VisionResult(False, STOPPED), now=float(x))
        if status.downtime_seconds > 0:
            break

    assert status is not None and status.downtime_seconds > 0, "Простой должен идти"
    assert status.estimated_loss == calculate_loss(status.downtime_seconds, COST)

    # и это же значение совпадает с контрактным событием по завершении
    for x in range(60, 140):
        events = engine.update(VisionResult(True, WORKING), now=float(x))
        if events:
            contract = events[0].to_contract(COST)
            assert contract.estimated_loss == calculate_loss(
                contract.duration_seconds, COST
            )
            break


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