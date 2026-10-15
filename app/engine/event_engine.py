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
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.core.config import settings
from app.core.types import LineStatus, SystemStatus
from app.engine.downtime import DowntimeState, DowntimeTracker
from app.engine.events import DowntimeFinished, DowntimeStarted
from app.vision import VisionResult


def _seconds_of_day() -> float:
    """Сколько секунд прошло с полуночи по стенным часам процесса.

    Именно то, что ждёт format_clock(): метка, из которой получается
    «ЧЧ:ММ». Монотонный счётчик для этого не годится — он растёт от
    загрузки системы, а не от полуночи.
    """
    now = _time.localtime()
    return float(now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)


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
    #: Время события для показа: секунды от начала суток по стенным
    #: часам. `at` — монотонная метка, по ней считается длительность,
    #: и её НЕЛЬЗЯ форматировать как «ЧЧ:ММ»: она растёт от загрузки
    #: системы, из-за чего событие в 23:38 показывалось как 12:21.
    wall_at: Optional[float] = None
    #: Начало простоя по стенным часам — для «ЧЧ:ММ» в контракте
    wall_started_at: Optional[float] = None

    @property
    def is_started(self) -> bool:
        return self.kind == "started"

    def to_contract(self, cost_per_minute: Optional[float] = None):
        """Готовое событие для БД и API — контрактный `DowntimeEvent`.

        Считает backend (Lev): Dashboard и Telegram получают уже
        посчитанный ущерб и ничего не пересчитывают (раздел 14).
        """
        from app.engine.events import DowntimeFinished, DowntimeStarted, to_contract

        # Для показа берём стенное время. Длительность при этом не
        # трогаем: она посчитана по монотонной метке и верна, даже
        # если часы двигали.
        shown = self.wall_at if self.wall_at is not None else self.at
        shown_started = (
            self.wall_started_at
            if self.wall_started_at is not None
            else self.started_at
        )

        if self.is_started:
            return to_contract(
                started=DowntimeStarted(camera_id=self.camera_id, at=shown),
                cost_per_minute=cost_per_minute or 0.0,
            )

        return to_contract(
            finished=DowntimeFinished(
                camera_id=self.camera_id,
                at=shown,
                started_at=shown_started,
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
        wall_clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.camera_id = int(
            camera_id if camera_id is not None else settings.camera_id
        )
        self.cost_per_minute = float(
            cost_per_minute if cost_per_minute is not None else settings.cost_per_minute
        )
        self._clock = clock or _time.monotonic
        # Стренные часы — только для показа «ЧЧ:ММ». Отдельные от
        # монотонного счётчика, потому что монотонная метка растёт от
        # загрузки системы: на сервере, простоявшем 12 часов, событие
        # в 23:38 записывалось как «12:21», и в истории простоя время
        # выглядело случайным числом.
        self._wall_clock = wall_clock or _seconds_of_day
        # Соответствие «монотонная метка → стенное время». Нужно, чтобы
        # у завершённого простоя было правильное время НАЧАЛА, а не
        # только конца. Хранится ограниченно: устаревшие метки не нужны.
        self._wall_by_stamp: "OrderedDict[float, float]" = OrderedDict()
        self._wall_remembered = 512
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
        self._remember(stamp)

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
    # Стренное время события
    # ------------------------------------------------------------------ #
    def _remember(self, stamp: float) -> None:
        """Запоминает стенное время для монотонной метки."""
        self._wall_by_stamp[stamp] = self._wall_clock()
        while len(self._wall_by_stamp) > self._wall_remembered:
            self._wall_by_stamp.popitem(last=False)

    def _wall_for(self, stamp: Optional[float]) -> Optional[float]:
        """Стренное время по монотонной метке, если оно запомнено."""
        if stamp is None:
            return None
        return self._wall_by_stamp.get(stamp)

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
        # Стренное время конца снимается здесь, начало — по сохранённому
        # соответствию (см. _remember). Если соответствия нет (простой
        # начался до перезапуска), начало восстанавливается вычитанием
        # длительности: точность до секунды тут не критична, а пустое
        # «00:00» в истории простоя смотрится как ошибка.
        wall = self._wall_clock()
        if isinstance(raw, DowntimeStarted):
            return EngineEvent(
                kind="started",
                camera_id=raw.camera_id,
                at=raw.at,
                wall_at=wall,
                duration_seconds=0.0,
                estimated_loss=0.0,
            )
        if isinstance(raw, DowntimeFinished):
            wall_started = None
            if raw.started_at is not None:
                wall_started = self._wall_for(raw.started_at)
            if wall_started is None:
                wall_started = wall - raw.duration_seconds
            return EngineEvent(
                kind="finished",
                camera_id=raw.camera_id,
                at=raw.at,
                wall_at=wall,
                duration_seconds=raw.duration_seconds,
                estimated_loss=self._loss(raw.duration_seconds, self.cost_per_minute),
                started_at=raw.started_at,
                wall_started_at=wall_started,
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