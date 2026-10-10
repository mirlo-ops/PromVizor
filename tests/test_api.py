"""Тесты Этапа 6 — FastAPI.

Запуск:

    .venv/bin/python tests/test_api.py
    .venv/bin/python -m pytest tests/test_api.py -v

Тесты не трогают рабочую базу: подключение подменяется временным
файлом, который удаляется после каждого теста.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.api.routes import close_database  # noqa: E402
from app.core.types import (  # noqa: E402
    DowntimeEvent,
    EventType,
    LineStatus,
    SystemStatus,
)
from app.database import EventRepository  # noqa: E402
from app.database.db import Database  # noqa: E402

#: Стоимость минуты — та же, что и в остальных тестах.
COST = 750.0


# --------------------------------------------------------------------------- #
# Изолированная база на каждый тест
# --------------------------------------------------------------------------- #
class fixture:
    """Подменяет подключение API на временную базу.

    Репозиторий создаётся заранее и наполняется данными: так тесты
    проверяют не только маршруты, но и что ответы содержат то, что
    реально лежит в базе.
    """

    def __init__(self, populate: bool = True):
        self.populate = populate
        self.client = None
        self.repo = None
        self._previous = {}

    def __enter__(self) -> "fixture":
        self._directory = tempfile.mkdtemp()
        self._path = str(Path(self._directory) / "api_test.db")

        db = Database(self._path)
        db.init_schema()
        self.repo = EventRepository(db)
        if self.populate:
            self._fill()

        # Подменяем подключение, которое отдаёт Depends(get_database)
        from app.api import routes as routes_module

        self._previous["db"] = getattr(routes_module.get_database, "_db", None)
        routes_module.get_database._db = db
        self._routes = routes_module
        self.client = TestClient(app)
        return self

    def _fill(self) -> None:
        repo = self.repo
        repo.ensure_camera(1, "Камера №1")
        repo.ensure_camera(2, "Камера №2")

        events = [
            DowntimeEvent(1, EventType.DOWNTIME_STARTED, "10:00", "", 0, 0.0),
            DowntimeEvent(1, EventType.DOWNTIME_FINISHED, "10:00", "10:05", 300, 3750.0),
            DowntimeEvent(1, EventType.DOWNTIME_STARTED, "11:00", "", 0, 0.0),
            DowntimeEvent(1, EventType.DOWNTIME_FINISHED, "11:00", "11:02", 120, 1500.0),
            DowntimeEvent(2, EventType.DOWNTIME_STARTED, "12:00", "", 0, 0.0),
            DowntimeEvent(2, EventType.DOWNTIME_FINISHED, "12:00", "12:10", 600, 7500.0),
        ]
        for event in events:
            repo.save_event(event, created_at=f"2026-10-0{1 + events.index(event) % 3}T10:00:00+00:00")

        repo.save_status(
            SystemStatus(1, LineStatus.WORKING, True, 0, 0.0),
            created_at="2026-10-01T09:00:00+00:00",
        )
        repo.save_status(
            SystemStatus(1, LineStatus.STOPPED, False, 134, 1675.0),
            created_at="2026-10-03T12:00:00+00:00",
        )

    def __exit__(self, *exc) -> None:
        self.client.close()
        self._routes.get_database._db = self._previous["db"]
        self.repo.db.close()
        for name in ("api_test.db", "api_test.db-wal", "api_test.db-shm"):
            Path(self._directory, name).unlink(missing_ok=True)
        Path(self._directory).rmdir()


# --------------------------------------------------------------------------- #
# Служебные маршруты
# --------------------------------------------------------------------------- #
def test_api_index_lists_endpoints():
    """Описание сервиса живёт на /api: корень отдан Dashboard."""
    with fixture(populate=False) as f:
        data = f.client.get("/api").json()
        assert data["service"] == "ПромВизор API"
        assert data["endpoints"]["status"] == "/api/status"
        assert data["endpoints"]["events"] == "/api/events"


def test_dashboard_is_served_at_root():
    """Dashboard раздаётся тем же сервером.

    Иначе его пришлось бы открывать как локальный файл, а браузер
    блокирует fetch со страницы file:// — интерфейс остался бы
    без данных.
    """
    with fixture(populate=False) as f:
        response = f.client.get("/")
        assert response.status_code == 200
        assert "ПромВизор" in response.text

        for asset in ("/css/styles.css", "/js/api.js", "/js/data.js", "/js/app.js"):
            response = f.client.get(asset)
            assert response.status_code == 200, asset
            assert response.text, asset


def test_health():
    with fixture(populate=False) as f:
        response = f.client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["schema_version"] >= 1


def test_config_returns_cost_per_minute():
    """Стоимость минуты нужна интерфейсу для подписи, но не для расчёта."""
    with fixture(populate=False) as f:
        body = f.client.get("/api/config").json()
        assert body["cost_per_minute"] > 0
        assert "camera_id" in body


# --------------------------------------------------------------------------- #
# Текущее состояние
# --------------------------------------------------------------------------- #
def test_status_returns_last_snapshot():
    with fixture() as f:
        body = f.client.get("/api/status").json()
        assert body["camera_id"] == 1
        assert body["line_status"] == LineStatus.STOPPED
        assert body["person_present"] is False
        assert body["downtime_seconds"] == 134
        assert body["estimated_loss"] == 1675.0


def test_status_on_empty_database_is_unknown():
    """Пустая база — не ошибка, а «нет данных»."""
    with fixture(populate=False) as f:
        response = f.client.get("/api/status")
        assert response.status_code == 200
        body = response.json()
        assert body["line_status"] == LineStatus.UNKNOWN
        assert body["downtime_seconds"] == 0
        assert body["estimated_loss"] == 0.0


def test_status_history_newest_first():
    with fixture() as f:
        body = f.client.get("/api/status/history").json()
        assert body["count"] == 2
        assert body["items"][0]["line_status"] == LineStatus.STOPPED
        assert body["items"][1]["line_status"] == LineStatus.WORKING


# --------------------------------------------------------------------------- #
# История простоя
# --------------------------------------------------------------------------- #
def test_events_list_returns_all():
    with fixture() as f:
        body = f.client.get("/api/events").json()
        assert body["count"] == 6
        assert body["total"] == 6


def test_events_filter_by_type():
    with fixture() as f:
        body = f.client.get("/api/events", params={"event_type": EventType.DOWNTIME_FINISHED}).json()
        assert body["count"] == 3
        assert all(item["event"] == EventType.DOWNTIME_FINISHED for item in body["items"])


def test_events_filter_by_camera():
    with fixture() as f:
        body = f.client.get("/api/events", params={"camera_id": 2}).json()
        assert body["count"] == 2
        assert all(item["camera_id"] == 2 for item in body["items"])


def test_events_pagination_limit_and_offset():
    """Страницы не должны перекрываться."""
    with fixture() as f:
        first = f.client.get("/api/events", params={"limit": 2}).json()
        second = f.client.get("/api/events", params={"limit": 2, "offset": 2}).json()

        assert first["count"] == 2
        assert second["count"] == 2
        assert first["total"] == 6  # total не зависит от limit
        first_ids = {(e["start_time"], e["event"]) for e in first["items"]}
        second_ids = {(e["start_time"], e["event"]) for e in second["items"]}
        assert not (first_ids & second_ids)


def test_events_unknown_type_rejected():
    with fixture() as f:
        response = f.client.get("/api/events", params={"event_type": "ВЗРЫВ"})
        assert response.status_code == 422
        assert "Неизвестный тип события" in response.json()["detail"]


def test_events_reversed_period_rejected():
    with fixture() as f:
        response = f.client.get(
            "/api/events",
            params={"since": "2026-10-10", "until": "2026-10-01"},
        )
        assert response.status_code == 422


def test_events_limit_is_bounded():
    """Огромный limit не должен выгружать всю таблицу."""
    with fixture() as f:
        assert f.client.get("/api/events", params={"limit": 10**9}).status_code == 422
        assert f.client.get("/api/events", params={"limit": 0}).status_code == 422
        assert f.client.get("/api/events", params={"limit": -5}).status_code == 422


def test_event_by_id():
    with fixture() as f:
        events = f.client.get("/api/events").json()["items"]
        target = events[0]

        response = f.client.get(f"/api/events/{1}")
        assert response.status_code == 200
        assert response.json()["camera_id"] == target["camera_id"]


def test_event_missing_gives_404():
    with fixture() as f:
        response = f.client.get("/api/events/9999")
        assert response.status_code == 404
        assert "не найдено" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Агрегаты
# --------------------------------------------------------------------------- #
def test_statistics_counts_only_finished():
    with fixture() as f:
        body = f.client.get("/api/statistics").json()
        # 300 + 120 + 600 секунд, начатые простоя не считаются
        assert body["events_count"] == 3
        assert body["total_downtime_seconds"] == 1020
        assert body["total_loss"] == 12750.0


def test_statistics_by_camera():
    with fixture() as f:
        body = f.client.get("/api/statistics/by-camera").json()
        by_id = {item["camera_id"]: item for item in body["items"]}
        assert by_id[1]["total_downtime_seconds"] == 420
        assert by_id[2]["total_downtime_seconds"] == 600


def test_statistics_on_empty_database():
    with fixture(populate=False) as f:
        body = f.client.get("/api/statistics").json()
        assert body["events_count"] == 0
        assert body["total_downtime_seconds"] == 0
        assert body["total_loss"] == 0.0


def test_cameras_list():
    with fixture() as f:
        body = f.client.get("/api/cameras").json()
        assert body["count"] == 2
        assert body["items"][0]["name"] == "Камера №1"


# --------------------------------------------------------------------------- #
# Демо-данные
# --------------------------------------------------------------------------- #
def test_event_carries_recorded_date():
    """Событие должно содержать дату записи: по одним «ЧЧ:ММ»
    сгруппировать события по суткам невозможно."""
    with fixture() as f:
        body = f.client.get("/api/events").json()
        for item in body["items"]:
            assert item["created_at"], "нет метки времени записи"
            assert item["created_at"].startswith("2026-10-")


def test_event_by_id_includes_recorded_date():
    with fixture() as f:
        body = f.client.get("/api/events/1").json()
        assert "created_at" in body
        assert body["created_at"]


def test_events_filters_by_recorded_date():
    with fixture() as f:
        body = f.client.get(
            "/api/events",
            params={"since": "2026-10-01", "until": "2026-10-01"},
        ).json()
        assert all(item["created_at"].startswith("2026-10-01") for item in body["items"])


def test_demo_seed_creates_data():
    with fixture(populate=False) as f:
        response = f.client.post("/api/demo/seed")
        assert response.status_code == 200
        body = response.json()
        assert body["events_created"] > 0

        stats = f.client.get("/api/statistics").json()
        assert stats["events_count"] > 0
        assert stats["total_loss"] > 0


def test_demo_seed_does_not_wipe_real_data():
    """По умолчанию демо не должен затирать то, что пришло с видео."""
    with fixture() as f:
        before = f.client.get("/api/events").json()["total"]
        f.client.post("/api/demo/seed")
        after = f.client.get("/api/events").json()["total"]
        assert after > before


def test_demo_seed_uses_real_formula():
    """Суммы демо должны считаться той же формулой, что и рабочие."""
    from app.core.types import calculate_loss

    with fixture(populate=False) as f:
        f.client.post("/api/demo/seed")
        events = f.client.get("/api/events", params={"limit": 1000}).json()["items"]

        finished = [e for e in events if e["event"] == EventType.DOWNTIME_FINISHED]
        assert finished
        for event in finished[:10]:
            assert event["estimated_loss"] == calculate_loss(event["duration_seconds"], COST)


def test_demo_data_has_no_future_events():
    """Событие из будущего попало бы в статистику за сегодня как случившееся."""
    from datetime import datetime, timezone

    with fixture(populate=False) as f:
        f.client.post("/api/demo/seed?days_back=5")
        body = f.client.get("/api/events", params={"limit": 1000}).json()

    now = datetime.now(timezone.utc).isoformat()
    # Метки времени записи API не отдаёт напрямую — проверяем через
    # сами сгенерированные данные
    from app.api.demo_data import generate_events

    stamped = [created_at for _, created_at in generate_events(days_back=5)]
    assert stamped, "демо-данные не создались"
    assert all(stamp <= now for stamp in stamped), "найдено событие из будущего"


def test_demo_data_is_chronological():
    """События должны идти по времени: /api/events сортирует по id."""
    from app.api.demo_data import generate_events

    stamped = [created_at for _, created_at in generate_events(days_back=5)]
    assert stamped == sorted(stamped)


def test_demo_downtimes_are_plausible():
    """Часовые простои выглядели бы как остановка завода."""
    from app.api.demo_data import generate_events

    finished = [
        event for event, _ in generate_events(days_back=5)
        if event.event == EventType.DOWNTIME_FINISHED
    ]
    assert finished
    for event in finished:
        assert 60 <= event.duration_seconds <= 60 * 60, "слишком длинный простой"
    total = sum(event.duration_seconds for event in finished)
    assert total / (5 * 86400) < 0.25, "линия простаивает слишком много"


def test_demo_status_shows_current_downtime():
    """Dashboard должен сразу показать живой пример, а не нули."""
    with fixture(populate=False) as f:
        f.client.post("/api/demo/seed?days_back=5")
        body = f.client.get("/api/status").json()
        assert body["line_status"] == LineStatus.STOPPED
        assert body["downtime_seconds"] > 0
        assert body["estimated_loss"] > 0


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