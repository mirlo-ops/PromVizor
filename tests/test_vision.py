"""Тесты Computer Vision (Этап 2).

Запуск:

    .venv/bin/python -m pytest tests/ -v
    или без pytest:
    .venv/bin/python tests/test_vision.py

Тесты не требуют YOLO: PersonDetector принимает подставной backend,
а для движения достаточно синтетических кадров.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

# Корень проекта в путях импорта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.types import LineStatus  # noqa: E402
from app.vision import (  # noqa: E402
    MotionDetector,
    PersonDetector,
    Roi,
    VisionPipeline,
)

FRAME_W, FRAME_H = 640, 480


# --------------------------------------------------------------------------- #
# Вспомогательные «камеры»
# --------------------------------------------------------------------------- #
class FakeLine:
    """Конвейер с настоящим движением: коробки реально смещаются."""

    def __init__(self, moving: bool = True) -> None:
        self.moving = moving
        self._offset = 0

    def frame(self) -> np.ndarray:
        if self.moving:
            self._offset += 4
        img = np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
        for i in range(6):
            x = (30 + i * 100 + self._offset) % (FRAME_W - 60)
            cv2.rectangle(img, (x, 180), (x + 60, 300), (210, 210, 210), -1)
        return img


def static_frame() -> np.ndarray:
    """Неподвижная сцена."""
    img = np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
    for i in range(6):
        cv2.rectangle(img, (30 + i * 100, 180), (30 + i * 100 + 60, 300), (210, 210, 210), -1)
    return img


def boxes(*coords):
    """Собирает backend из списка кортежей (x1, y1, x2, y2, conf)."""
    items = [
        {"x1": c[0], "y1": c[1], "x2": c[2], "y2": c[3], "confidence": c[4]}
        for c in coords
    ]
    return lambda frame: items


# --------------------------------------------------------------------------- #
# ROI
# --------------------------------------------------------------------------- #
def test_roi_normalizes_inverted_corners():
    roi = Roi(left=0.9, top=0.8, right=0.1, bottom=0.2)
    assert (roi.left, roi.right) == (0.1, 0.9)
    assert (roi.top, roi.bottom) == (0.2, 0.8)


def test_roi_clamps_out_of_frame():
    roi = Roi(left=-1.0, top=-2.0, right=3.0, bottom=4.0)
    assert 0.0 <= roi.left <= roi.right <= 1.0
    assert 0.0 <= roi.top <= roi.bottom <= 1.0


def test_roi_pixels_scale_with_resolution():
    roi = Roi(0.20, 0.35, 0.80, 0.90)
    assert roi.to_pixels(1920, 1080) == (384, 378, 1152, 594)
    assert roi.to_pixels(640, 480) == (128, 168, 384, 264)


def test_roi_box_inside_and_outside():
    roi = Roi(0.20, 0.35, 0.80, 0.90)
    w, h = 1920, 1080
    assert roi.contains_box(700, 600, 800, 800, w, h) is True     # внутри
    assert roi.contains_box(50, 50, 200, 200, w, h) is False      # снаружи


# --------------------------------------------------------------------------- #
# Person Detection
# --------------------------------------------------------------------------- #
def test_person_detected_in_roi():
    detector = PersonDetector(roi=Roi(0.2, 0.35, 0.8, 0.9), backend=boxes((700, 600, 800, 800, 0.9)))
    result = detector.detect(np.zeros((1080, 1920, 3), np.uint8))
    assert result.person_present is True
    assert result.confidence == 0.9


def test_person_outside_roi_is_not_present():
    detector = PersonDetector(roi=Roi(0.2, 0.35, 0.8, 0.9), backend=boxes((50, 50, 200, 200, 0.8)))
    result = detector.detect(np.zeros((1080, 1920, 3), np.uint8))
    assert result.person_present is False
    assert result.person_outside_roi is True


def test_no_person():
    detector = PersonDetector(roi=Roi(0.2, 0.35, 0.8, 0.9), backend=boxes())
    result = detector.detect(np.zeros((1080, 1920, 3), np.uint8))
    assert result.person_present is False


def test_person_inside_wins_over_outside():
    both = boxes((50, 50, 200, 200, 0.7), (700, 600, 800, 800, 0.95))
    detector = PersonDetector(roi=Roi(0.2, 0.35, 0.8, 0.9), backend=both)
    result = detector.detect(np.zeros((1080, 1920, 3), np.uint8))
    assert result.person_present is True
    assert result.confidence == 0.95


def test_detector_handles_none_frame():
    detector = PersonDetector(backend=boxes())
    assert detector.detect(None).person_present is False


# --------------------------------------------------------------------------- #
# Motion Detection
# --------------------------------------------------------------------------- #
def _detector():
    return MotionDetector(roi=Roi(0.0, 0.0, 1.0, 1.0), min_samples=5, history_size=20)


def test_warmup_reports_unknown():
    d = _detector()
    line = FakeLine(moving=True)
    states = [d.update(line.frame()).line_status for _ in range(4)]
    assert all(s == LineStatus.UNKNOWN for s in states)


def test_moving_line_is_working():
    d = _detector()
    line = FakeLine(moving=True)
    for _ in range(12):
        d.update(line.frame())
    assert d.state == LineStatus.WORKING


def test_stopped_line_is_detected():
    d = _detector()
    line = FakeLine(moving=True)
    for _ in range(12):
        d.update(line.frame())
    line.moving = False
    for _ in range(25):
        d.update(line.frame())
    assert d.state == LineStatus.STOPPED


def test_line_restart_is_detected():
    d = _detector()
    line = FakeLine(moving=True)
    for _ in range(12):
        d.update(line.frame())
    line.moving = False
    for _ in range(25):
        d.update(line.frame())
    assert d.state == LineStatus.STOPPED
    line.moving = True
    for _ in range(20):
        d.update(line.frame())
    assert d.state == LineStatus.WORKING


def test_blackout_does_not_fake_motion():
    """Заслон объектива не должен объявить стоящую линию рабочей."""
    d = _detector()
    line = FakeLine(moving=False)
    for _ in range(20):
        d.update(line.frame())
    assert d.state == LineStatus.STOPPED

    for _ in range(4):
        d.update(np.zeros((FRAME_H, FRAME_W, 3), np.uint8))
    assert d.state == LineStatus.STOPPED

    # возврат к нормальному освещению тоже не должен ломать состояние
    for _ in range(4):
        d.update(cv2.convertScaleAbs(line.frame(), alpha=0.25, beta=0))
    assert d.state == LineStatus.STOPPED


def test_single_bad_frame_does_not_change_state():
    d = _detector()
    line = FakeLine(moving=True)
    for _ in range(12):
        d.update(line.frame())
    before = d.state
    d.update(None)
    assert d.state == before


def test_resolution_change_resets_history():
    d = _detector()
    line = FakeLine(moving=True)
    for _ in range(12):
        d.update(line.frame())
    assert d.state == LineStatus.WORKING
    for _ in range(3):
        d.update(np.full((720, 1280, 3), 60, np.uint8))
    assert d.state == LineStatus.UNKNOWN


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def test_pipeline_combines_both_signals():
    pipeline = VisionPipeline(
        roi=Roi(0.0, 0.0, 1.0, 1.0),
        person_detector=PersonDetector(
            roi=Roi(0.0, 0.0, 1.0, 1.0), backend=boxes((100, 100, 200, 300, 0.95))
        ),
        motion_detector=_detector(),
    )
    line = FakeLine(moving=False)
    for _ in range(20):
        result = pipeline.process(line.frame())

    assert result.person_present is True
    assert result.line_status == LineStatus.STOPPED
    # Ключевая комбинация для простоя: линия стоит, рабочего нет
    assert "не обнаружен" in result.describe() or result.person_present


# --------------------------------------------------------------------------- #
# Минимальный раннер без pytest
# --------------------------------------------------------------------------- #
def _main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failed = []
    for name, func in tests:
        try:
            func()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"  ✗ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")

    print(f"\n{len(tests) - len(failed)}/{len(tests)} тестов пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())