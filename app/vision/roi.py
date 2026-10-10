"""ROI — контролируемая зона камеры (Этап 2 — Computer Vision).

ROI определяет, находится ли рабочий В рабочей зоне:

    ┌─────────────────────────────┐
    │                             │
    │       CONVEYOR              │
    │  ┌───────────────────────┐  │
    │  │       ROI             │  │
    │  │      👤 WORKER        │  │
    │  └───────────────────────┘  │
    │                             │
    └─────────────────────────────┘

Координаты хранятся НОРМАЛИЗОВАННЫМИ (0.0–1.0 от размера кадра),
поэтому одна и та же настройка работает для камеры 640×480 и 1920×1080.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from app.core.config import settings


@dataclass(frozen=True)
class Roi:
    """Прямоугольник контролируемой зоны в нормализованных координатах.

    Координаты: (left, top, right, bottom), каждая в диапазоне 0.0–1.0.
    Значения автоматически нормализуются: левый край не может оказаться
    правее правого, а границы — вне кадра.
    """

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        # Порядок устойчив: меньший угол идёт первым
        l, r = sorted((_clamp(self.left), _clamp(self.right)))
        t, b = sorted((_clamp(self.top), _clamp(self.bottom)))
        object.__setattr__(self, "left", l)
        object.__setattr__(self, "right", r)
        object.__setattr__(self, "top", t)
        object.__setattr__(self, "bottom", b)

    # ------------------------------------------------------------------ #
    # Геометрия
    # ------------------------------------------------------------------ #
    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        """Площадь ROI в долях кадра."""
        return self.width * self.height

    def to_pixels(self, frame_width: int, frame_height: int) -> Tuple[int, int, int, int]:
        """Возвращает (x, y, w, h) в пикселях для OpenCV.

        Границы округляются внутрь кадра, чтобы срез не вышел за размер.
        """
        x1 = int(round(self.left * frame_width))
        y1 = int(round(self.top * frame_height))
        x2 = int(round(self.right * frame_width))
        y2 = int(round(self.bottom * frame_height))

        x1 = _clip(x1, 0, frame_width)
        y1 = _clip(y1, 0, frame_height)
        x2 = _clip(x2, 0, frame_width)
        y2 = _clip(y2, 0, frame_height)

        return x1, y1, max(0, x2 - x1), max(0, y2 - y1)

    def contains_point(self, x: float, y: float, frame_width: int, frame_height: int) -> bool:
        """Проверяет, лежит ли точка (в пикселях кадра) внутри ROI."""
        nx = x / frame_width if frame_width else 0.0
        ny = y / frame_height if frame_height else 0.0
        return self.left <= nx <= self.right and self.top <= ny <= self.bottom

    def contains_box(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        frame_width: int,
        frame_height: int,
        min_overlap: float = 0.30,
    ) -> bool:
        """Попадает ли bounding_box рабочего в ROI.

        Считается не центр, а ПЕРЕКРЫТИЕ: бокс, наполовину вылезший
        из зоны, ещё считается находящимся в ней. Так рабочий,
        стоящий на границе, не «моргает» из-за пары пикселей.
        """
        if frame_width <= 0 or frame_height <= 0:
            return False

        box_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if box_area <= 0:
            return False

        rx1 = self.left * frame_width
        ry1 = self.top * frame_height
        rx2 = self.right * frame_width
        ry2 = self.bottom * frame_height

        # Пересечение прямоугольников
        inter_w = max(0.0, min(x2, rx2) - max(x1, rx1))
        inter_h = max(0.0, min(y2, ry2) - max(y1, ry1))
        inter_area = inter_w * inter_h

        overlap = inter_area / box_area
        return overlap >= min_overlap

    # ------------------------------------------------------------------ #
    # Представление
    # ------------------------------------------------------------------ #
    def as_dict(self) -> dict:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
        }

    def describe(self) -> str:
        return (
            f"ROI {self.left:.2f},{self.top:.2f} — "
            f"{self.right:.2f},{self.bottom:.2f} "
            f"({self.width:.2f}×{self.height:.2f} кадра)"
        )


# ---------------------------------------------------------------------- #
# Вспомогательные функции
# ---------------------------------------------------------------------- #
def _clamp(value: float) -> float:
    """Ограничивает значение диапазоном 0.0–1.0."""
    return max(0.0, min(1.0, float(value)))


def _clip(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def default_roi() -> Roi:
    """ROI из настроек (app/core/config.py)."""
    return Roi(
        left=settings.roi_left,
        top=settings.roi_top,
        right=settings.roi_right,
        bottom=settings.roi_bottom,
    )


def roi_from_settings() -> Roi:
    """Синоним default_roi() — для читаемости в коде вызывающего."""
    return default_roi()