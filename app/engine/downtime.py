"""Таймер простоя (Этап 3 — Event Engine).

Состояния (раздел 11 Handoff):

    IDLE      — простоя нет
    DOWNTIME  — простой подтверждён

Ключевое правило: условие «линия стоит И рабочего нет» должно
держаться дольше порога N секунд. Короткое условие не считается
простоем — иначе один кадр с помехой создавал бы ложное событие.

Таймер не знает про камеры и YOLO — на вход подаётся булево
«условие простоя выполняется» и время. Это делает его тестируемым
и переиспользуемым.

Время передаётся вызывающим кодом (метка now), а не берётся внутри:
- так таймер тестируется без ожидания реальных секунд;
- так DEMO-режим может подставить «ускоренное» демо-время.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class DowntimeState(str, Enum):
    """Состояние простоя (раздел 11 Handoff)."""

    IDLE = "IDLE"
    DOWNTIME = "DOWNTIME"


@dataclass
class DowntimeSnapshot:
    """Снимок состояния таймера — для логов и интерфейса."""

    state: DowntimeState = DowntimeState.IDLE
    #: Сколько секунд условие простоя держится (до подтверждения)
    pending_seconds: float = 0.0
    #: Сколько секунд длится подтверждённый простой
    downtime_seconds: float = 0.0
    #: Момент начала подтверждённого простоя
    started_at: Optional[float] = None

    @property
    def is_downtime(self) -> bool:
        return self.state == DowntimeState.DOWNTIME

    def describe(self) -> str:
        if self.state == DowntimeState.DOWNTIME:
            return f"Простой: {self.downtime_seconds:.0f} сек"
        return "Простоя нет"


class DowntimeTracker:
    """Считает простой по условию и порогу.

    Пример:

        tracker = DowntimeTracker(threshold_seconds=30, camera_id=1)
        events = tracker.update(condition=True, now=0.0)
        events = tracker.update(condition=True, now=40.0)  # DOWNTIME_STARTED
        events = tracker.update(condition=False, now=100.0)  # DOWNTIME_FINISHED

    События возвращаются списком: за один вызов может прийти ноль,
    одно или два события (если состояние скачком изменилось).
    """

    def __init__(
        self,
        threshold_seconds: float,
        camera_id: int = 1,
        resume_confirm_seconds: float = 0.0,
    ) -> None:
        if threshold_seconds < 0:
            raise ValueError("Порог простоя не может быть отрицательным")
        if resume_confirm_seconds < 0:
            raise ValueError("Подтверждение восстановления не может быть отрицательным")

        self.threshold_seconds = float(threshold_seconds)
        self.camera_id = int(camera_id)
        self.resume_confirm_seconds = float(resume_confirm_seconds)

        self._state = DowntimeState.IDLE
        self._pending_since: Optional[float] = None
        self._started_at: Optional[float] = None
        self._last_now: Optional[float] = None
        # Момент, с которого движение восстановилось (ждём подтверждения)
        self._resumed_since: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Состояние
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> DowntimeState:
        return self._state

    @property
    def is_downtime(self) -> bool:
        return self._state == DowntimeState.DOWNTIME

    def snapshot(self, now: Optional[float] = None) -> DowntimeSnapshot:
        """Текущее состояние на момент now."""
        stamp = self._last_now if now is None else now
        pending = 0.0
        if self._state == DowntimeState.DOWNTIME:
            duration = max(0.0, (stamp - self._started_at)) if (stamp is not None and self._started_at is not None) else 0.0
        else:
            duration = 0.0
            if self._pending_since is not None and stamp is not None:
                pending = max(0.0, stamp - self._pending_since)

        return DowntimeSnapshot(
            state=self._state,
            pending_seconds=pending,
            downtime_seconds=duration,
            started_at=self._started_at,
        )

    # ------------------------------------------------------------------ #
    # Основной метод
    # ------------------------------------------------------------------ #
    def update(self, condition: bool, now: float, unknown: bool = False) -> List[object]:
        """Обновляет таймер.

        Аргументы:
            condition: выполняется ли условие простоя
                       (линия стоит И рабочего нет).
            now: текущее время в секундах (монотонное).
            unknown: состояние линии не определяется. Условие при этом
                     игнорируется, но простой не закрывается — нет
                     данных, значит нельзя утверждать, что линия
                     заработала.

        Возвращает: список событий (см. app/engine/event_engine.py).
        """
        if now < 0:
            raise ValueError("Время не может быть отрицательным")
        if self._last_now is not None and now < self._last_now:
            raise ValueError(
                f"Время идёт назад: {now} < {self._last_now}. "
                "Таймер простоя требует монотонного времени."
            )
        self._last_now = now

        if unknown:
            return []

        if condition:
            self._resumed_since = None
            return self._handle_condition(now)
        return self._handle_no_condition(now)

    def reset(self) -> None:
        """Сбрасывает таймер (смена камеры, смена сцены, рестарт)."""
        self._state = DowntimeState.IDLE
        self._pending_since = None
        self._started_at = None
        self._resumed_since = None
        self._last_now = None

    # ------------------------------------------------------------------ #
    # Внутренняя логика
    # ------------------------------------------------------------------ #
    def _handle_condition(self, now: float) -> List[object]:
        """Условие простоя выполняется."""
        if self._state == DowntimeState.DOWNTIME:
            # Уже в простое — продолжаем
            return []

        # Порог 0 — простой подтверждается сразу
        if self.threshold_seconds <= 0:
            return self._start_downtime(now)

        if self._pending_since is None:
            self._pending_since = now
            return []

        if now - self._pending_since >= self.threshold_seconds:
            return self._start_downtime(now)

        return []

    def _handle_no_condition(self, now: float) -> List[object]:
        """Условие простоя НЕ выполняется."""
        # Движение восстановилось — но ждём подтверждения,
        # чтобы микропаузы не плодили пары событий
        if self._state == DowntimeState.DOWNTIME:
            if self._resumed_since is None:
                self._resumed_since = now
            if now - self._resumed_since >= self.resume_confirm_seconds:
                return self._finish_downtime(now)
            return []

        # Не в простое: незавершённое условие просто отменяется
        # (короткое срабатывание = ложное, простоя не было)
        self._pending_since = None
        self._resumed_since = None
        return []

    def _start_downtime(self, now: float) -> List[object]:
        from app.engine.events import DowntimeStarted

        # Простой начался НЕ тогда, когда мы его подтвердили, а тогда,
        # когда условие впервые выполнилось. Порог — это задержка
        # подтверждения, а не часть простоя: линия стоит с момента
        # pending_since, и потери производства тоже с него.
        started_at = self._pending_since if self._pending_since is not None else now

        self._state = DowntimeState.DOWNTIME
        self._started_at = started_at
        self._pending_since = None
        self._resumed_since = None
        return [DowntimeStarted(camera_id=self.camera_id, at=started_at)]

    def _finish_downtime(self, now: float) -> List[object]:
        from app.engine.events import DowntimeFinished

        started = self._started_at if self._started_at is not None else now
        duration = max(0.0, now - started)

        self._state = DowntimeState.IDLE
        self._pending_since = None
        self._resumed_since = None
        finished_at = self._started_at
        self._started_at = None

        return [
            DowntimeFinished(
                camera_id=self.camera_id,
                at=now,
                started_at=finished_at,
                duration_seconds=duration,
            )
        ]