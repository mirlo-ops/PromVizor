"""События простоя (Этап 3 — Event Engine).

Внутренние события движка отделены от контракта `DowntimeEvent`
(app/core/types.py). Причины:

* таймер не знает про «строки времени в формате HH:MM» и стоимость
  минуты — это не его дело;
* `DowntimeEvent` — формат для БД и API, а движку удобнее работать
  с точной меткой времени и готовым числом секунд.

Преобразование в контракт выполняет метод `to_contract()` и
делается один раз — на границе с API/БД.

Формулировки статусов (раздел 7 Handoff):
    ✅ «Рабочий не обнаружен в контролируемой зоне камеры.»
    ❌ «Рабочий отсутствует на предприятии.»
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.types import DowntimeEvent, EventType, calculate_loss


@dataclass(frozen=True)
class DowntimeStarted:
    """Простой подтвердился: линия стоит, рабочего нет дольше порога."""

    camera_id: int
    at: float  # метка времени начала, сек


@dataclass(frozen=True)
class DowntimeFinished:
    """Линия заработала: простой завершён."""

    camera_id: int
    at: float  # метка времени окончания, сек
    started_at: Optional[float]  # метка времени начала, сек
    duration_seconds: float


def format_clock(at: float) -> str:
    """Переводит метку времени в «HH:MM» — формат контракта.

    Метка приходит в секундах от начала суток (для реального времени)
    либо из «демо-часов» DEMO-режима.
    """
    if at < 0:
        at = 0.0
    total_minutes = int(at // 60) % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def to_contract(
    started: DowntimeStarted | None = None,
    finished: DowntimeFinished | None = None,
    cost_per_minute: float = 0.0,
) -> Optional[DowntimeEvent]:
    """Преобразует внутреннее событие в контракт `DowntimeEvent`.

    Возвращает `None`, если передано ничего или переданы оба
    события сразу (так быть не должно).
    """
    if started is not None and finished is not None:
        raise ValueError("Ожидается либо начало простоя, либо его конец, не оба")

    if started is not None:
        return DowntimeEvent(
            camera_id=started.camera_id,
            event=EventType.DOWNTIME_STARTED,
            start_time=format_clock(started.at),
            end_time="",  # простой ещё не завершён
            duration_seconds=0,
            estimated_loss=0.0,
        )

    if finished is not None:
        duration = int(round(finished.duration_seconds))
        return DowntimeEvent(
            camera_id=finished.camera_id,
            event=EventType.DOWNTIME_FINISHED,
            start_time=format_clock(finished.started_at or 0.0),
            end_time=format_clock(finished.at),
            duration_seconds=duration,
            estimated_loss=calculate_loss(duration, cost_per_minute),
        )

    return None


def describe(event: DowntimeEvent) -> str:
    """Человекочитаемое описание события простоя."""
    if event.event == EventType.DOWNTIME_STARTED:
        return f"Простой начался в {event.start_time}"
    minutes = event.duration_seconds // 60
    seconds = event.duration_seconds % 60
    length = f"{minutes} мин {seconds} сек" if minutes else f"{seconds} сек"
    return (
        f"Простой завершён в {event.end_time}, длительность {length}"
    )