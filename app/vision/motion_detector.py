"""Определение движения линии (Этап 2 — Computer Vision).

Результат:
    WORKING / STOPPED / UNKNOWN

ВАЖНО: остановку нельзя определять по одному кадру — иначе любой
кратковременный заслон, смена освещения или дрожание камеры дают
ложное «линия стоит». Поэтому решение принимается по НЕСКОЛЬКИМ
последовательным кадрам, а переход между состояниями защищён
гистерезисом.

Как работает:
1. Кадр уменьшается (быстрее) и берётся только зона ROI.
2. Текущий кадр сравнивается с ПРЕДЫДУЩИМ: сумма абсолютных разностей
   → доля изменившихся пикселей за один кадр.
   Именно соседние кадры, а не сравнение с «эталоном»: если эталон
   зафиксировать один раз, то линия, остановившаяся навсегда, будет
   вечно отличаться от него и читаться как работающая.
3. Значения копятся в окне; решение принимается по СРЕДНЕМУ, а
   переход в STOPPED дополнительно требует, чтобы НИЗКОЕ движение
   держалось несколько кадров подряд — одиночный заслон не считается.
4. Гистерезис: после STOPPED нужно заметно больше движения, чтобы
   вернуться в WORKING, и меньше — чтобы уйти в STOPPED.

Значения статуса берутся из app.core.types.LineStatus — это
авторитетный контракт (app/core/CONTRACT.md), где состояние линии
обозначено WORKING, а не MOVING.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

import cv2
import numpy as np

from app.core.config import settings
from app.core.types import LineStatus
from app.vision.roi import Roi, default_roi


@dataclass
class MotionResult:
    """Результат анализа движения в одном кадре."""

    #: WORKING / STOPPED / UNKNOWN — значение для контракта
    line_status: str
    #: Средняя доля движущихся пикселей за окно
    motion: float = 0.0
    #: Сколько кадров учтено (для отладки прогрева)
    samples: int = 0
    #: Человекочитаемое пояснение
    reason: str = ""

    @property
    def is_working(self) -> bool:
        return self.line_status == LineStatus.WORKING

    @property
    def is_stopped(self) -> bool:
        return self.line_status == LineStatus.STOPPED

    @property
    def is_unknown(self) -> bool:
        return self.line_status == LineStatus.UNKNOWN

    def describe(self) -> str:
        if self.line_status == LineStatus.WORKING:
            return f"Линия работает (движение {self.motion:.3f})"
        if self.line_status == LineStatus.STOPPED:
            return f"Линия остановлена (движение {self.motion:.3f})"
        return f"Состояние линии неизвестно: прогрев {self.samples} кадров"


class MotionDetector:
    """Определяет движение конвейера по кадрам внутри ROI.

    Пример:

        detector = MotionDetector()
        result = detector.update(frame)
        if result.is_stopped:
            ...
    """

    def __init__(
        self,
        roi: Optional[Roi] = None,
        history_size: Optional[int] = None,
        min_samples: Optional[int] = None,
        working_threshold: Optional[float] = None,
        stopped_threshold: Optional[float] = None,
        diff_threshold: Optional[float] = None,
        blur_kernel: Optional[int] = None,
        resize_width: Optional[int] = None,
        lighting_threshold: Optional[float] = None,
    ) -> None:
        self.roi = roi or default_roi()
        self.history_size = int(history_size or settings.motion_history_size)
        self.min_samples = int(min_samples or settings.motion_min_samples)
        self.working_threshold = float(
            working_threshold
            if working_threshold is not None
            else settings.motion_working_threshold
        )
        self.stopped_threshold = float(
            stopped_threshold
            if stopped_threshold is not None
            else settings.motion_stopped_threshold
        )
        self.diff_threshold = float(
            diff_threshold if diff_threshold is not None else settings.motion_diff_threshold
        )
        self.blur_kernel = int(blur_kernel or settings.motion_blur_kernel)
        self.resize_width = int(resize_width or settings.motion_resize_width)
        self.lighting_threshold = float(
            lighting_threshold
            if lighting_threshold is not None
            else settings.motion_lighting_threshold
        )

        self._prev: Optional[np.ndarray] = None
        self._diffs: Deque[float] = deque(maxlen=self.history_size)
        self._brightness: Deque[float] = deque(maxlen=self.history_size)
        self._state: str = LineStatus.UNKNOWN

    # ------------------------------------------------------------------ #
    # Публичный интерфейс
    # ------------------------------------------------------------------ #
    def update(self, frame) -> MotionResult:
        """Обрабатывает очередной кадр и возвращает состояние линии."""
        prepared = self._prepare(frame)
        if prepared is None:
            # Кадр не пригоден (пустой или неверный размер) — состояние
            # НЕ меняем, чтобы одна плохая картинка не сбросила линию
            return MotionResult(
                line_status=self._state,
                samples=len(self._diffs),
                reason="кадр пропущен",
            )

        if self._prev is not None and self._prev.shape != prepared.shape:
            # Сменилось разрешение — накопленная история больше не годится
            self.reset()

        if self._prev is None:
            # Первый кадр сравнивать не с чем. НЕ пишем «нулевое движение»:
            # это была бы ложная тишина, которая портит среднее.
            self._prev = prepared
            return MotionResult(
                line_status=LineStatus.UNKNOWN,
                motion=0.0,
                samples=0,
                reason="первый кадр: ожидаю следующий",
            )

        # Смена освещения (заслон объектива, включение света) резко
        # меняет всю картинку и выглядит как движение. Такие кадры
        # нельзя считать движением линии — иначе стоящая линия
        # объявляется рабочей.
        #
        # Сравниваем не с ПРЕДЫДУЩИМ кадром, а с устойчивой базовой
        # яркостью: иначе после заслона «чёрный» кадр становится
        # базой, и возврат к нормальному свету выглядит как движение.
        # Отброшенный кадр НЕ становится новой базой и не подменяет
        # предыдущий — иначе ошибка закрепится.
        if self._is_lighting_change(prepared):
            return MotionResult(
                line_status=self._state,
                motion=self._average_motion(),
                samples=len(self._diffs),
                reason="кадр отброшен: смена освещения",
            )

        diff = self._frame_diff(self._prev, prepared)
        self._prev = prepared
        self._diffs.append(diff)
        self._brightness.append(float(prepared.mean()))

        if len(self._diffs) < self.min_samples:
            return MotionResult(
                line_status=LineStatus.UNKNOWN,
                motion=round(diff, 4),
                samples=len(self._diffs),
                reason=f"прогрев: {len(self._diffs)}/{self.min_samples} кадров",
            )

        motion = self._average_motion()
        self._state = self._decide(motion)

        result = MotionResult(
            line_status=self._state,
            motion=round(motion, 4),
            samples=len(self._diffs),
            reason="",
        )
        result.reason = result.describe()
        return result

    @property
    def state(self) -> str:
        """Текущее состояние линии (после прогрева)."""
        return self._state

    def reset(self) -> None:
        """Сбрасывает историю — например, при смене камеры или сцены."""
        self._prev = None
        self._diffs.clear()
        self._brightness.clear()
        self._state = LineStatus.UNKNOWN

    # ------------------------------------------------------------------ #
    # Внутреннее
    # ------------------------------------------------------------------ #
    def _prepare(self, frame) -> Optional[np.ndarray]:
        """Готовит кадр: серый, малый, размытый, только ROI."""
        if frame is None:
            return None

        shape = getattr(frame, "shape", None)
        if shape is None or len(shape) < 2:
            return None

        h, w = int(shape[0]), int(shape[1])
        if h <= 0 or w <= 0:
            return None

        x, y, rw, rh = self.roi.to_pixels(w, h)
        if rw <= 0 or rh <= 0:
            return None

        roi_frame = frame[y : y + rh, x : x + rw]

        gray = _to_gray(roi_frame)
        if gray.size == 0:
            return None

        # Уменьшаем — считать быстрее, шум усредняется
        scale = self.resize_width / gray.shape[1] if gray.shape[1] > 0 else 1.0
        if scale > 0 and scale < 1.0:
            gray = cv2.resize(
                gray,
                (self.resize_width, max(1, int(gray.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )

        # Размытие убирает пиксельный шум и дрожание
        k = self.blur_kernel
        if k > 1 and k % 2 == 1:
            gray = cv2.GaussianBlur(gray, (k, k), 0)

        return gray.astype(np.float32)

    def _is_lighting_change(self, current) -> bool:
        """Похоже ли, что изменилось освещение, а не содержимое сцены.

        Признак — резкий сдвиг средней яркости ОТНОСИТЕЛЬНО УСТОЙЧИВОЙ
        БАЗЫ (медиана последних принятых кадров), а не относительно
        предыдущего кадра. Конвейер меняет отдельные участки и оставляет
        среднюю яркость примерно на месте; заслон объектива или включение
        света меняют её целиком.

        Пока базы нет (первые кадры) — сравниваем с предыдущим принятым.
        """
        if current.size == 0:
            return False

        mean_curr = float(current.mean())

        if self._brightness:
            baseline = _median(self._brightness)
        elif self._prev is not None and self._prev.shape == current.shape:
            baseline = float(self._prev.mean())
        else:
            return False  # базы нет — судить пока не о чем

        return abs(mean_curr - baseline) / 255.0 > self.lighting_threshold

    def _frame_diff(self, prev, current) -> float:
        """Доля пикселей, изменившихся между двумя соседними кадрами."""
        if prev is None or current is None:
            return 0.0
        if prev.shape != current.shape:
            return 0.0

        diff = cv2.absdiff(current, prev)
        # Пиксель считается «движущимся», если изменился сильнее порога
        _, mask = cv2.threshold(
            diff, self.diff_threshold * 255.0, 255.0, cv2.THRESH_BINARY
        )
        size = float(mask.size or 1)
        return float(cv2.countNonZero(mask)) / size

    def _average_motion(self) -> float:
        """Средняя доля изменившихся пикселей за окно."""
        if not self._diffs:
            return 0.0
        return sum(self._diffs) / len(self._diffs)

    def _low_motion_streak(self) -> int:
        """Сколько последних кадров подряд движение низкое."""
        streak = 0
        for value in reversed(self._diffs):
            if value <= self.stopped_threshold:
                streak += 1
            else:
                break
        return streak

    def _decide(self, motion: float) -> str:
        """Переводит уровень движения в состояние линии (с гистерезисом)."""
        if self._state == LineStatus.STOPPED:
            # Для возврата в работу нужно больше движения — это гистерезис
            if motion >= self.working_threshold:
                return LineStatus.WORKING
            return LineStatus.STOPPED

        # Остановка требует УСТОЙЧИВОГО низкого движения: одиночный
        # заслон или смена освещения не должны останавливать линию.
        if motion <= self.stopped_threshold and self._low_motion_streak() >= self.min_samples:
            return LineStatus.STOPPED

        if motion >= self.working_threshold:
            return LineStatus.WORKING

        # Между порогами: состояние не меняем, чтобы не дрожало
        if self._state == LineStatus.WORKING:
            return LineStatus.WORKING
        return LineStatus.UNKNOWN


# ---------------------------------------------------------------------- #
# Вспомогательные функции
# ---------------------------------------------------------------------- #
def _median(values) -> float:
    """Медиана списка — устойчивая оценка базовой яркости."""
    items = sorted(values)
    if not items:
        return 0.0
    mid = len(items) // 2
    if len(items) % 2:
        return float(items[mid])
    return (float(items[mid - 1]) + float(items[mid])) / 2.0


def _to_gray(frame):
    """Переводит кадр в оттенки серого."""
    try:
        if len(frame.shape) == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if len(frame.shape) == 2:
            return frame
    except cv2.error:
        # Неожиданное число каналов — пробуем напрямую
        return frame
    return frame