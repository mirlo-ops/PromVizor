"""Computer Vision «ПромВизор» (Этап 2).

Определяет:
* есть ли рабочий в контролируемой зоне (ROI) — YOLO;
* работает ли линия — анализ движения.

Не зависит от источника видео: на вход подаётся кадр.

    from app.vision import VisionPipeline, create_source

    with create_source("demo", scenario="stopped_no_person") as src:
        vision = VisionPipeline()
        while (frame := src.get_frame()) is not None:
            result = vision.process(frame)
            print(result.person_present, result.line_status)

Результат process() уже готов к отправке в SystemStatus
(app/core/types.py): person_present и line_status берутся отсюда.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.types import LineStatus
from app.vision.motion_detector import MotionDetector, MotionResult
from app.vision.person_detector import PersonDetection, PersonDetector
from app.vision.roi import Roi, default_roi, roi_from_settings

__all__ = [
    "Roi",
    "default_roi",
    "roi_from_settings",
    "PersonDetector",
    "PersonDetection",
    "MotionDetector",
    "MotionResult",
    "VisionPipeline",
    "VisionResult",
    "LineStatus",
]


@dataclass
class VisionResult:
    """Итог анализа одного кадра — готов для SystemStatus."""

    #: Рабочий обнаружен в ROI
    person_present: bool = False
    #: Состояние линии: WORKING / STOPPED / UNKNOWN
    line_status: str = LineStatus.UNKNOWN
    #: Уверенность YOLO (0.0–1.0)
    confidence: float = 0.0
    #: Уровень движения в ROI (0.0–1.0)
    motion: float = 0.0

    def describe(self) -> str:
        if self.person_present:
            person = "Рабочий обнаружен в контролируемой зоне камеры."
        else:
            person = "Рабочий не обнаружен в контролируемой зоне камеры."

        if self.line_status == LineStatus.WORKING:
            line = "Линия работает."
        elif self.line_status == LineStatus.STOPPED:
            line = "Линия остановлена."
        else:
            line = "Состояние линии определяется."

        return f"{person} {line}"


class VisionPipeline:
    """Соединяет детектор человека и детектор движения.

    Пример:

        pipeline = VisionPipeline()
        result = pipeline.process(frame)
        if result.line_status == LineStatus.STOPPED and not result.person_present:
            ...  # кандидат в простой
    """

    def __init__(
        self,
        roi: Roi | None = None,
        person_detector: PersonDetector | None = None,
        motion_detector: MotionDetector | None = None,
    ) -> None:
        self.roi = roi or default_roi()
        self.person = person_detector or PersonDetector(roi=self.roi)
        self.motion = motion_detector or MotionDetector(roi=self.roi)

    def process(self, frame) -> VisionResult:
        """Анализирует кадр: человек + движение линии."""
        detection = self.person.detect(frame)
        motion = self.motion.update(frame)

        return VisionResult(
            person_present=detection.person_present,
            line_status=motion.line_status,
            confidence=detection.confidence,
            motion=motion.motion,
        )

    def reset(self) -> None:
        """Сбрасывает накопленное состояние движение-детектора."""
        self.motion.reset()

    def warmup(self) -> None:
        """Заранее грузит модель YOLO (первый кадр будет быстрее)."""
        self.person.warmup()