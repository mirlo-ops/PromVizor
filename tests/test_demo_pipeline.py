"""Тесты конвейера: скорость детекции и состояние линии по сценарию.

Проверяются два решения, принятых ради демонстрации 16.10.2026.

1. YOLO запускается не на каждом кадре, а раз в DETECT_EVERY_FRAMES.
   Без этого окно камеры шло примерно один кадр в секунду: YOLO на
   сервере даёт около секунды на кадр, и всё остальное в конвейере —
   миллисекунды.

2. В демо-сценариях «линия стоит» и «линия стоит + рабочий ушёл»
   состояние линии задаётся сценарием. В роликах конвейер
   нарисован, но не двигается, и детектор движения честно отвечает
   WORKING — на демонстрации это выглядит как ошибка системы.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.types import LineStatus
from app.pipeline.runner import Pipeline

_tests = []


def test(name):
    def wrap(fn):
        _tests.append((name, fn))
        return fn

    return wrap


class _FakeDetection:
    def __init__(self, detections):
        self.detections = detections
        self.person_present = bool(detections)


def _pipeline(detect_every=1, mode="webcam", scenario="normal") -> Pipeline:
    """Конвейер без базы и без видео: нужен только расчёт кадра."""
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.detect_every = detect_every
    pipeline.scenario = scenario

    class _State:
        pass

    pipeline.state = _State()
    pipeline.state.mode = mode
    pipeline.state.camera_index = 0
    pipeline.state.error = ""
    pipeline._last_detections = None
    pipeline._frames_since_detect = 0

    calls = {"n": 0}
    pipeline._detect = lambda frame: (
        calls.__setitem__("n", calls["n"] + 1) or [(100.0, 100.0, 200.0, 300.0)]
    )
    pipeline._detect_count = calls
    return pipeline


# --------------------------------------------------------------------------- #
# Частота детекции
# --------------------------------------------------------------------------- #
@test("на каждом кадре при detect_every=1")
def _():
    pipeline = _pipeline(detect_every=1)
    for _ in range(5):
        pipeline._detect_sparse(object())
    assert pipeline._detect_count["n"] == 5, (
        f"ожидалось 5 детекций, прошло {pipeline._detect_count['n']}"
    )


@test("раз в три кадра при detect_every=3")
def _():
    # Ключевая проверка скорости: из 10 кадров YOLO запускается
    # примерно 4 раза, а не 10.
    pipeline = _pipeline(detect_every=3)
    for _ in range(10):
        pipeline._detect_sparse(object())
    detected = pipeline._detect_count["n"]
    assert detected <= 5, f"YOLO запустился {detected} раз из 10 — экономии нет"
    assert detected >= 3, f"YOLO запустился слишком редко: {detected}"


@test("между детекциями возвращается прошлый результат")
def _():
    # Иначе трек обнулялся бы на пропущенных кадрах, и рамка человека
    # мигала: то видна, то нет.
    pipeline = _pipeline(detect_every=3)
    first = pipeline._detect_sparse(object())
    second = pipeline._detect_sparse(object())
    third = pipeline._detect_sparse(object())
    assert first is second, "прошлый результат не переиспользован"
    assert first is third, "прошлый результат не переиспользован"


@test("синтетика считает каждый кадр — там нет YOLO")
def _():
    pipeline = _pipeline(detect_every=3, mode="synthetic")
    for _ in range(4):
        pipeline._detect_sparse(object())
    assert pipeline._detect_count["n"] == 4, (
        "в синтетике детекция должна идти каждый кадр"
    )


@test("detect_every=0 не ломает конвейер")
def _():
    # Ноль в .env — обычная ошибка при настройке. Раньше это дало бы
    # деление на ноль или вечный цикл без детекции.
    pipeline = _pipeline(detect_every=0)
    for _ in range(3):
        pipeline._detect_sparse(object())
    assert pipeline._detect_count["n"] >= 1, "при detect_every=0 детекций не было"


# --------------------------------------------------------------------------- #
# Состояние линии по сценарию
# --------------------------------------------------------------------------- #
def _status_of(mode: str, scenario: str):
    pipeline = _pipeline(detect_every=1, mode=mode, scenario=scenario)
    pipeline.state.mode = mode
    return pipeline._forced_line_status()


@test("сценарий «линия стоит» — STOPPED")
def _():
    assert _status_of("demo", "stopped") == LineStatus.STOPPED


@test("сценарий «линия стоит + рабочий ушёл» — STOPPED")
def _():
    assert _status_of("demo", "stopped_no_person") == LineStatus.STOPPED


@test("сценарий «норма» — детектор решает сам")
def _():
    # В сценарии 1 линия движется, и навязывать STOPPED было бы
    # прямой ложью на экране.
    assert _status_of("demo", "normal") is None
    assert _status_of("demo", "person_left") is None


@test("на настоящей камере сценарий не применяется")
def _():
    # Камера сервера и камера из браузера определяются движением.
    # Навязать им STOPPED по остаткам сценария — значит соврать
    # оператору о том, что видно на камере.
    assert _status_of("webcam", "stopped") is None
    assert _status_of("browser", "stopped") is None
    assert _status_of("synthetic", "stopped") is None


@test("список принудительных сценариев совпадает с демо-файлами")
def _():
    # Сценарий из этого списка, для которого нет ролика, — это
    # опечатка: он молча ничего не делал бы.
    from app.video.demo import DemoSource

    for scenario in Pipeline.SCENARIOS_LINE_STOPPED:
        assert scenario in DemoSource.SCENARIO_FILES, (
            f"сценарий {scenario!r} помечен как «линия стоит», "
            "но такого демо-сценария нет"
        )


def main() -> int:
    failed = []
    for name, fn in _tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"  ✗ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")

    print(f"\n{len(_tests) - len(failed)}/{len(_tests)} тестов пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())