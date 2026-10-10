"""Event Engine «ПромВизор» (Этап 3).

Превращает результаты Computer Vision в события простоя:

    Линия стоит + рабочего нет дольше N секунд → DOWNTIME
    Линия заработала → простой завершён

Не зависит от видео и YOLO: на вход подаётся `VisionResult`.
"""

from __future__ import annotations

from app.engine.downtime import DowntimeSnapshot, DowntimeState, DowntimeTracker
from app.engine.event_engine import EngineEvent, EventEngine
from app.engine.events import (
    DowntimeFinished,
    DowntimeStarted,
    describe,
    format_clock,
    to_contract,
)
from app.engine.loss import (
    average_loss,
    build_statistics,
    format_loss,
    loss_for_minutes,
    loss_for_seconds,
    total_loss,
)

__all__ = [
    "EventEngine",
    "EngineEvent",
    "DowntimeTracker",
    "DowntimeState",
    "DowntimeSnapshot",
    "DowntimeStarted",
    "DowntimeFinished",
    "to_contract",
    "describe",
    "format_clock",
    "loss_for_seconds",
    "loss_for_minutes",
    "total_loss",
    "average_loss",
    "build_statistics",
    "format_loss",
]