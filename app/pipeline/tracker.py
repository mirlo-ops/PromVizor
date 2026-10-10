"""Трекинг людей между кадрами (часть конвейера).

Задача — показать не просто «человек в кадре», а конкретного человека:
его номер и путь, который он прошёл. Для этого боксы YOLO нужно
сопоставлять между кадрами.

Как это работает. Трек — это ID и история центров бокса. Между двумя
кадрами боксы сопоставляются по IoU (intersection over union, доля
пересечения): если рамки сильно перекрываются, это один и тот же
человек. YOLO сам по себе ID не даёт — из кадра в кадр нумерация
сбилась бы, и вместо одного человека на экране мигали бы разные
номера.

Что трекер НЕ делает. Он не различает «того же» человека после
ухода из кадра и возвращения: такой человек получит новый ID. Отличить
их без анализа внешности или времени отсутствия нельзя, а в Alpha
такого усложнения не требуется.

Почему не готовые решения. ultralytics умеет трекить сам, но там
трекер встроен в их пайплайн и переиспользовать его отдельно от
нашей детекции неудобно. Свой трекер — около сотни строк, полный
контроль и никакой зависимости.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Sequence, Tuple

#: Сколько точек пути храним на трек. Больше не нужно: экран всё
#: равно не покажет длинную историю, а память растёт с каждым кадром.
MAX_TRAIL_POINTS = 60

#: Ниже этого IoU боксы считаются разными людьми.
IOU_THRESHOLD = 0.3

#: Максимальный сдвиг центра между кадрами в долях от размера кадра.
#: Без этого ограничения две рамки на противоположных концах картинки
#: иногда считались бы одним человеком.
MAX_CENTER_SHIFT = 0.25

#: Сколько кадров трек может не видеться, прежде чем будет удалён.
#: Нужен на случай, когда человек на секунду перекрылся другим.
MAX_MISSES = 15


# --------------------------------------------------------------------------- #
# Геометрия
# --------------------------------------------------------------------------- #
def _to_xyxy(box: object) -> Tuple[float, float, float, float]:
    """Приводит бокс к виду (x1, y1, x2, y2).

    YOLO отдаёт координаты по-разному: кортежем или словарём с
    ключами. Нормализуем здесь, чтобы дальше не думать о формате.
    """
    if isinstance(box, dict):
        return (
            float(box["x1"]),
            float(box["y1"]),
            float(box["x2"]),
            float(box["y2"]),
        )
    x1, y1, x2, y2 = box[:4]
    return float(x1), float(y1), float(x2), float(y2)


def iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    """Доля пересечения двух рамок: 0 — не пересекаются, 1 — совпали."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    # Пересечение
    left = max(ax1, bx1)
    top = max(ay1, by1)
    right = min(ax2, bx2)
    bottom = min(ay2, by2)
    if right <= left or bottom <= top:
        return 0.0

    intersection = (right - left) * (bottom - top)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def center(box: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Центр рамки."""
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


# --------------------------------------------------------------------------- #
# Трек
# --------------------------------------------------------------------------- #
@dataclass
class Track:
    """Один человек, прослеживаемый между кадрами."""

    track_id: int
    box: Tuple[float, float, float, float]
    confidence: float = 0.0
    #: История центров: [(x, y), ...] — из неё рисуется путь
    trail: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=MAX_TRAIL_POINTS))
    #: Кадров без обнаружения этого человека
    misses: int = 0
    #: Всего кадров, в которых человек был виден
    age: int = 0

    def update(self, box: Tuple[float, float, float, float], confidence: float) -> None:
        self.box = box
        self.confidence = confidence
        self.misses = 0
        self.age += 1
        self.trail.append(center(box))


class PersonTracker:
    """Сопоставляет боксы между кадрами и ведёт историю пути.

    Пример:

        tracker = PersonTracker()
        tracks = tracker.update(detections)
        for track in tracks:
            print(track.track_id, track.box, list(track.trail))
    """

    def __init__(
        self,
        iou_threshold: float = IOU_THRESHOLD,
        max_center_shift: float = MAX_CENTER_SHIFT,
        max_misses: int = MAX_MISSES,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.max_center_shift = max_center_shift
        self.max_misses = max_misses

        self._tracks: List[Track] = []
        self._next_id = 1

    # ------------------------------------------------------------------ #
    # Обновление
    # ------------------------------------------------------------------ #
    def update(self, detections: Sequence[object]) -> List[Track]:
        """Сопоставляет новые боксы с треками и возвращает видимые треки.

        `detections` — то, что вернул YOLO: кортежи (x1, y1, x2, y2)
        либо словари с ключами "x1".."y2".
        """
        boxes = [_to_xyxy(d) for d in detections]
        confidences = [float(d.get("confidence", 0.0)) if isinstance(d, dict) else 0.0
                       for d in detections]

        # Сопоставление «лучшее совпадение первым» — так один человек
        # не привязывается сразу к двум трекам.
        pairs = []
        for track_index, track in enumerate(self._tracks):
            for box_index, box in enumerate(boxes):
                overlap = iou(track.box, box)
                if overlap < self.iou_threshold:
                    continue
                if not self._shift_ok(track.box, box):
                    continue
                pairs.append((overlap, track_index, box_index))

        pairs.sort(reverse=True)
        used_tracks = set()
        used_boxes = set()

        for _overlap, track_index, box_index in pairs:
            if track_index in used_tracks or box_index in used_boxes:
                continue
            used_tracks.add(track_index)
            used_boxes.add(box_index)
            self._tracks[track_index].update(boxes[box_index], confidences[box_index])

# Треки, которых не видно на этом кадре, считаем пропущенными.
        # Важно: делаем это ДО создания новых треков. Иначе только что
        # созданный человек тут же получил бы misses=1 и был выброшен
        # из выдачи — на экране не появлялось бы никого.
        # Сравниваем по индексу, а не по объекту: dataclass сравнивается
        # по полям, и два разных человека с похожими рамками сочлись бы
        # одним.
        for index, track in enumerate(self._tracks):
            if index not in used_tracks:
                track.misses += 1

        self._tracks = [t for t in self._tracks if t.misses <= self.max_misses]

        # Новые боксы — новые люди
        for box_index, box in enumerate(boxes):
            if box_index in used_boxes:
                continue
            track = Track(track_id=self._next_id, box=box, confidence=confidences[box_index])
            track.age = 1
            track.trail.append(center(box))
            self._next_id += 1
            self._tracks.append(track)

        # Возвращаем только те, кого видели на текущем кадре:
        # иначе на экране появлялись бы «призраки» ушедших людей
        return [t for t in self._tracks if t.misses == 0]

    def _shift_ok(self, track_box, box) -> bool:
        """Проверяет, что центр не улетел слишком далеко.

        На кадрах с двумя людьми перекрытие рамок бывает случайным;
        ограничение по сдвигу отсекает такие ложные совпадения.
        """
        if self.max_center_shift <= 0:
            return True
        cx1, cy1 = center(track_box)
        cx2, cy2 = center(box)
        width = max(1.0, track_box[2] - track_box[0])
        height = max(1.0, track_box[3] - track_box[1])
        dx = abs(cx2 - cx1) / width
        dy = abs(cy2 - cy1) / height
        return dx <= self.max_center_shift * 4 and dy <= self.max_center_shift * 4

    # ------------------------------------------------------------------ #
    # Служебное
    # ------------------------------------------------------------------ #
    @property
    def tracks(self) -> List[Track]:
        """Все треки, включая на секунду пропавшие."""
        return list(self._tracks)

    def reset(self) -> None:
        """Забывает всех людей. Вызывается при смене источника видео."""
        self._tracks.clear()
        self._next_id = 1


__all__ = [
    "PersonTracker",
    "Track",
    "iou",
    "MAX_TRAIL_POINTS",
    "IOU_THRESHOLD",
]