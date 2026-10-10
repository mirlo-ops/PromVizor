"""Синтетическое видео для демонстрации без DEMO-роликов.

Зачем. DEMO-роликов ещё нет (Этап 9), но показать работу системы
нужно сейчас: рамка вокруг человека, его путь и исчезновение через
5 секунд — вся логика должна быть видна на глазах. Этот источник
рисует такую сцену программно.

Важно: это НЕ данные завода. Синтетика нужна, чтобы проверить
механику; настоящие показания появляются только с реальной камеры
или настоящего ролика. На кадрах это помечено, а режим в интерфейсе
называется «Демо».

Что в сцене. Линия с движущимися деталями (конвейер работает) и
человек, который ходит по рабочей зоне, затем уходит за пределы
кадра. Через несколько секунд сцена повторяется — демонстрация
идёт неограниченно долго.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from app.video.source import VideoSource


class SyntheticSource(VideoSource):
    """Рисует сцену «конвейер работает, человек ходит и уходит»."""

    source_type = "synthetic"
    is_demo = True

    #: Длительность одного цикла сцены, секунды
    CYCLE_SECONDS = 24.0

    #: Сколько секунд человек отсутствует в кадре перед тем, как
    #: вернуться. Задано так, чтобы простой успел набрать порог
    #: и событие появилось в отчёте.
    ABSENT_SECONDS = 9.0

    def __init__(
        self,
        width: int = 960,
        height: int = 540,
        fps: int = 12,
        absent_seconds: Optional[float] = None,
    ) -> None:
        super().__init__()
        # Размеры и частота храним под другими именами: в базовом
        # классе width/height/fps — свойства, и присваивание им
        # обычных значений упало бы с ошибкой.
        self._width = int(width)
        self._height = int(height)
        self._fps = float(fps)
        self.absent_seconds = self.ABSENT_SECONDS if absent_seconds is None else absent_seconds
        self._frame_index = 0
        self._last_person_box = None
        self._cap = None  # источник синтетический, cv2 не нужен

    # ------------------------------------------------------------------ #
    # Жизненный цикл
    # ------------------------------------------------------------------ #
    def open(self) -> "SyntheticSource":
        return self

    def close(self) -> None:
        pass

    def is_opened(self) -> bool:
        return True

    # ---- свойства кадра: перекрывают одноимённые свойства VideoSource ---- #
    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_count(self) -> int:
        # цикл бесконечный
        return 0

    @property
    def duration_seconds(self) -> Optional[float]:
        return self.CYCLE_SECONDS

    @property
    def position_seconds(self) -> float:
        return (self._frame_index / self._fps) % self.CYCLE_SECONDS

    def describe(self) -> str:
        return f"SYNTHETIC source (синтетическая сцена, {self.width}x{self.height})"

    # ------------------------------------------------------------------ #
    # Кадры
    # ------------------------------------------------------------------ #
    def get_frame(self) -> Optional["np.ndarray"]:
        t = self._frame_index / self._fps
        self._frame_index += 1

        frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        self._draw_background(frame)
        self._draw_conveyor(frame, t)
        person_box = self._draw_person(frame, t)
        # Запоминаем, где нарисовали человека: конвейер спрашивает
        # эту рамку вместо YOLO (см. known_person_box)
        self._last_person_box = person_box
        self._draw_hud(frame, t, person_box)
        return frame

    def known_person_box(self):
        """Рамка нарисованного человека на последнем кадре.

        Почему это вообще нужно. YOLO обучена на фотографиях людей;
        силуэт, собранный из прямоугольников и кругов, она не признаёт —
        проверено, уверенность оставалась нулевой. Для синтетики
        известный бокс — честный выход: это витрина механики
        (трек, путь, простой), а не измерение.

        Настоящая детекция проверяется на веб-камере и на DEMO-роликах.
        """
        return self._last_person_box

    def _cycle_position(self, t: float) -> float:
        """Позиция внутри цикла: 0..1."""
        return (t % self.CYCLE_SECONDS) / self.CYCLE_SECONDS

    # ------------------------------------------------------------------ #
    # Отрисовка сцены
    # ------------------------------------------------------------------ #
    def _draw_background(self, frame) -> None:
        import cv2

        # Пол и стена цеха
        horizon = int(self.height * 0.62)
        frame[:, :] = (58, 66, 72)
        cv2.rectangle(
            frame,
            (0, horizon),
            (self.width, self.height),
            (48, 52, 58),
            -1,
        )
        # Несколько колонн
        for i in range(6):
            x = int(self.width * (0.08 + i * 0.17))
            cv2.rectangle(frame, (x, int(self.height * 0.2)), (x + 10, horizon),
                          (70, 78, 84), -1)

    def _draw_conveyor(self, frame, t: float) -> None:
        """Конвейер с деталями: пока есть движение — линия работает."""
        import cv2

        y = int(self.height * 0.72)
        x0 = int(self.width * 0.06)
        x1 = int(self.width * 0.94)
        cv2.rectangle(frame, (x0, y), (x1, y + 18), (86, 96, 104), -1)
        cv2.rectangle(frame, (x0, y + 18), (x1, y + 26), (62, 70, 76), -1)

        # Детали на ленте — их движение и означает «линия работает»
        for i in range(6):
            offset = (t * 90) % (x1 - x0)
            cx = int(x0 + offset - i * 120)
            if cx < x0 - 40:
                continue
            cv2.rectangle(frame, (cx, y - 22), (cx + 44, y), (150, 130, 90), -1)
            cv2.rectangle(frame, (cx, y - 22), (cx + 44, y - 16), (110, 94, 62), -1)

    def _draw_person(self, frame, t: float):
        """Рисует человека, когда он «в кадре».

        Пропорции взяты у реального человека: голова примерно 1/15 роста,
        плечи шире таза. Это важно — YOLO обучена на фотографиях людей,
        и примитив из прямоугольников она не признаёт.

        Возвращает рамку (x1, y1, x2, y2) или None — когда человека нет.
        """
        import cv2

        position = self._cycle_position(t)
        present_until = 1.0 - (self.absent_seconds / self.CYCLE_SECONDS)
        if position > present_until:
            return None

        # Человек идёт слева направо по рабочей зоне
        progress = position / present_until if present_until else 0.0
        bob = int(math.sin(t * 3.2) * 3)
        # Рост — заметная доля кадра: мелкая фигура не распознаётся
        body_h = int(self.height * 0.46)
        head_r = max(5, body_h // 15)

        cx = int(self.width * (0.18 + progress * 0.60))
        base_y = int(self.height * 0.86) + bob

        head_cy = base_y - body_h + head_r
        neck_y = head_cy + head_r
        shoulder_y = neck_y + int(head_r * 1.2)
        hip_y = base_y - int(body_h * 0.42)
        shoulder_half = head_r * 2
        hip_half = head_r

        body_color = (78, 110, 158)

        # Ноги: две, с шагом
        swing = int(math.sin(t * 5.0) * (body_h * 0.16))
        leg_w = max(4, int(head_r * 0.7))
        for direction in (-1, 1):
            x_top = cx + direction * int(hip_half * 0.6)
            x_bottom = cx + direction * (int(hip_half * 0.7) + (swing if direction > 0 else -swing))
            cv2.line(frame, (x_top, hip_y), (x_bottom, base_y), (44, 48, 54), leg_w, cv2.LINE_AA)

        # Торс: плечи шире таза
        torso = np.array(
            [
                [cx - shoulder_half, shoulder_y],
                [cx + shoulder_half, shoulder_y],
                [cx + hip_half, hip_y],
                [cx - hip_half, hip_y],
            ],
            dtype=np.int32,
        )
        cv2.fillPoly(frame, [torso], body_color, cv2.LINE_AA)

        # Руки
        arm_swing = int(math.sin(t * 5.0) * (body_h * 0.10))
        arm_w = max(3, int(head_r * 0.55))
        for direction in (-1, 1):
            x_shoulder = cx + direction * shoulder_half
            x_hand = x_shoulder + direction * int(head_r * 0.4) + (arm_swing if direction > 0 else -arm_swing)
            cv2.line(
                frame,
                (x_shoulder, shoulder_y),
                (x_hand, hip_y + int(body_h * 0.10)),
                body_color,
                arm_w,
                cv2.LINE_AA,
            )

        # Шея и голова
        cv2.line(frame, (cx, neck_y - head_r // 2), (cx, neck_y + 2),
                 (98, 134, 182), max(3, head_r // 2), cv2.LINE_AA)
        cv2.circle(frame, (cx, head_cy), head_r, (98, 134, 182), -1, cv2.LINE_AA)

        x1 = cx - shoulder_half - head_r // 2
        x2 = cx + shoulder_half + head_r // 2
        y1 = head_cy - head_r - 2
        return (x1, y1, x2, base_y + 2)

    def _draw_hud(self, frame, t: float, person_box) -> None:
        """Подпись «СИНТЕТИКА» — чтобы её не приняли за запись."""
        import cv2

        label = "СИНТЕТИКА — демонстрация без DEMO-ролика"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (8, 8), (tw + 22, th + 22), (40, 44, 50), -1)
        cv2.putText(frame, label, (16, th + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (200, 200, 200), 1, cv2.LINE_AA)
        if person_box is None:
            cv2.putText(frame, "Person: OFF", (16, self.height - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (92, 74, 239), 2, cv2.LINE_AA)
        else:
            cv2.putText(frame, "Person: ON", (16, self.height - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 176, 80), 2, cv2.LINE_AA)


__all__ = ["SyntheticSource"]