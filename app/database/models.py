"""Таблицы и преобразование строк БД в объекты (Этап 5 — Database).

Разделение по файлам соответствует разделу 15 Handoff:

* `models.py` — схема и маппинг «строка ↔ объект»;
* `db.py` — подключение, создание схемы, транзакции;
* `repository.py` — операции чтения и записи.

Схема покрывает всё, что требует раздел 15:

| Требование Handoff      | Где хранится           |
|-------------------------|------------------------|
| камера                  | `cameras`              |
| начало простоя          | `downtime_events`      |
| конец простоя           | `downtime_events`      |
| продолжительность       | `downtime_events`      |
| состояние линии         | `status_log`           |
| наличие рабочего        | `status_log`           |
| рассчитанный ущерб      | `downtime_events`, `status_log` |

Используется только стандартная библиотека (`sqlite3`) — без ORM.
Для трёх таблиц и Alpha-версии ORM дала бы лишнюю зависимость и
слой абстракции поверх простых запросов.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.types import (
    DowntimeEvent,
    EventType,
    LineStatus,
    Statistics,
    SystemStatus,
    VideoSourceType,
)

#: Версия схемы. При изменении структуры таблиц значение повышается,
#: и `db.migrate()` применяет нужные шаги.
SCHEMA_VERSION = 1

#: Событие остановки, для которого ещё нет подтверждённого конца.
OPEN_DOWNTIME = ""

#: Метка времени в формате контракта «HH:MM».
CLOCK_FORMAT = "%H:%M"


# --------------------------------------------------------------------------- #
# Имена таблиц и столбцов
# --------------------------------------------------------------------------- #
class Table:
    CAMERAS = "cameras"
    DOWNTIME_EVENTS = "downtime_events"
    STATUS_LOG = "status_log"


class Column:
    ID = "id"
    NAME = "name"
    SOURCE_TYPE = "source_type"
    IS_DEMO = "is_demo"
    CAMERA_ID = "camera_id"
    EVENT = "event"
    START_TIME = "start_time"
    END_TIME = "end_time"
    DURATION_SECONDS = "duration_seconds"
    ESTIMATED_LOSS = "estimated_loss"
    LINE_STATUS = "line_status"
    PERSON_PRESENT = "person_present"
    DOWNTIME_SECONDS = "downtime_seconds"
    CREATED_AT = "created_at"


# --------------------------------------------------------------------------- #
# Схема
# --------------------------------------------------------------------------- #
CREATE_CAMERAS = f"""
CREATE TABLE IF NOT EXISTS {Table.CAMERAS} (
    {Column.ID}          INTEGER PRIMARY KEY AUTOINCREMENT,
    {Column.NAME}        TEXT    NOT NULL,
    {Column.SOURCE_TYPE} TEXT    NOT NULL DEFAULT '{VideoSourceType.DEMO}',
    {Column.IS_DEMO}     INTEGER NOT NULL DEFAULT 0,
    {Column.CREATED_AT}  TEXT    NOT NULL
)
"""

CREATE_DOWNTIME_EVENTS = f"""
CREATE TABLE IF NOT EXISTS {Table.DOWNTIME_EVENTS} (
    {Column.ID}               INTEGER PRIMARY KEY AUTOINCREMENT,
    {Column.CAMERA_ID}        INTEGER NOT NULL,
    {Column.EVENT}            TEXT    NOT NULL,
    {Column.START_TIME}        TEXT    NOT NULL,
    {Column.END_TIME}         TEXT    NOT NULL DEFAULT '',
    {Column.DURATION_SECONDS} INTEGER NOT NULL DEFAULT 0,
    {Column.ESTIMATED_LOSS}   REAL    NOT NULL DEFAULT 0,
    {Column.CREATED_AT}       TEXT    NOT NULL,
    FOREIGN KEY ({Column.CAMERA_ID}) REFERENCES {Table.CAMERAS}({Column.ID})
        ON DELETE CASCADE
)
"""

CREATE_STATUS_LOG = f"""
CREATE TABLE IF NOT EXISTS {Table.STATUS_LOG} (
    {Column.ID}               INTEGER PRIMARY KEY AUTOINCREMENT,
    {Column.CAMERA_ID}        INTEGER NOT NULL,
    {Column.LINE_STATUS}      TEXT    NOT NULL,
    {Column.PERSON_PRESENT}   INTEGER NOT NULL DEFAULT 0,
    {Column.DOWNTIME_SECONDS} INTEGER NOT NULL DEFAULT 0,
    {Column.ESTIMATED_LOSS}   REAL    NOT NULL DEFAULT 0,
    {Column.CREATED_AT}       TEXT    NOT NULL,
    FOREIGN KEY ({Column.CAMERA_ID}) REFERENCES {Table.CAMERAS}({Column.ID})
        ON DELETE CASCADE
)
"""

CREATE_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_events_camera "
    f"ON {Table.DOWNTIME_EVENTS} ({Column.CAMERA_ID})",
    f"CREATE INDEX IF NOT EXISTS idx_events_created "
    f"ON {Table.DOWNTIME_EVENTS} ({Column.CREATED_AT})",
    f"CREATE INDEX IF NOT EXISTS idx_events_type "
    f"ON {Table.DOWNTIME_EVENTS} ({Column.EVENT})",
    f"CREATE INDEX IF NOT EXISTS idx_status_camera "
    f"ON {Table.STATUS_LOG} ({Column.CAMERA_ID}, {Column.CREATED_AT})",
)

SCHEMA_STATEMENTS = (CREATE_CAMERAS, CREATE_DOWNTIME_EVENTS, CREATE_STATUS_LOG) + CREATE_INDEXES


# --------------------------------------------------------------------------- #
# Записи
# --------------------------------------------------------------------------- #
@dataclass
class CameraRow:
    """Камера в системе."""

    id: int
    name: str
    source_type: str = VideoSourceType.DEMO
    is_demo: bool = False
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            Column.ID: self.id,
            Column.NAME: self.name,
            Column.SOURCE_TYPE: self.source_type,
            Column.IS_DEMO: int(self.is_demo),
            Column.CREATED_AT: self.created_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> "CameraRow":
        return cls(
            id=int(row[Column.ID]),
            name=str(row[Column.NAME]),
            source_type=str(row[Column.SOURCE_TYPE]),
            is_demo=bool(row[Column.IS_DEMO]),
            created_at=str(row[Column.CREATED_AT] or ""),
        )


#: Алиас для ясности: контрактный объект камерой не предусмотрен,
#: поэтому таблица камер — это внутреннее понятие репозитория.
Camera = CameraRow


def row_to_downtime_event(row: Any) -> DowntimeEvent:
    """Строка `downtime_events` → контрактный `DowntimeEvent`."""
    return DowntimeEvent(
        camera_id=int(row[Column.CAMERA_ID]),
        event=str(row[Column.EVENT]),
        start_time=str(row[Column.START_TIME]),
        end_time=str(row[Column.END_TIME] or ""),
        duration_seconds=int(row[Column.DURATION_SECONDS] or 0),
        estimated_loss=float(row[Column.ESTIMATED_LOSS] or 0.0),
    )


@dataclass
class EventRecord:
    """Событие простоя вместе с меткой времени его записи.

    Контрактный `DowntimeEvent` содержит только «ЧЧ:ММ» — времени в
    нём суток нет. Интерфейсу, однако, нужно группировать события по
    дням (отчёт за месяц, таймлайн за сутки), и по одним часам минуты
    это сделать нельзя. Поэтому API отдаёт событие вместе с датой
    записи, не меняя сам контракт.
    """

    event: DowntimeEvent
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = self.event.to_dict()
        data["created_at"] = self.created_at
        return data


def row_to_event_record(row: Any) -> EventRecord:
    """Строка `downtime_events` → `EventRecord` с датой записи."""
    return EventRecord(
        event=row_to_downtime_event(row),
        created_at=str(row[Column.CREATED_AT] or ""),
    )


def row_to_system_status(row: Any) -> SystemStatus:
    """Строка `status_log` → контрактный `SystemStatus`."""
    return SystemStatus(
        camera_id=int(row[Column.CAMERA_ID]),
        line_status=str(row[Column.LINE_STATUS]),
        person_present=bool(row[Column.PERSON_PRESENT]),
        downtime_seconds=int(row[Column.DOWNTIME_SECONDS] or 0),
        estimated_loss=float(row[Column.ESTIMATED_LOSS] or 0.0),
    )


def build_statistics(
    total_seconds: int,
    total_loss: float,
    events_count: int,
) -> Statistics:
    """Собирает контрактные `Statistics` из результатов агрегации."""
    return Statistics(
        total_downtime_seconds=int(total_seconds),
        total_loss=round(float(total_loss), 2),
        events_count=int(events_count),
    )


#: Событие простоя в виде, пригодном для записи в БД.
DowntimeEventRow = DowntimeEvent

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_STATEMENTS",
    "Table",
    "Column",
    "CameraRow",
    "Camera",
    "row_to_downtime_event",
    "row_to_event_record",
    "row_to_system_status",
    "build_statistics",
    "OPEN_DOWNTIME",
    "LineStatus",
    "EventType",
    "EventRecord",
]