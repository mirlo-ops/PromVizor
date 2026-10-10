"""Отрисовка разметки на кадре: рамка, номер человека, пройденный путь.

Требование из ТЗ: пока идёт видео с камеры, должен быть виден «трек»
— зелёная рамка вокруг человека. Позже уточнилось: не просто рамка,
а номер человека и линия пройденного пути.

Цвета несут смысл, а не просто украшают:

* **зелёный** — человек в кадре, всё в порядке;
* **жёлтый** — человек вне контролируемой зоны (ROI);
* **красный** — человека нет в кадре дольше порога: линия стоит,
  простой фиксируется.

Цвет рамки выбирается по ТОМУ ЖЕ признаку, по которому движок
принимает решение о простое. Иначе получилось бы расхождение:
на экране человек есть, а система уже считает, что его нет.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from app.core.types import LineStatus
from app.pipeline.tracker import Track

#: Цвета в BGR — так их отдаёт OpenCV.
COLOR_PERSON = (0, 176, 80)        # зелёный: человек на месте
COLOR_OUTSIDE = (0, 191, 255)     # жёлтый: человек вне ROI
COLOR_DOWNTIME = (92, 74, 239)    # красный: линия стоит
COLOR_TRAIL = (0, 176, 80)        # путь человека
COLOR_TRAIL_FADE = (200, 200, 200)
COLOR_ROI = (180, 180, 180)       # граница контролируемой зоны
COLOR_TEXT = (255, 255, 255)
COLOR_LABEL_BG = (60, 60, 60)


def _scale_for(frame: np.ndarray) -> Tuple[int, int, int]:
    """Толщина линий и размер шрифта под текущий размер кадра.

    На кадре 320×240 рамка в 2 пикселя превращается в жирное пятно,
    а на 4K — в невидимую ниточку.
    """
    height, width = frame.shape[:2]
    shortest = min(height, width)
    thickness = max(1, round(shortest / 300))
    font_scale = max(0.4, shortest / 900)
    return thickness, int(round(font_scale * 20)), font_scale


def draw_trails(frame: np.ndarray, tracks: Sequence[Track]) -> np.ndarray:
    """Рисует путь, пройденный каждым человеком.

    Путь рисуется от самой старой точки к текущей, с затуханием:
    начало пути бледное, конец — насыщенный. Так видно, куда человек
    двигался, а не просто где он сейчас.
    """
    thickness, _box_h, _font = _scale_for(frame)
    result = frame

    for track in tracks:
        points = list(track.trail)
        if len(points) < 2:
            continue

        total = len(points) - 1
        for index in range(total):
            # OpenCV требует целые координаты, а центр трека —
            # дробное число. Без приведения линия не рисуется.
            start = (int(round(points[index][0])), int(round(points[index][1])))
            end = (int(round(points[index + 1][0])), int(round(points[index + 1][1])))
            # Затухание от начала пути к текущему положению
            ratio = (index + 1) / total
            color = tuple(
                int(COLOR_TRAIL_FADE[i] + (COLOR_TRAIL[i] - COLOR_TRAIL_FADE[i]) * ratio)
                for i in range(3)
            )
            cv2.line(result, start, end, color, thickness, cv2.LINE_AA)

        # Точка в текущем положении
        here = (int(round(points[-1][0])), int(round(points[-1][1])))
        cv2.circle(result, here, thickness * 3, COLOR_TRAIL, -1, cv2.LINE_AA)

    return result


def _roi_pixels(roi, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """Контролируемая зона как углы прямоугольника (x1, y1, x2, y2).

    ВАЖНО: `Roi.to_pixels` возвращает (x, y, w, h) — левый верхний
    угол и РАЗМЕРЫ, а не углы прямоугольника. Здесь они
    складываются, иначе зона рисовалась бы вдвое меньше нужного.

    Если посчитать не удалось, возвращаем None: отсутствие зоны не
    должно стирать разметку человека.
    """
    if roi is None:
        return None
    try:
        height, width = frame.shape[:2]
        x, y, w, h = roi.to_pixels(width, height)
        return int(x), int(y), int(x + w), int(y + h)
    except Exception:
        return None


def draw_tracks(
    frame: np.ndarray,
    tracks: Sequence[Track],
    line_status: str = LineStatus.WORKING,
    roi: Optional[object] = None,
) -> np.ndarray:
    """Рисует рамку и номер каждого человека.

    `roi` — объект с методом to_pixels(frame_shape); если он задан,
    рисуется граница контролируемой зоны.
    """
    thickness, box_h, font = _scale_for(frame)

    # Цвет рамки зависит от состояния линии — так же, как решает
    # система, а не от настроения интерфейса.
    if line_status == LineStatus.STOPPED:
        box_color = COLOR_DOWNTIME
    else:
        box_color = COLOR_PERSON

    for track in tracks:
        x1, y1, x2, y2 = (int(v) for v in track.box)
        color = box_color
        if roi is not None and not _box_inside(track.box, roi, frame):
            color = COLOR_OUTSIDE

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

        label = f"Рабочий {track.track_id}"
        _put_label(frame, label, x1, y1, color, thickness, box_h, font)

    return frame


def _box_inside(box, roi, frame) -> bool:
    """Проверяет, находится ли человек в контролируемой зоне.

    Используется готовый contains_box — он считает перекрытие рамки
    с зоной, а не центр, и человек на границе не «моргает».
    """
    if roi is None:
        return True
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return True
    try:
        return roi.contains_box(box[0], box[1], box[2], box[3], width, height)
    except Exception:
        # Если зона не посчиталась, считаем рамку допустимой:
        # отсутствие зоны не должно стирать разметку человека.
        return True


def _put_label(frame, text, x, y, color, thickness, box_h, font) -> None:
    """Подпись над рамкой: номер человека читается прямо на видео."""
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font, thickness)
    # Подпись не должна вылезать за верхний край кадра
    top = max(y, th + baseline + 2)
    cv2.rectangle(
        frame,
        (x, top - th - baseline - 2),
        (x + tw + 6, top),
        color,
        -1,
    )
    cv2.putText(
        frame,
        text,
        (x + 3, top - baseline - 1),
        cv2.FONT_HERSHEY_SIMPLEX,
        font,
        COLOR_TEXT,
        max(1, thickness - 1),
        cv2.LINE_AA,
    )


def draw_roi(frame: np.ndarray, roi: Optional[object]) -> np.ndarray:
    """Граница контролируемой зоны камеры."""
    pixels = _roi_pixels(roi, frame)
    if pixels is None:
        return frame
    rx1, ry1, rx2, ry2 = pixels
    thickness, _box_h, _font = _scale_for(frame)
    cv2.rectangle(frame, (rx1, ry1), (rx2, ry2), COLOR_ROI, thickness, cv2.LINE_AA)
    cv2.putText(
        frame,
        "Контролируемая зона",
        (rx1 + 4, max(16, ry1 - 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        COLOR_ROI,
        1,
        cv2.LINE_AA,
    )
    return frame


def draw_banner(frame: np.ndarray, text: str, ok: bool = True) -> np.ndarray:
    """Полоса-заглушка: «нет сигнала», «ожидание роликов» и подобное."""
    height, width = frame.shape[:2]
    bar = max(36, round(height * 0.09))
    color = COLOR_PERSON if ok else COLOR_DOWNTIME

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, bar), color, -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    font_scale = max(0.5, bar / 90)
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2)
    cv2.putText(
        frame,
        text,
        ((width - tw) // 2, (bar + th) // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        COLOR_TEXT,
        2,
        cv2.LINE_AA,
    )
    return frame


def render_frame(
    frame: np.ndarray,
    tracks: Sequence[Track],
    line_status: str = LineStatus.WORKING,
    roi: Optional[object] = None,
    show_roi: bool = True,
) -> np.ndarray:
    """Полная разметка кадра: ROI, путь, рамки, номера."""
    result = frame
    if show_roi:
        draw_roi(result, roi)
    draw_trails(result, tracks)
    draw_tracks(result, tracks, line_status=line_status, roi=roi)
    return result


def encode_jpeg(frame: np.ndarray, quality: int = 70) -> bytes:
    """Кадр в JPEG для MJPEG-потока.

    Качество 70 — компромисс: при 85 поток заметно тяжелее по трафику,
    а рамки и цифры на экране читаются уже хорошо.
    """
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return b""
    return buffer.tobytes()


__all__ = [
    "render_frame",
    "draw_tracks",
    "draw_trails",
    "draw_roi",
    "draw_banner",
    "encode_jpeg",
]