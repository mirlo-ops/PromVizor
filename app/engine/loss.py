"""Расчёт ущерба (Этап 3 — Event Engine).

Формула (раздел 13 Handoff):

    loss = downtime_minutes × cost_per_minute

Считает ТОЛЬКО backend (Lev). Dashboard и Telegram отображают
готовую цифру и не пересчитывают её (раздел 14).

Сама формула живёт в `app/core/types.py` → `calculate_loss()`,
чтобы был ровно один источник истины. Здесь — тонкий слой
удобных обёрток для движка и БД.
"""

from __future__ import annotations

from typing import Iterable

from app.core.types import DowntimeEvent, EventType, Statistics, calculate_loss


def loss_for_seconds(downtime_seconds: float, cost_per_minute: float) -> float:
    """Ущерб за конкретную длительность простоя, ₽."""
    return calculate_loss(int(round(downtime_seconds)), cost_per_minute)


def loss_for_minutes(downtime_minutes: float, cost_per_minute: float) -> float:
    """Ущерб за длительность, заданную в минутах, ₽."""
    return calculate_loss(int(round(downtime_minutes * 60)), cost_per_minute)


def total_loss(durations_seconds: Iterable[float], cost_per_minute: float) -> float:
    """Суммарный ущерб по нескольким простоям, ₽."""
    return calculate_loss(int(round(sum(durations_seconds))), cost_per_minute)


def format_loss(value: float) -> str:
    """Формат суммы для вывода: «75 000 ₽»."""
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def average_loss(durations_seconds, cost_per_minute: float) -> float:
    """Средний ущерб на одно событие простоя, ₽."""
    items = list(durations_seconds)
    if not items:
        return 0.0
    return calculate_loss(int(round(sum(items) / len(items))), cost_per_minute)


def build_statistics(events, cost_per_minute: float) -> Statistics:
    """Собирает `Statistics` по завершённым событиям простоя.

    Считает ТОЛЬКО события вида DOWNTIME_FINISHED: у начатого простоя
    ещё нет ни длительности, ни ущерба, и включение его в статистику
    занижало бы средние значения.

    Аргумент events — любой iterable из `DowntimeEvent`
    (строки из БД, результат to_contract() и т.п.).
    """
    finished = [e for e in events if e.event == EventType.DOWNTIME_FINISHED]

    total_seconds = sum(int(e.duration_seconds) for e in finished)
    total_loss = sum(float(e.estimated_loss) for e in finished)

    return Statistics(
        total_downtime_seconds=total_seconds,
        total_loss=round(total_loss, 2),
        events_count=len(finished),
    )


__all__ = [
    "calculate_loss",
    "loss_for_seconds",
    "loss_for_minutes",
    "total_loss",
    "average_loss",
    "build_statistics",
    "format_loss",
    "Statistics",
    "EventType",
    "DowntimeEvent",
]