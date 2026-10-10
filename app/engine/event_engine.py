"""Event Engine (Этап 3 — Event Engine).

Логика (раздел 10 Handoff):

    MOVING + PERSON PRESENT  → NORMAL
    MOVING + PERSON ABSENT   → NORMAL / NO DOWNTIME
    STOPPED + PERSON PRESENT → POSSIBLE MAINTENANCE
    STOPPED + PERSON ABSENT  → POSSIBLE DOWNTIME

    STOPPED + PERSON ABSENT дольше N секунд → DOWNTIME

Движок НЕ знает про YOLO, OpenCV и источники видео: на вход
подаётся `VisionResult` из app/vision. Это разделение позволяет
тестировать логику простоя без видео.

ВАЖНО про UNKNOWN: состояние «линия определяется» НЕ равно STOPPED.
При UNKNOWN простой не подтверждается, но и не закрывается — нет
данных, значит нельзя утверждать, что линия заработала.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.core.config import settings
from app.core.types import LineStatus, SystemStatus
from app.engine.downtime import DowntimeState, DowntimeTracker
from app.engine.events import DowntimeFinished, DowntimeStarted
from app.vision import VisionResult


@dataclass
class EngineEvent:
    """Событие движка в удобном для интерфейса видеде."""

    kind: str                  # "started" | "finished"
    camera_id: int
    at: float
    duration_seconds: float
    estimated_loss: float
    #: Момент начала простоя (только для kind="finished")
    started_at: Optional[float] = None

    @property
    def is_started(self) -> bool:
        return self.kind == "started"

    def to_contract(self, cost_per_minute: Optional[float] = None):
        """Готовое событие для БД и API — контрактный `DowntimeEvent`.

        Считает backend (Lev): Dashboard и Telegram получают уже
        посчитанный ущерб и ничего не пересчитывают (раздел 14).
        """
        from app.engine.events import DowntimeFinished, DowntimeStarted, to_contract

        if self.is_started:
            return to_contract(
                started=DowntimeStarted(camera_id=self.camera_id, at=self.at),
                cost_per_minute=cost_per_minute or 0.0,
            )

        return to_contract(
            finished=DowntimeFinished(
                camera_id=self.camera_id,
                at=self.at,
                started_at=self.started_at,
                duration_seconds=self.duration_seconds,
            ),
            cost_per_minute=cost_per_minute or 0.0,
        )

    def describe(self) -> str:
        if self.is_started:
            return f"Обнаружен простой, начало в {self._clock()}"
        return (
            f"Простой завершён, длительность "
            f"{int(self.duration_seconds)} сек"
        )

    def _clock(self) -> str:
        from app.engine.events import format_clock
        return format_clock(self.at)


class EventEngine:
    """Превращает результаты Vision в события простоя.

    Пример:

        engine = EventEngine(camera_id=1, threshold_seconds=30)
        for result in vision_results:
            events = engine.update(result)
            for ev in events:
                print(ev.describe())
    """

    def __init__(
        self,
        camera_id: Optional[int] = None,
        threshold_seconds: Optional[float] = None,
        resume_confirm_seconds: Optional[float] = None,
        cost_per_minute: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.camera_id = int(
            camera_id if camera_id is not None else settings.camera_id
        )
        self.cost_per_minute = float(
            cost_per_minute if cost_per_minute is not None else settings.cost_per_minute
        )
        self._clock = clock or _time.monotonic
        self._tracker = DowntimeTracker(
            threshold_seconds=(
                threshold_seconds
                if threshold_seconds is not None
                else settings.downtime_threshold_seconds
            ),
            camera_id=self.camera_id,
            resume_confirm_seconds=(
                resume_confirm_seconds
                if resume_confirm_seconds is not None
                else settings.downtime_resume_confirm_seconds
            ),
        )
        # Момент, с которого состояние линии UNKNOWN (грань неопределённости)
        self._unknown_since: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Основной метод
    # ------------------------------------------------------------------ #
    def update(self, result: VisionResult, now: Optional[float] = None) -> List[EngineEvent]:
        """Обрабатывает результат Vision и возвращает события простоя."""
        stamp = self._clock() if now is None else float(now)

        condition, unknown = self._evaluate(result)

        if unknown:
            # Следим, как долго линия в неопределённом состоянии
            if self._unknown_since is None:
                self._unknown_since = stamp
        else:
            self._unknown_since = None

        raw_events = self._tracker.update(
            condition=condition, now=stamp, unknown=unknown
        )
        return [self._to_engine_event(e) for e in raw_events]

    # ------------------------------------------------------------------ #
    # Логика условий (раздел 10)
    # ------------------------------------------------------------------ #
    def _evaluate(self, result: VisionResult):
        """Возвращает (условие простоя, состояние UNKNOWN).

        Условие простоя: линия стоит И рабочего нет в ROI.
        """
        line = result.line_status

        if line == LineStatus.UNKNOWN:
            # Данных недостаточно — простой не подтверждаем и не закрываем
            return False, True

        stopped = line == LineStatus.STOPPED

        # MOVING + PERSON ABSENT → простоя нет (рабочий просто отошёл)
        # STOPPED + PERSON PRESENT → возможно обслуживание, простоя нет
        condition = stopped and not result.person_present
        return condition, False

    # ------------------------------------------------------------------ #
    # Преобразование
    # ------------------------------------------------------------------ #
    def _to_engine_event(self, raw) -> EngineEvent:
        if isinstance(raw, DowntimeStarted):
            return EngineEvent(
                kind="started",
                camera_id=raw.camera_id,
                at=raw.at,
                duration_seconds=0.0,
                estimated_loss=0.0,
            )
        if isinstance(raw, DowntimeFinished):
            return EngineEvent(
                kind="finished",
                camera_id=raw.camera_id,
                at=raw.at,
                duration_seconds=raw.duration_seconds,
                estimated_loss=self._loss(raw.duration_seconds, self.cost_per_minute),
                started_at=raw.started_at,
            )
        raise TypeError(f"Неизвестное событие: {type(raw)!r}")

    @staticmethod
    def _loss(duration_seconds: float, cost_per_minute: float) -> float:
        from app.core.types import calculate_loss
        return calculate_loss(int(round(duration_seconds)), cost_per_minute)

    # ------------------------------------------------------------------ #
    # Состояние
    # ------------------------------------------------------------------ #
    @property
    def is_downtime(self) -> bool:
        return self._tracker.is_downtime

    @property
    def state(self) -> DowntimeState:
        return self._tracker.state

    def current_downtime_seconds(self, now: Optional[float] = None) -> float:
        """Сколько секунд идёт текущий простой (0, если его нет)."""
        stamp = self._clock() if now is None else float(now)
        return self._tracker.snapshot(stamp).downtime_seconds

    def status(self, result: VisionResult, now: Optional[float] = None) -> SystemStatus:
        """Собирает `SystemStatus` для Dashboard/Telegram."""
        stamp = self._clock() if now is None else float(now)
        seconds = self.current_downtime_seconds(stamp)

        return SystemStatus(
            camera_id=self.camera_id,
            line_status=result.line_status,
            person_present=result.person_present,
            downtime_seconds=int(round(seconds)),
            estimated_loss=self._loss(seconds, self.cost_per_minute),
        )

    def reset(self) -> None:
        """Сбрасывает состояние (смена камеры, рестарт)."""
        self._tracker.reset()
        self._unknown_since = None


__all__ = [
    "EventEngine",
    "EngineEvent",
    "DowntimeState",
    "SystemStatus",
]