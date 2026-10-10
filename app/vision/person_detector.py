"""Определение человека в кадре (Этап 2 — Computer Vision).

Основной результат — флаг:

    person_present: bool

Формулировка статуса (важно для Telegram и отчётов):
    ✅ «Рабочий не обнаружен в контролируемой зоне камеры.»
    ❌ «Рабочий отсутствует на предприятии.»

Система видит только то, что попадает в камеру, поэтому утверждать
что-либо о фактическом присутствии сотрудника нельзя.

YOLO подключается лениво: модуль импортируется и модель грузится только
при первом вызове detect(). Это позволяет импортировать пакет без
установленного ultralytics и подставить свою реализацию через backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from app.core.config import settings
from app.vision.roi import Roi, default_roi

#: Бокс человека в пикселях кадра
Box = tuple  # (x1, y1, x2, y2)

#: Подпись детектора: принимает кадр, возвращает список боксов.
#: Боксы — кортежи из 4 чисел (x1, y1, x2, y2) либо словари с ключами
#: "x1", "y1", "x2", "y2" (как отдаёт ultralytics).
DetectionBackend = Callable[["object"], Sequence[object]]


@dataclass
class PersonDetection:
    """Результат анализа одного кадра."""

    #: Рабочий обнаружен ВНУТРИ ROI
    person_present: bool = False
    #: Рабочий виден в кадре, но вне контролируемой зоны
    person_outside_roi: bool = False
    #: Все найденные боксы людей в пикселях
    detections: List[Box] = field(default_factory=list)
    #: Максимальная уверенность среди боксов в ROI (0.0–1.0)
    confidence: float = 0.0
    #: Пояснение для логов и интерфейса
    reason: str = ""

    def describe(self) -> str:
        if self.person_present:
            return "Рабочий обнаружен в контролируемой зоне камеры."
        if self.person_outside_roi:
            return "Рабочий виден в кадре, но вне контролируемой зоны."
        return "Рабочий не обнаружен в контролируемой зоне камеры."


class PersonDetector:
    """Ищет человека и определяет, находится ли он в ROI.

    По умолчанию используется YOLO (ultralytics). Модель грузится
    лениво — при первом detect().

    Пример:

        detector = PersonDetector()
        detection = detector.detect(frame)
        if detection.person_present:
            ...
    """

    def __init__(
        self,
        roi: Optional[Roi] = None,
        model_name: Optional[str] = None,
        confidence: Optional[float] = None,
        person_classes: Optional[Sequence[int]] = None,
        backend: Optional[DetectionBackend] = None,
    ) -> None:
        self.roi = roi or default_roi()
        self.model_name = model_name or settings.yolo_model
        self.confidence = (
            settings.yolo_confidence if confidence is None else float(confidence)
        )
        self.person_classes = tuple(
            person_classes if person_classes is not None else settings.yolo_person_classes
        )
        self._backend = backend
        self._model = None

    # ------------------------------------------------------------------ #
    # Модель
    # ------------------------------------------------------------------ #
    def _load_model(self):
        """Ленивая загрузка YOLO. Бросает понятную ошибку без ultralytics."""
        if self._model is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:  # pragma: no cover - зависит от окружения
                raise RuntimeError(
                    "YOLO не установлен. Установите пакет: "
                    "pip install ultralytics\n"
                    "Либо передайте собственную реализацию: "
                    "PersonDetector(backend=my_detector)"
                ) from exc
            self._model = YOLO(self.model_name)
        return self._model

    def warmup(self) -> None:
        """Принудительно грузит модель — чтобы первый кадр не ждал."""
        self._load_model()

    # ------------------------------------------------------------------ #
    # Детекция
    # ------------------------------------------------------------------ #
    def detect(self, frame) -> PersonDetection:
        """Анализирует кадр и возвращает PersonDetection."""
        if frame is None:
            return PersonDetection(reason="пустой кадр")

        boxes = self._run_backend(frame)

        h, w = _frame_size(frame)
        inside: List[Box] = []
        outside: List[Box] = []
        best_conf = 0.0

        for raw in boxes:
            box, conf = _normalize_box(raw)
            if box is None:
                continue
            if self.roi.contains_box(box[0], box[1], box[2], box[3], w, h):
                inside.append(box)
                if conf > best_conf:
                    best_conf = conf
            else:
                outside.append(box)

        result = PersonDetection(
            person_present=bool(inside),
            person_outside_roi=bool(outside) and not inside,
            detections=inside + outside,
            confidence=round(best_conf, 3),
        )
        result.reason = result.describe()
        return result

    def person_in_roi(self, frame) -> bool:
        """Короткая проверка: есть ли рабочий в контролируемой зоне."""
        return self.detect(frame).person_present

    def _run_backend(self, frame) -> Sequence[object]:
        """Вызывает YOLO или подставленную реализацию."""
        if self._backend is not None:
            return self._backend(frame) or []

        model = self._load_model()
        # classes=self.person_classes — YOLO вернёт только нужные классы
        results = model.predict(
            frame,
            conf=self.confidence,
            classes=list(self.person_classes),
            # Уменьшенный размер входа заметно ускоряет CPU-инференс на
            # обычных веб-камерах, сохраняя достаточную детализацию для людей.
            imgsz=416,
            verbose=False,
        )
        return _boxes_from_results(results)


# ---------------------------------------------------------------------- #
# Разбор результатов YOLO
# ---------------------------------------------------------------------- #
def _boxes_from_results(results) -> List[object]:
    """Достаёт боксы из объекта Results ultralytics."""
    boxes: List[object] = []
    for res in results or []:
        raw = getattr(res, "boxes", None)
        if raw is None:
            continue
        for xyxy, conf in zip(_iter(raw.xyxy), _iter(getattr(raw, "conf", []))):
            box = [float(v) for v in _to_list(xyxy)]
            boxes.append(
                {
                    "x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3],
                    "confidence": float(conf),
                }
            )
    return boxes


def _normalize_box(raw) -> tuple:
    """Приводит бокс к (x1, y1, x2, y2, confidence)."""
    if isinstance(raw, dict):
        if not all(k in raw for k in ("x1", "y1", "x2", "y2")):
            return None, 0.0
        vals = [float(raw["x1"]), float(raw["y1"]), float(raw["x2"]), float(raw["y2"])]
        conf = float(raw.get("confidence", raw.get("conf", 0.0)) or 0.0)
        return _ordered(vals), conf

    seq = _to_list(raw)
    if len(seq) < 4:
        return None, 0.0
    vals = [float(v) for v in seq[:4]]
    conf = float(seq[4]) if len(seq) >= 5 else 0.0
    return _ordered(vals), conf


def _ordered(box: List[float]) -> Box:
    """Гарантирует x1<=x2 и y1<=y2."""
    x1, x2 = sorted((box[0], box[2]))
    y1, y2 = sorted((box[1], box[3]))
    return (x1, y1, x2, y2)


def _to_list(value) -> List:
    """numpy-массив / тензор → обычный список Python."""
    tolist = getattr(value, "tolist", None)
    return tolist() if callable(tolist) else list(value)


def _iter(value):
    """Итерирует по numpy-массиву, tensor или списку."""
    if value is None:
        return []
    return value


def _frame_size(frame) -> tuple:
    """Возвращает (высота, ширина) кадра."""
    shape = getattr(frame, "shape", None)
    if shape is None or len(shape) < 2:
        return 0, 0
    return int(shape[0]), int(shape[1])
