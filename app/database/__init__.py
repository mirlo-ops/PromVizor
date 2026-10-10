"""Слой данных «ПромВизор» (Этап 5 — Database).

SQLite без ORM. Репозиторий работает с контрактными объектами
(`SystemStatus`, `DowntimeEvent`, `Statistics`), наружу не отдаёт
строки sqlite3.

    from app.database import Database, EventRepository

    with Database("promvizor.db") as db:
        repo = EventRepository(db)
        repo.ensure_camera(1, "Камера №1")
        repo.save_event(event)
        stats = repo.statistics()
"""

from __future__ import annotations

from app.database.db import Database
from app.database.models import (
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    Camera,
    CameraRow,
    Column,
    EventRecord,
    Table,
    build_statistics,
    row_to_downtime_event,
    row_to_event_record,
    row_to_system_status,
)
from app.database.repository import EventRepository, utcnow_iso

__all__ = [
    "Database",
    "EventRepository",
    "utcnow_iso",
    "SCHEMA_VERSION",
    "SCHEMA_STATEMENTS",
    "Camera",
    "CameraRow",
    "EventRecord",
    "Table",
    "Column",
    "row_to_downtime_event",
    "row_to_event_record",
    "row_to_system_status",
    "build_statistics",
]