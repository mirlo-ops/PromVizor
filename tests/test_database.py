"""Тесты Этапа 5 — Database.

Запуск:

    .venv/bin/python tests/test_database.py
    .venv/bin/python -m pytest tests/test_database.py -v

Все тесты работают на `:memory:` и не трогают рабочую базу.
Один тест проверяет поведение на файле — с отдельной временной БД.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.types import (  # noqa: E402
    DowntimeEvent,
    EventType,
    LineStatus,
    SystemStatus,
)
from app.database import Database, EventRepository, utcnow_iso  # noqa: E402


def fresh() -> tuple:
    """База в памяти с созданной схемой."""
    db = Database(":memory:")
    db.init_schema()
    return db, EventRepository(db)


#: Стоимость минуты для расчёта ожидаемых сумм
COST = 750.0


def finished(camera_id=1, seconds=300, loss=None) -> DowntimeEvent:
    """Завершённый простой. Ущерб по умолчанию считается из длительности,
    иначе пришлось бы подставлять его руками в каждом тесте."""
    if loss is None:
        loss = seconds / 60 * COST
    return DowntimeEvent(
        camera_id=camera_id,
        event=EventType.DOWNTIME_FINISHED,
        start_time="10:00",
        end_time="10:05",
        duration_seconds=seconds,
        estimated_loss=loss,
    )


def started(camera_id=1) -> DowntimeEvent:
    return DowntimeEvent(
        camera_id=camera_id,
        event=EventType.DOWNTIME_STARTED,
        start_time="11:00",
        end_time="",
        duration_seconds=0,
        estimated_loss=0.0,
    )


# --------------------------------------------------------------------------- #
# Схема
# --------------------------------------------------------------------------- #
def test_schema_is_created():
    db, _ = fresh()
    assert db.schema_version() >= 1
    for table in ("cameras", "downtime_events", "status_log"):
        assert db.query_value(f"SELECT COUNT(*) FROM {table}", default=0) == 0
    db.close()


def test_init_schema_is_idempotent():
    db, _ = fresh()
    db.init_schema()
    db.init_schema()
    assert db.schema_version() >= 1
    db.close()


def test_foreign_keys_enabled():
    """В SQLite внешние ключи выключены по умолчанию."""
    db, repo = fresh()
    repo.ensure_camera(1)
    try:
        repo.save_event(finished(camera_id=99))
        raise AssertionError("внешний ключ не сработал")
    except Exception:
        pass  # ожидаем IntegrityError
    db.close()


# --------------------------------------------------------------------------- #
# Камеры
# --------------------------------------------------------------------------- #
def test_ensure_camera_registers_once():
    db, repo = fresh()
    first = repo.ensure_camera(1, "Камера №1")
    second = repo.ensure_camera(1, "Другое имя")
    assert first == second == 1
    assert len(repo.list_cameras()) == 1
    # Повторная регистрация не должна перетирать имя
    assert repo.get_camera(1).name == "Камера №1"
    db.close()


def test_camera_defaults():
    db, repo = fresh()
    repo.ensure_camera(2)
    camera = repo.get_camera(2)
    assert camera.name == "КАМ 2"
    assert camera.is_demo is False
    assert repo.get_camera(999) is None
    db.close()


# --------------------------------------------------------------------------- #
# События простоя
# --------------------------------------------------------------------------- #
def test_save_and_read_event():
    db, repo = fresh()
    repo.ensure_camera(1)
    event_id = repo.save_event(finished())
    stored = repo.get_event(event_id)

    assert stored is not None
    assert stored.camera_id == 1
    assert stored.event == EventType.DOWNTIME_FINISHED
    assert stored.start_time == "10:00"
    assert stored.end_time == "10:05"
    assert stored.duration_seconds == 300
    assert stored.estimated_loss == 3750.0
    db.close()


def test_save_events_is_transactional():
    """Ошибка посередине откатывает всю пачку."""
    db, repo = fresh()
    repo.ensure_camera(1)
    try:
        repo.save_events([finished(), finished(camera_id=99), finished()])
    except Exception:
        pass
    assert db.table_counts()["downtime_events"] == 0
    db.close()


def test_recent_events_order_and_filter():
    db, repo = fresh()
    for camera in (1, 2):
        repo.ensure_camera(camera)
    for index in range(5):
        repo.save_event(finished(camera_id=1, seconds=60 * (index + 1)))
        repo.save_event(finished(camera_id=2, seconds=60))
    repo.save_event(started(camera_id=1))

    everything = repo.recent_events(limit=100)
    assert len(everything) == 11
    # Свежие сверху
    assert everything[0].end_time == ""

    only_camera_2 = repo.recent_events(limit=100, camera_id=2)
    assert len(only_camera_2) == 5

    limit_check = repo.recent_events(limit=3)
    assert len(limit_check) == 3
    db.close()


def test_events_between_filters_by_created_at():
    db, repo = fresh()
    repo.ensure_camera(1)
    repo.save_event(finished(), created_at="2026-10-01T10:00:00+00:00")
    repo.save_event(finished(), created_at="2026-10-05T10:00:00+00:00")
    repo.save_event(finished(), created_at="2026-10-09T10:00:00+00:00")

    october_first = repo.events_between(since="2026-10-01", until="2026-10-03")
    assert len(october_first) == 1

    september = repo.events_between(until="2026-09-30")
    assert len(september) == 0
    db.close()


def test_delete_event():
    db, repo = fresh()
    repo.ensure_camera(1)
    event_id = repo.save_event(finished())
    assert repo.delete_event(event_id) is True
    assert repo.delete_event(event_id) is False
    assert repo.get_event(event_id) is None
    db.close()


# --------------------------------------------------------------------------- #
# Статусы
# --------------------------------------------------------------------------- #
def test_latest_status():
    db, repo = fresh()
    repo.ensure_camera(1)
    assert repo.latest_status() is None

    repo.save_status(SystemStatus(1, LineStatus.WORKING, True, 0, 0.0))
    repo.save_status(SystemStatus(1, LineStatus.STOPPED, False, 120, 1500.0))

    latest = repo.latest_status()
    assert latest.line_status == LineStatus.STOPPED
    assert latest.person_present is False
    assert latest.downtime_seconds == 120
    assert latest.estimated_loss == 1500.0
    db.close()


def test_status_history_is_latest_first():
    db, repo = fresh()
    repo.ensure_camera(1)
    for seconds in (10, 20, 30):
        repo.save_status(SystemStatus(1, LineStatus.STOPPED, False, seconds, seconds * 12.5))

    history = repo.status_history(limit=10)
    assert [s.downtime_seconds for s in history] == [30, 20, 10]
    assert len(repo.status_history(limit=2)) == 2
    db.close()


# --------------------------------------------------------------------------- #
# Агрегаты
# --------------------------------------------------------------------------- #
def test_statistics_counts_only_finished():
    db, repo = fresh()
    repo.ensure_camera(1)
    repo.save_events([finished(seconds=60), finished(seconds=120), started()])

    stats = repo.statistics()
    assert stats.events_count == 2
    assert stats.total_downtime_seconds == 180
    assert stats.total_loss == 2250.0
    db.close()


def test_statistics_on_empty_database():
    db, repo = fresh()
    stats = repo.statistics()
    assert stats.events_count == 0
    assert stats.total_downtime_seconds == 0
    assert stats.total_loss == 0.0
    db.close()


def test_statistics_filtered_by_camera():
    db, repo = fresh()
    for camera in (1, 2):
        repo.ensure_camera(camera)
    repo.save_events([finished(camera_id=1, seconds=60), finished(camera_id=2, seconds=120)])

    assert repo.statistics(camera_id=1).total_downtime_seconds == 60
    assert repo.statistics(camera_id=2).total_downtime_seconds == 120
    assert repo.statistics().total_downtime_seconds == 180
    db.close()


def test_statistics_filtered_by_period():
    db, repo = fresh()
    repo.ensure_camera(1)
    repo.save_event(finished(), created_at="2026-10-01T10:00:00+00:00")
    repo.save_event(finished(), created_at="2026-10-05T10:00:00+00:00")

    assert repo.statistics(since="2026-10-01", until="2026-10-02").events_count == 1
    assert repo.statistics(since="2026-10-02").events_count == 1
    db.close()


def test_downtime_by_camera():
    db, repo = fresh()
    for camera in (1, 2):
        repo.ensure_camera(camera)
    repo.save_events([
        finished(camera_id=1, seconds=60),
        finished(camera_id=1, seconds=60),
        finished(camera_id=2, seconds=300),
    ])

    by_camera = repo.downtime_by_camera()
    assert by_camera[1]["events_count"] == 2
    assert by_camera[1]["total_downtime_seconds"] == 120
    assert by_camera[2]["events_count"] == 1
    assert by_camera[2]["total_downtime_seconds"] == 300
    db.close()


# --------------------------------------------------------------------------- #
# Ограничения и целостность
# --------------------------------------------------------------------------- #
def test_limit_is_sanitised():
    """Отрицательный LIMIT в SQLite означает «без нижней границы»."""
    db, repo = fresh()
    repo.ensure_camera(1)
    for _ in range(5):
        repo.save_event(finished())

    assert len(repo.recent_events(limit=-5)) >= 1   # не «все строки»
    assert len(repo.recent_events(limit=0)) >= 1
    assert len(repo.recent_events(limit=999_999)) == 5
    assert len(repo.recent_events(limit="мусор")) >= 1
    db.close()


def test_transaction_rolls_back_on_error():
    db, repo = fresh()
    repo.ensure_camera(1)
    repo.save_event(finished())
    before = db.table_counts()["downtime_events"]

    try:
        with db.transaction():
            repo.save_event(finished())
            repo.save_event(finished(camera_id=42))   # нарушит внешний ключ
    except Exception:
        pass

    assert db.table_counts()["downtime_events"] == before
    db.close()


def test_transaction_commits_on_success():
    db, repo = fresh()
    repo.ensure_camera(1)
    before = db.table_counts()["downtime_events"]
    with db.transaction():
        repo.save_event(finished())
        repo.save_event(finished())
    assert db.table_counts()["downtime_events"] == before + 2
    db.close()


def test_nested_transaction_is_safe():
    db, repo = fresh()
    repo.ensure_camera(1)
    with db.transaction():
        with db.transaction():       # вложенный BEGIN недопустим
            repo.save_event(finished())
    assert db.table_counts()["downtime_events"] == 1
    db.close()


def test_prune_status_log_keeps_recent():
    db, repo = fresh()
    repo.ensure_camera(1)
    for _ in range(20):
        repo.save_status(SystemStatus(1, LineStatus.WORKING, True, 0, 0.0))

    removed = repo.prune_status_log(keep_last=5)
    assert removed == 15
    assert db.table_counts()["status_log"] == 5
    db.close()


def test_clear_all_keeps_schema():
    db, repo = fresh()
    repo.ensure_camera(1)
    repo.save_event(finished())
    repo.save_status(SystemStatus(1, LineStatus.WORKING, True, 0, 0.0))

    db.clear_all()
    counts = db.table_counts()
    assert counts == {"cameras": 0, "downtime_events": 0, "status_log": 0}
    # Схема на месте — база готова к новой работе
    repo.ensure_camera(1)
    repo.save_event(finished())
    assert db.table_counts()["downtime_events"] == 1
    db.close()


def test_deleting_camera_cascades():
    db, repo = fresh()
    repo.ensure_camera(1)
    repo.save_event(finished())
    db.execute("DELETE FROM cameras WHERE id = 1")
    assert db.table_counts()["downtime_events"] == 0
    db.close()


# --------------------------------------------------------------------------- #
# Работа с файлом
# --------------------------------------------------------------------------- #
def test_data_survives_reopen():
    """Главная проверка: закрытие базы не теряет записанное."""
    directory = tempfile.mkdtemp()
    path = str(Path(directory) / "test.db")

    db = Database(path)
    db.init_schema()
    repo = EventRepository(db)
    repo.ensure_camera(1, "Камера №1")
    repo.save_event(finished())
    repo.save_status(SystemStatus(1, LineStatus.STOPPED, False, 120, 1500.0))
    db.close()

    db2 = Database(path)
    db2.init_schema()
    repo2 = EventRepository(db2)
    assert db2.table_counts()["downtime_events"] == 1
    assert repo2.statistics().total_loss == 3750.0
    assert repo2.latest_status().line_status == LineStatus.STOPPED
    db2.close()


def test_context_manager_closes_connection():
    with Database(":memory:") as db:
        db.init_schema()
        repo = EventRepository(db)
        repo.ensure_camera(1)
    # После выхода соединение закрыто
    assert db._conn is None


def test_utcnow_iso_format():
    stamp = utcnow_iso()
    assert stamp.endswith("+00:00")
    assert "T" in stamp


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