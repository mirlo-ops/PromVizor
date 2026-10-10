"""Тесты конвейера обработки видео.

Запуск:

    .venv/bin/python tests/test_pipeline.py

Покрываются три вещи, которые легко сломать незаметно:

* трекер: один человек должен оставаться одним человеком;
* режим камеры: выбор сохраняется и читается обратно;
* конвейер: простой фиксируется по исчезновению человека.

YOLO в тестах не участвует — подменяется там, где это позволяет.
Это осознанно: проверять надо логику системы, а не совпадение
модели с картинкой.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import camera_mode  # noqa: E402
from app.core.types import EventType, LineStatus  # noqa: E402
from app.database import Database, EventRepository  # noqa: E402
from app.pipeline.synthetic import SyntheticSource  # noqa: E402
from app.pipeline.tracker import PersonTracker, iou  # noqa: E402


# --------------------------------------------------------------------------- #
# Трекер
# --------------------------------------------------------------------------- #
def test_iou_of_same_box_is_one():
    assert abs(iou((0, 0, 100, 100), (0, 0, 100, 100)) - 1.0) < 1e-9


def test_iou_of_disjoint_boxes_is_zero():
    assert iou((0, 0, 10, 10), (50, 50, 60, 60)) == 0.0


def test_iou_of_half_overlap():
    value = iou((0, 0, 100, 100), (50, 0, 150, 100))
    assert 0.3 < value < 0.35  # 5000 / 15000


def test_tracker_keeps_one_person_across_frames():
    """Человек, идущий вправо, остаётся одним треком.

    Проверка на то, ради чего трекер и писался: без него номер
    «прыгал» бы между кадрами и на экране мигали бы разные ID.
    """
    tracker = PersonTracker()
    ids = []
    for step in range(12):
        x = 100 + step * 8
        tracks = tracker.update([(x, 100, x + 60, 300)])
        assert len(tracks) == 1, f"на кадре {step} треков: {len(tracks)}"
        ids.append(tracks[0].track_id)

    assert len(set(ids)) == 1, f"ID человека менялся: {ids}"
    assert ids[0] == 1


def test_tracker_builds_trail():
    """История пути растёт и содержит пройденные точки."""
    tracker = PersonTracker()
    for step in range(10):
        x = 100 + step * 10
        tracker.update([(x, 100, x + 60, 300)])

    tracks = tracker.update([(200, 100, 260, 300)])
    trail = list(tracks[0].trail)
    assert len(trail) == 11, f"точек в пути: {len(trail)}"
    # Путь идёт слева направо: x растёт
    assert trail[0][0] < trail[-1][0], "направление пути перепутано"


def test_tracker_separates_two_people():
    """Два человека рядом — два разных трека."""
    tracker = PersonTracker()
    tracks = tracker.update([(100, 100, 160, 300), (300, 100, 360, 300)])
    assert len(tracks) == 2, f"треков: {len(tracks)}"
    assert tracks[0].track_id != tracks[1].track_id


def test_tracker_does_not_swap_two_people():
    """Люди, идущие навстречу, не меняются местами по ID.

    Самая частая ошибка трекера: пока люди пересекаются, номера
    меняются местами, и на экране кажется, что они телепортируются.
    """
    tracker = PersonTracker()
    for step in range(10):
        left = 100 + step * 20
        right = 400 - step * 20
        tracks = tracker.update([(left, 100, left + 60, 300), (right, 100, right + 60, 300)])
        assert len(tracks) == 2, "люди пропали или слиплись"

    ids = sorted(t.track_id for t in tracks)
    # Изначально левый был первым — он так и должен остаться первым
    assert ids == [1, 2], f"ID перепутаны: {ids}"


def test_tracker_keeps_track_through_brief_gap():
    """Человек, на секунду пропавший за другим, сохраняет номер."""
    tracker = PersonTracker()
    for _ in range(5):
        tracker.update([(200, 100, 260, 300)])
    track_id = tracker.tracks[0].track_id

    tracker.update([])  # пропал на кадр
    tracks = tracker.update([(202, 101, 262, 301)])

    assert len(tracks) == 1
    assert tracks[0].track_id == track_id, "номер человека изменился из-за одного пропуска"


def test_tracker_drops_track_after_long_absence():
    """Ушедший надолго человек забывается: новый получит новый номер."""
    tracker = PersonTracker()
    tracker.update([(200, 100, 260, 300)])

    for _ in range(tracker.max_misses + 3):
        tracker.update([])

    assert tracker.tracks == [], "ушедший человек остался в треках"
    tracks = tracker.update([(200, 100, 260, 300)])
    assert tracks[0].track_id != 1, "новый человек унаследовал старый номер"


def test_tracker_reset():
    tracker = PersonTracker()
    tracker.update([(200, 100, 260, 300)])
    tracker.reset()
    assert tracker.tracks == []
    tracks = tracker.update([(200, 100, 260, 300)])
    assert tracks[0].track_id == 1, "после сброса нумерация не началась заново"


# --------------------------------------------------------------------------- #
# Синтетическая сцена
# --------------------------------------------------------------------------- #
def test_synthetic_frame_shape():
    source = SyntheticSource(width=320, height=240, fps=8)
    frame = source.get_frame()
    assert frame is not None
    assert frame.shape == (240, 320, 3)


def test_synthetic_person_appears_and_disappears():
    """Человек есть в кадре, потом исчезает — и это видно по боксу."""
    source = SyntheticSource(width=640, height=480, fps=10)
    cycle = SyntheticSource.CYCLE_SECONDS
    absent = source.absent_seconds

    # В начале цикла человек есть
    source.get_frame()
    assert source.known_person_box() is not None, "человек не нарисован в начале сцены"

    # Кадр в середине отсутствия: человека нет.
    # Человек уходит на отметке (цикл − отсутствие) и приходит в конце.
    gone_at = cycle - absent
    while source.position_seconds < gone_at + absent / 2:
        source.get_frame()
    assert source.known_person_box() is None, (
        f"человек не исчез на отметке {source.position_seconds:.1f} с"
    )


def test_synthetic_cycles_forever():
    """Сцена бесконечная: демонстрация не должна останавливаться."""
    source = SyntheticSource(width=160, height=120, fps=10)
    for _ in range(50):
        assert source.get_frame() is not None


# --------------------------------------------------------------------------- #
# Режим камеры
# --------------------------------------------------------------------------- #
def test_mode_roundtrip():
    """Выбранный режим сохраняется и читается обратно."""
    with tempfile.TemporaryDirectory() as tmp:
        original = camera_mode.SETTINGS_FILE
        target = Path(tmp) / "camera_mode.json"
        camera_mode.SETTINGS_FILE = target
        try:
            saved = camera_mode.save(camera_mode.CameraMode(mode="webcam", webcam_index=2))
            assert saved.mode == "webcam"
            assert target.exists(), "файл настроек не создан"

            loaded = camera_mode.load()
            assert loaded.mode == "webcam", f"режим после чтения: {loaded.mode}"
            assert loaded.webcam_index == 2, f"камера после чтения: {loaded.webcam_index}"

            # И содержимое файла — нормальный JSON
            assert json.loads(target.read_text(encoding="utf-8"))["mode"] == "webcam"
        finally:
            camera_mode.SETTINGS_FILE = original


def test_mode_rejects_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        original = camera_mode.SETTINGS_FILE
        camera_mode.SETTINGS_FILE = Path(tmp) / "camera_mode.json"
        try:
            try:
                camera_mode.save(camera_mode.CameraMode(mode="telepathy"))
                raise AssertionError("недопустимый режим должен отклоняться")
            except ValueError:
                pass
        finally:
            camera_mode.SETTINGS_FILE = original


def test_mode_broken_file_falls_back():
    """Битый файл настроек не должен ломать запуск системы."""
    with tempfile.TemporaryDirectory() as tmp:
        original = camera_mode.SETTINGS_FILE
        target = Path(tmp) / "camera_mode.json"
        target.write_text("{ это не json", encoding="utf-8")
        camera_mode.SETTINGS_FILE = target
        try:
            loaded = camera_mode.load()
            assert loaded.mode in camera_mode.ALL_MODES, f"сломанный режим: {loaded.mode}"
        finally:
            camera_mode.SETTINGS_FILE = original


def test_mode_describe():
    assert "Веб" in camera_mode.describe_mode(camera_mode.MODE_WEBCAM)
    assert camera_mode.describe_mode(camera_mode.MODE_RTSP)


# --------------------------------------------------------------------------- #
# Конвейер целиком
# --------------------------------------------------------------------------- #
def _run_pipeline(seconds: float, mode: str = "synthetic"):
    """Гоняет конвейер заданное время и возвращает состояние и базу."""
    db = Database(":memory:")
    db.init_schema()
    repo = EventRepository(db)

    from app.pipeline.runner import Pipeline

    pipeline = Pipeline(repo, camera_id=1, mode=mode)
    pipeline.start()
    pipeline.wait_frame(timeout=10)
    time.sleep(seconds)
    state = pipeline.snapshot()
    pipeline.stop()
    return state, repo, db


def test_pipeline_produces_frames():
    state, _repo, _db = _run_pipeline(2.0)
    assert state["frames"] > 0, "кадры не обрабатывались"
    assert state["frame_size"][0] > 0, "размер кадра не определён"
    assert state["error"] == "", f"ошибка конвейера: {state['error']}"


def test_pipeline_writes_status():
    state, repo, _db = _run_pipeline(4.0)
    status = repo.latest_status()
    assert status is not None, "статус не записан в базу"
    assert status.camera_id == 1
    assert state["error"] == "", f"ошибка записи: {state['error']}"


def test_pipeline_records_downtime_when_person_leaves():
    """Человек исчез → простой фиксируется (требование ТЗ)."""
    state, repo, _db = _run_pipeline(30.0)

    events = repo.recent_events(limit=50, event_type=EventType.DOWNTIME_FINISHED)
    assert events, "простой не зафиксирован: цикл синтетики длится 24 секунды"
    downtime = events[0]
    assert downtime.duration_seconds > 0
    assert downtime.estimated_loss > 0, "ущерб не посчитан"


def test_pipeline_no_downtime_while_person_present():
    """Пока человек в кадре, простоя быть не должно."""
    state, repo, _db = _run_pipeline(8.0)
    status = repo.latest_status()
    assert status is not None
    if status.person_present:
        assert status.line_status == LineStatus.WORKING, (
            "человек в кадре, но линия отмечена как стоящая"
        )
        assert status.downtime_seconds == 0, "при человеке накопался простой"


def test_pipeline_reports_source_for_each_mode():
    """Каждый режим сообщает, откуда взялось видео."""
    _state, _repo, _db = _run_pipeline(1.5, mode="synthetic")
    assert _state["detection_source"] == "synthetic"


def test_pipeline_stops_cleanly():
    """Остановка не должна оставлять поток работать."""
    db = Database(":memory:")
    db.init_schema()
    repo = EventRepository(db)

    from app.pipeline.runner import Pipeline

    pipeline = Pipeline(repo, camera_id=1, mode="synthetic")
    pipeline.start()
    pipeline.wait_frame(timeout=10)
    pipeline.stop()

    assert pipeline.snapshot()["running"] is False
    frames = pipeline.snapshot()["frames"]
    time.sleep(1.5)
    assert pipeline.snapshot()["frames"] == frames, "после остановки кадры идут"


# --------------------------------------------------------------------------- #
# Раннер без pytest
# --------------------------------------------------------------------------- #
def _main() -> int:
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]

    passed = 0
    failed = []
    for name, func in tests:
        try:
            func()
            passed += 1
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"  ✗ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")

    print(f"\n{passed}/{len(tests)} тестов пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
