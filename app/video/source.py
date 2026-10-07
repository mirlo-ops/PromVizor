"""Единый интерфейс источника видео (Этап 1 — Video).

Любой источник (DEMO / WEBCAM / RTSP) отдаёт кадры одинаково,
поэтому AI-pipeline (Vision → Event Engine) не зависит от типа источника.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class VideoSource:
    """Базовый источник видео: open() → get_frame() → close().

    get_frame() возвращает кадр BGR (numpy.ndarray) или None,
    если источник исчерпан (например, конец видео без зацикливания).
    """

    #: строковый тип источника: "demo" / "webcam" / "rtsp"
    source_type: str = ""
    #: True только у DEMO — для явной пометки «DEMO MODE» в интерфейсе
    is_demo: bool = False

    def __init__(self) -> None:
        self._cap = None  # cv2.VideoCapture

    # ------------------------------------------------------------------ #
    # Жизненный цикл
    # ------------------------------------------------------------------ #
    def open(self) -> "VideoSource":
        """Открывает источник. Бросает RuntimeError, если открыть не удалось."""
        if self.is_opened():
            return self
        import cv2

        self._cap = self._create_capture(cv2)
        if self._cap is None or not self._cap.isOpened():
            self.close()
            raise RuntimeError(
                f"Не удалось открыть источник видео: {self.describe()}"
            )
        return self

    def _create_capture(self, cv2):  # noqa: ANN001 - cv2 module
        """Создаёт cv2.VideoCapture. Переопределяется наследниками."""
        raise NotImplementedError

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    # ------------------------------------------------------------------ #
    # Чтение кадров
    # ------------------------------------------------------------------ #
    def get_frame(self) -> Optional["np.ndarray"]:
        """Возвращает следующий кадр или None (источник исчерпан)."""
        if not self.is_opened():
            self.open()

        ok, frame = self._cap.read()
        if ok and frame is not None:
            return frame

        # Конец потока: даём источнику шанс продолжить (например, зациклиться)
        if self._on_end():
            ok, frame = self._cap.read()
            if ok and frame is not None:
                return frame
        return None

    def _on_end(self) -> bool:
        """Вызывается в конце потока.

        Возвращает True, если источнику удалось продолжить (перемотка).
        По умолчанию — поток просто заканчивается.
        """
        return False

    # ------------------------------------------------------------------ #
    # Свойства источника
    # ------------------------------------------------------------------ #
    @property
    def fps(self) -> float:
        return float(self._cap.get(5)) if self.is_opened() else 0.0  # CAP_PROP_FPS

    @property
    def width(self) -> int:
        return int(self._cap.get(3)) if self.is_opened() else 0  # CAP_PROP_FRAME_WIDTH

    @property
    def height(self) -> int:
        return int(self._cap.get(4)) if self.is_opened() else 0  # CAP_PROP_FRAME_HEIGHT

    @property
    def frame_count(self) -> int:
        return int(self._cap.get(7)) if self.is_opened() else 0  # CAP_PROP_FRAME_COUNT

    @property
    def duration_seconds(self) -> Optional[float]:
        """Длительность видео в реальных секундах (для DEMO-таймера)."""
        if not self.is_opened() or self.fps <= 0 or self.frame_count <= 0:
            return None
        return self.frame_count / self.fps

    @property
    def position_seconds(self) -> float:
        """Текущая позиция в видео, реальные секунды."""
        if not self.is_opened():
            return 0.0
        msec = self._cap.get(0)  # CAP_PROP_POS_MSEC
        return msec / 1000.0 if msec and msec > 0 else 0.0

    # ------------------------------------------------------------------ #
    # Утилиты
    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Короткое описание источника для логов и ошибок."""
        return f"{self.source_type or self.__class__.__name__}"

    def __enter__(self) -> "VideoSource":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()
