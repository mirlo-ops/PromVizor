"""Схемы ответов API (Этап 6 — FastAPI, Handoff раздел 16).

Схемы повторяют контракт из `app/core/types.py` один в один:
те же имена полей и тот же смысл. Gasun получает ровно те данные,
о которых договорились, и не должен знать, откуда они взялись —
SQLite, файл или что-то ещё.

Разница только в одном: контракт — это dataclass-ы для внутреннего
обмена, а здесь Pydantic-схемы. Они же служат документацией:
по /docs видно, что именно отдаёт каждый endpoint.

Ущерб нигде не пересчитывается. Значение приходит из базы таким,
как его посчитал backend по формуле `calculate_loss()` — Dashboard
и Telegram только отображают его (Handoff, раздел 14).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.types import EventType, LineStatus, VideoSourceType


class ApiModel(BaseModel):
    """Общая база схем.

    `from_attributes=True` позволяет строить схему прямо из dataclass-ов
    контракта — не нужно вручную перечислять поля в словарь.
    """

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Текущее состояние
# --------------------------------------------------------------------------- #
class StatusResponse(ApiModel):
    """Ответ `GET /api/status`.

    Поля совпадают с контрактным `SystemStatus`.
    """

    camera_id: int = Field(..., examples=[1])
    line_status: str = Field(
        ...,
        description="WORKING / STOPPED / UNKNOWN",
        examples=[LineStatus.STOPPED],
    )
    person_present: bool = Field(..., examples=[False])
    downtime_seconds: int = Field(..., examples=[134])
    estimated_loss: float = Field(..., examples=[1675.0])


class StatusHistoryResponse(ApiModel):
    """Ответ `GET /api/status/history` — снимки состояния, свежие сверху."""

    items: List[StatusResponse]
    count: int


# --------------------------------------------------------------------------- #
# История простоя
# --------------------------------------------------------------------------- #
class EventResponse(ApiModel):
    """Ответ по одному событию.

    Поля совпадают с контрактным `DowntimeEvent`:

    * `start_time` / `end_time` — «ЧЧ:ММ», как требует контракт;
    * у события `DOWNTIME_STARTED` `end_time` пуст, длительность и
      ущерб равны нулю: простой ещё не завершён, считать нечего.
    """

    camera_id: int = Field(..., examples=[1])
    event: str = Field(..., examples=[EventType.DOWNTIME_FINISHED])
    start_time: str = Field(..., examples=["12:40"])
    end_time: str = Field("", examples=["12:42"])
    duration_seconds: int = Field(0, examples=[120])
    estimated_loss: float = Field(0.0, examples=[1500.0])
    id: int = Field(
        0,
        description=(
            "Идентификатор строки в базе, строго возрастает. Бот "
            "запоминает последний прочитанный id и берёт только "
            "следующие — так событие не присылается дважды. "
            "В контракте `DowntimeEvent` этого поля нет: контракт "
            "от его добавления не меняется."
        ),
        examples=[181],
    )
    created_at: str = Field(
        "",
        description=(
            "Метка времени записи события в базе, ISO 8601 UTC. "
            "В контракте `DowntimeEvent` её нет — там только «ЧЧ:ММ», "
            "а интерфейсу нужно группировать события по суткам. "
            "Контракт от этого не меняется."
        ),
        examples=["2026-10-09T11:23:00+00:00"],
    )


class EventListResponse(ApiModel):
    """Ответ `GET /api/events` — история событий, свежие сверху."""

    items: List[EventResponse]
    count: int
    total: int = Field(
        ...,
        description="Сколько всего событий подходит под фильтр, без учёта limit",
    )


# --------------------------------------------------------------------------- #
# Агрегаты
# --------------------------------------------------------------------------- #
class StatisticsResponse(ApiModel):
    """Ответ `GET /api/statistics`.

    Поля совпадают с контрактным `Statistics`. Считаются только
    завершённые простоя: у начатого ещё нет ни длительности,
    ни ущерба.
    """

    total_downtime_seconds: int = Field(0, examples=[600])
    total_loss: float = Field(0.0, examples=[7500.0])
    events_count: int = Field(0, examples=[5])


class CameraStatistics(BaseModel):
    """Простой и ущерб по одной камере."""

    camera_id: int
    total_downtime_seconds: int = 0
    total_loss: float = 0.0
    events_count: int = 0


class StatisticsByCameraResponse(ApiModel):
    """Ответ `GET /api/statistics/by-camera`."""

    items: List[CameraStatistics]


# --------------------------------------------------------------------------- #
# Камеры
# --------------------------------------------------------------------------- #
class CameraResponse(ApiModel):
    """Камера в системе."""

    id: int = Field(..., examples=[1])
    name: str = Field(..., examples=["Камера №1"])
    source_type: str = Field(
        VideoSourceType.DEMO,
        description="demo / webcam / rtsp",
    )
    is_demo: bool = Field(False, description="Демонстрационный ролик, а не живая камера")
    created_at: str = Field("", description="Метка времени регистрации, ISO 8601 UTC")


class CameraListResponse(ApiModel):
    """Ответ `GET /api/cameras`."""

    items: List[CameraResponse]
    count: int


# --------------------------------------------------------------------------- #
# Служебное
# --------------------------------------------------------------------------- #
class ConfigResponse(ApiModel):
    """Ответ `GET /api/config` — значения, нужные для отображения.

    Отдаём стоимость минуты простоя, чтобы интерфейс мог показать
    «за минуту — 750 ₽», но пересчитывать по ней ущерб он не должен:
    готовое значение всегда приходит из поля `estimated_loss`.
    """

    cost_per_minute: float = Field(..., examples=[750.0])
    camera_id: int
    default_source: str
    downtime_threshold_seconds: int
    timezone: str = Field("UTC", description="Метки времени в базе хранятся в UTC")


class HealthResponse(ApiModel):
    """Ответ `GET /api/health` — проверка живости для оркестраторов."""

    status: str = Field("ok")
    database: str = Field("ok", description="ok / error")
    schema_version: int = 0
    server_time: datetime


class ErrorResponse(ApiModel):
    """Единый формат ошибки — чтобы клиенту не приходилось разбирать
    два разных вида ответа об ошибке."""

    detail: str


#: Диапазон дат для фильтров: оба края включаются.
#: Тип `date`, а не `datetime`, потому что Dashboard выбирает день.
DateFilter = Optional[date]


__all__ = [
    "ApiModel",
    "StatusResponse",
    "StatusHistoryResponse",
    "EventResponse",
    "EventListResponse",
    "StatisticsResponse",
    "CameraStatistics",
    "StatisticsByCameraResponse",
    "CameraResponse",
    "CameraListResponse",
    "ConfigResponse",
    "HealthResponse",
    "ErrorResponse",
]