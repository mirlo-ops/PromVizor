"""Общий контракт между Lev (backend/AI) и Gasun (API/Frontend/Telegram).

Этот файл — единственная точка обмена данными между частями системы.
Gasun не знает, каким образом система определила простой,
Lev не знает, каким образом данные отображаются на Dashboard.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


class LineStatus:
    """Состояние производственной линии (строковые константы)."""

    WORKING = "WORKING"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"

    ALL = (WORKING, STOPPED, UNKNOWN)


class EventType:
    """Типы событий для истории и Telegram."""

    DOWNTIME_STARTED = "DOWNTIME_STARTED"
    DOWNTIME_FINISHED = "DOWNTIME_FINISHED"

    ALL = (DOWNTIME_STARTED, DOWNTIME_FINISHED)


class VideoSourceType:
    """Тип источника видео."""

    DEMO = "demo"
    WEBCAM = "webcam"
    RTSP = "rtsp"

    ALL = (DEMO, WEBCAM, RTSP)


@dataclass
class SystemStatus:
    """Главный объект состояния системы.

    Lev создаёт → Gasun получает и отображает.
    """

    camera_id: int
    line_status: str
    person_present: bool
    downtime_seconds: int
    estimated_loss: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SystemStatus":
        return cls(
            camera_id=int(data["camera_id"]),
            line_status=str(data["line_status"]),
            person_present=bool(data["person_present"]),
            downtime_seconds=int(data["downtime_seconds"]),
            estimated_loss=float(data["estimated_loss"]),
        )


@dataclass
class DowntimeEvent:
    """Событие простоя для истории событий (/api/events)."""

    camera_id: int
    event: str
    start_time: str  # "02:15"
    end_time: str = ""  # "" пока простой не завершён
    duration_seconds: int = 0
    estimated_loss: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DowntimeEvent":
        return cls(
            camera_id=int(data["camera_id"]),
            event=str(data["event"]),
            start_time=str(data.get("start_time", "")),
            end_time=str(data.get("end_time", "")),
            duration_seconds=int(data.get("duration_seconds", 0)),
            estimated_loss=float(data.get("estimated_loss", 0.0)),
        )


@dataclass
class Statistics:
    """Агрегированная статистика (/api/statistics)."""

    total_downtime_seconds: int = 0
    total_loss: float = 0.0
    events_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Statistics":
        return cls(
            total_downtime_seconds=int(data.get("total_downtime_seconds", 0)),
            total_loss=float(data.get("total_loss", 0.0)),
            events_count=int(data.get("events_count", 0)),
        )
