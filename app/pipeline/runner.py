"""Конвейер обработки видео.

До этого этапа модули существовали по отдельности: видео, детекторы,
движок событий, база. Не было процесса, который их соединяет. Он
здесь — и работает в фоне, пока API отдаёт кадры и данные.

Что делает цикл, по кадру:

    кадр → детекция человека и движения → решение EventEngine
         → события и статус в базу → разметка кадра для показа

Почему конвейер живёт в своём потоке, а не в запросе. Обработка идёт
постоянно и не должна ждать HTTP-запроса: иначе кадр обрабатывался
бы только когда кто-то открыл страницу, и данные о простое запаздывали
бы на минуты. API лишь читает то, что конвейер уже посчитал.

Чего конвейер НЕ делает. Он не отправляет уведомления в Telegram —
это зона Gasun (Handoff, этап 8). Конвейер фиксирует событие в базе
и отдаёт его через API; дальше с ним работает бот.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.types import LineStatus, SystemStatus
from app.database import EventRepository
from app.engine import EventEngine
from app.pipeline import overlay
from app.pipeline.synthetic import SyntheticSource
from app.pipeline.tracker import PersonTracker, Track
from app.vision import PersonDetector, VisionPipeline, VisionResult
from app.vision.roi import roi_from_settings
from app.video import create_source

#: Частота кадров синтетической сцены. Совпадает с той, с какой её
#: рисует SyntheticSource, иначе сцена шла бы быстрее реального времени.
SYNTHETIC_FPS = 12.0

#: Больше этого числа кадров в секунду конвейер не берёт ни от одного
#: источника: оператору достаточно, а процессор и сеть берегуся.
MAX_PIPELINE_FPS = 15.0


@dataclass
class PipelineState:
    """Состояние конвейера для интерфейса и диагностики."""

    running: bool = False
    mode: str = "demo"
    camera_index: int = 0
    #: Текст ошибки, если источник не открылся или отвалился
    error: str = ""
    #: Человекочитаемое описание источника
    source_label: str = ""
    #: Человек сейчас в кадре
    person_present: bool = False
    #: Сколько человек найдено (а не только в ROI)
    persons_count: int = 0
    line_status: str = LineStatus.UNKNOWN
    #: Кадров обработано с момента запуска
    frames: int = 0
    #: Время последнего кадра
    last_frame_at: float = 0.0
    #: Размер кадра
    frame_size: tuple = (0, 0)
    #: Треки для интерфейса: [{id, box, trail, confidence}]
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    #: Детекция шла по YOLO или по известному боксу синтетики
    detection_source: str = "yolo"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "mode": self.mode,
            "camera_index": self.camera_index,
            "error": self.error,
            "source_label": self.source_label,
            "person_present": self.person_present,
            "persons_count": self.persons_count,
            "line_status": self.line_status,
            "frames": self.frames,
            "last_frame_at": self.last_frame_at,
            "frame_size": list(self.frame_size),
            "tracks": self.tracks,
            "detection_source": self.detection_source,
        }


class Pipeline:
    """Обрабатывает видео в фоне и отдаёт разметку кадра."""

    def __init__(
        self,
        repository: EventRepository,
        camera_id: int = 1,
        mode: Optional[str] = None,
        camera_index: int = 0,
        scenario: Optional[str] = None,
    ) -> None:
        self.repo = repository
        self.camera_id = camera_id
        self.state = PipelineState(
            mode=mode or settings.default_source,
            camera_index=camera_index,
        )

        self._lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._jpeg: bytes = b""
        self._preview_jpeg: bytes = b""
        self._preview_captured_at = 0.0
        self._preview_tracks: List[Track] = []
        self._preview_status = LineStatus.UNKNOWN
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Камеру регистрируем до первой записи: у событий и статусов стоит
        # внешний ключ, и без регистрации любая запись падала бы с
        # «FOREIGN KEY constraint failed» — конвейер при этом жил бы
        # молча, а в базе было бы пусто.
        try:
            self.repo.ensure_camera(
                self.camera_id,
                f"Камера №{self.camera_id}",
                source_type=self.state.mode,
                is_demo=self.state.mode in ("demo", "synthetic"),
            )
        except Exception:
            # База может быть недоступна на старте: тогда первая же запись
            # вернёт внятную ошибку, дублировать её здесь не нужно
            pass

        self.source = None
        self.vision: Optional[VisionPipeline] = None
        self.detector: Optional[PersonDetector] = None
        self.roi = roi_from_settings()
        self.tracker = PersonTracker()
        self.engine: Optional[EventEngine] = None
        self.scenario = scenario
        # Размер последнего обработанного кадра: нужен, чтобы перевести
        # координаты ROI в пиксели. До первого кадра неизвестен, и без
        # него проверка «человек в зоне» падала бы с исключением
        self._last_frame_shape = (0, 0)
        # Метка начала обработки текущего кадра — нужна, чтобы держать
        # реальную частоту кадров
        self._frame_started_at = 0.0

        self._last_status_write = 0.0
        #: Как часто писать снимок состояния в базу, секунд
        self.status_interval = 2.0

    # ------------------------------------------------------------------ #
    # Запуск и остановка
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Запускает конвейер в фоне. Повторный вызов игнорируется."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="promvizor-pipeline", daemon=True
            )
            self.state.running = True
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Просит конвейер завершиться и ждёт поток."""
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self.state.running = False
        self._close_source()

    def _close_source(self) -> None:
        try:
            if self.source is not None:
                self.source.close()
        except Exception:
            # Закрытие источника не должно ронять конвейер
            pass
        self.source = None

    # ------------------------------------------------------------------ #
    # Основной цикл
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.source is None:
                    if not self._open_source():
                        # Внятная причина уже записана в _open_source
                        # («RTSP_URL не задан», «камера занята»).
                        # Перетирать её здесь нельзя: оператор увидел бы
                        # «что-то сломалось» вместо причины, которую
                        # можно исправить.
                        if not self.state.error:
                            self._set_error("Не удалось открыть источник видео")
                        # Ждём и пробуем снова: камера, подключённая
                        # позже, должна подхватиться сама
                        time.sleep(2.0)
                        continue

                self._frame_started_at = time.monotonic()

                frame = self.source.get_frame()
                if frame is None:
                    self._handle_no_frame()
                    continue

                self._process(frame)
                self._pace()

            except Exception as exc:  # noqa: BLE001
                # Конвейер не должен умирать из-за одного кадра:
                # иначе демонстрация останавливалась бы навсегда
                self._set_error(f"{type(exc).__name__}: {exc}")
                self._close_source()
                time.sleep(1.0)

    def _pace(self) -> None:
        """Держит реальную частоту кадров.

        Без этого конвейер обрабатывает кадры так быстро, как успевает,
        и сцена синтетики прокручивается за секунды вместо минут: тогда
        «человек отсутствует 9 секунд» превратилось бы в «через полсекунды
        на экране», а длительность простоя в отчёте была бы неверной.

        Для живой камеры ограничение тоже полезно: не даёт грузить
        процессор, перебирая кадры каждую миллисекунду.
        """
        interval = self._frame_interval()
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._frame_started_at
        remaining = interval - elapsed
        if remaining > 0:
            # Ждать дольше секунды бессмысленно: скорее всего,
            # источник отвалился
            self._stop.wait(min(remaining, 1.0))

    def _frame_interval(self) -> float:
        """Пауза между кадрами, секунды."""
        if self.state.mode == "synthetic":
            fps = SYNTHETIC_FPS
        else:
            try:
                fps = float(self.source.fps) if self.source else 0.0
            except Exception:
                fps = 0.0
        # Камера с 30–60 fps ограничивается: оператору кадр так часто
        # не нужен, а нагрузка на процессор заметна
        fps = max(1.0, min(fps, MAX_PIPELINE_FPS))
        return 1.0 / fps

    def _open_source(self) -> bool:
        """Открывает источник по текущему режиму."""
        try:
            self.source = self._build_source()
            self.source.open()
        except Exception as exc:  # noqa: BLE001
            self.source = None
            self._set_error(self._describe_open_error(exc))
            return False

        self.roi = roi_from_settings()
        self.vision = VisionPipeline()
        self.detector = PersonDetector(roi=self.roi)
        self.tracker.reset()

        mode = self.state.mode
        # Порог подтверждения простоя зависит от режима: для веб-камеры
        # 5 секунд (требование ТЗ), для демо — меньше, чтобы за короткий
        # ролик событие успело появиться.
        threshold = self._threshold_for(mode)
        self.engine = EventEngine(
            camera_id=self.camera_id,
            threshold_seconds=threshold,
            resume_confirm_seconds=0,
            clock=lambda: 0.0,
        )
        self.state.detection_source = "synthetic" if mode == "synthetic" else "yolo"
        self.state.error = ""
        self.state.source_label = self.source.describe()
        return True

    def _build_source(self):
        mode = self.state.mode

        if mode == "webcam":
            return create_source("webcam", device_index=self.state.camera_index)

        if mode == "rtsp":
            # Сетевой камеры в Alpha нет: включать нечего, и вместо
            # картинки показывается «нет сигнала». Здесь настройка
            # проверяется, чтобы ошибка была внятной.
            if not settings.rtsp_url:
                raise RuntimeError("RTSP_URL не задан — сетевая камера недоступна")
            return create_source("rtsp", url=settings.rtsp_url)

        # Демо: настоящие ролики, если они есть, иначе синтетика
        if mode == "demo":
            try:
                return create_source("demo", scenario=self.scenario or "normal")
            except Exception:
                return SyntheticSource()

        return SyntheticSource()

    def _describe_open_error(self, exc: Exception) -> str:
        mode = self.state.mode
        if mode == "webcam":
            return (
                f"Камера №{self.state.camera_index} недоступна: {exc}. "
                "Проверьте, что она подключена и не занята другой программой."
            )
        if mode == "rtsp":
            return f"Сетевая камера недоступна: {exc}"
        return f"Источник не открыт: {exc}"

    def _threshold_for(self, mode: str) -> int:
        """Порог подтверждения простоя для режима."""
        if mode == "webcam":
            # ТЗ: в режиме веб-камеры 5 секунд — условность именно
            # этого режима, на сетевой камере и демо он другой
            return settings.downtime_threshold_webcam_seconds
        return settings.downtime_threshold_demo_seconds

    def _handle_no_frame(self) -> None:
        """Источник не дал кадр: это «нет сигнала»."""
        if self.state.mode in ("webcam", "rtsp"):
            self._set_error("Нет сигнала с камеры")
        else:
            self._set_error("Источник видео исчерпан")
        self._close_source()
        time.sleep(1.0)

    # ------------------------------------------------------------------ #
    # Обработка кадра
    # ------------------------------------------------------------------ #
    def _process(self, frame) -> None:
        detections = self._detect(frame)
        tracks = self.tracker.update(detections)

        person_present = any(self._box_in_roi(t.box) for t in tracks)

        result = self._result_for(frame, person_present)
        events = self.engine.update(result, now=time.monotonic())
        status = self.engine.status(result, now=time.monotonic())

        self._write_state(events, status)

        annotated = overlay.render_frame(
            frame.copy(),
            tracks,
            line_status=result.line_status,
            roi=self.roi,
        )
        if self.state.mode == "rtsp":
            annotated = overlay.draw_banner(annotated, "НЕТ СИГНАЛА", ok=False)

        self._publish(annotated, tracks, result)

    def _detect(self, frame) -> List[Any]:
        """Находит людей в кадре.

        Для синтетики используется известный бокс: YOLO на
        нарисованном силуэте не срабатывает, а механика (трек, путь,
        простой) должна быть видна уже сейчас. Для настоящих
        источников — только YOLO.
        """
        if self.state.mode == "synthetic" and isinstance(self.source, SyntheticSource):
            box = self.source.known_person_box()
            return [box] if box else []

        detection = self.detector.detect(frame)
        return detection.detections

    def _box_in_roi(self, box) -> bool:
        """В контролируемой ли человек зоне.

        Используется готовый contains_box: он считает ПЕРЕКРЫТИЕ рамки
        с зоной, а не центр, поэтому человек на границе не «моргает».
        Считать здесь же по-своему нельзя: расхождение с детектором
        дало бы два разных ответа на один кадр — на видео человек был
        бы виден, а в отчёте числилось бы отсутствие.

        Раньше здесь использовался to_pixels, но он возвращает
        (x, y, w, h) — левый верхний угол и РАЗМЕРЫ, а не углы
        прямоугольника. Зона получалась вдвое меньше, и присутствие
        человека не определялось никогда.
        """
        height, width = self._last_frame_shape
        if width <= 0 or height <= 0:
            return False
        return self.roi.contains_box(box[0], box[1], box[2], box[3], width, height)

    def _result_for(self, frame, person_present: bool) -> VisionResult:
        """Итог кадра: человек + состояние линии.

        По требованию ТЗ простой — это отсутствие рабочего, а не
        остановка конвейера: «если человек есть в кадре — сообщения
        нет, если человек выходит из кадра — через 5 секунд это
        фиксируется в отчёт». Поэтому в режимах, где оператор смотрит
        видео с камеры (веб-камера, синтетика), отсутствие человека
        трактуется как STOPPED — иначе простой не наступил бы никогда.
        """
        motion_result = self.vision.motion.update(frame)

        line_status = motion_result.line_status
        if self.state.mode in ("webcam", "synthetic") and not person_present:
            line_status = LineStatus.STOPPED

        return VisionResult(
            person_present=person_present,
            line_status=line_status,
            confidence=0.0,
            motion=motion_result.motion,
        )

    def _write_state(self, events, status: SystemStatus) -> None:
        """Пишет события и снимок состояния в базу."""
        for event in events:
            contract = event.to_contract(self.engine.cost_per_minute)
            try:
                self.repo.save_event(contract)
            except Exception as exc:  # noqa: BLE001
                self._set_error(f"Не удалось сохранить событие: {exc}")

        now = time.time()
        if now - self._last_status_write >= self.status_interval:
            self._last_status_write = now
            try:
                self.repo.save_status(status)
            except Exception as exc:  # noqa: BLE001
                self._set_error(f"Не удалось сохранить статус: {exc}")

    def _publish(self, annotated, tracks, result: VisionResult) -> None:
        """Сохраняет кадр с разметкой и обновляет состояние."""
        jpeg = overlay.encode_jpeg(annotated)
        with self._frame_lock:
            self._jpeg = jpeg
            self._preview_tracks = [
                Track(
                    track_id=track.track_id,
                    box=tuple(track.box),
                    confidence=track.confidence,
                    trail=deque(track.trail),
                    misses=track.misses,
                    age=track.age,
                )
                for track in tracks
            ]
            self._preview_status = result.line_status

        self.state.frames += 1
        self.state.last_frame_at = time.time()
        self.state.frame_size = (annotated.shape[1], annotated.shape[0])
        self.state.person_present = result.person_present
        self.state.persons_count = len(tracks)
        self.state.line_status = result.line_status
        self.state.tracks = [
            {
                "id": t.track_id,
                "box": list(t.box),
                "trail": [[round(p[0], 1), round(p[1], 1)] for p in list(t.trail)],
                "confidence": round(t.confidence, 3),
            }
            for t in tracks
        ]
        self._last_frame_shape = annotated.shape[:2]

    def _set_error(self, message: str) -> None:
        self.state.error = message

    # ------------------------------------------------------------------ #
    # Доступ извне
    # ------------------------------------------------------------------ #
    def latest_jpeg(self) -> bytes:
        """Свежий JPEG-кадр; веб-камера использует последние рамки треков."""
        source = self.source
        if self.state.mode == "webcam" and source is not None:
            snapshot = getattr(source, "latest_frame_snapshot", None)
            captured = snapshot() if callable(snapshot) else None
            if captured is not None:
                frame, captured_at = captured
                with self._preview_lock:
                    if captured_at != self._preview_captured_at:
                        with self._frame_lock:
                            tracks = [
                                Track(
                                    track_id=track.track_id,
                                    box=tuple(track.box),
                                    confidence=track.confidence,
                                    trail=deque(track.trail),
                                    misses=track.misses,
                                    age=track.age,
                                )
                                for track in self._preview_tracks
                            ]
                            status = self._preview_status
                        annotated = overlay.render_frame(
                            frame,
                            tracks,
                            line_status=status,
                            roi=self.roi,
                        )
                        self._preview_jpeg = overlay.encode_jpeg(annotated)
                        self._preview_captured_at = captured_at
                    return self._preview_jpeg

        with self._frame_lock:
            return self._jpeg

    def has_frame(self) -> bool:
        with self._frame_lock:
            return bool(self._jpeg)

    def wait_frame(self, timeout: float = 2.0) -> bool:
        """Ждёт появления первого кадра."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.has_frame():
                return True
            time.sleep(0.05)
        return False

    def snapshot(self) -> Dict[str, Any]:
        return self.state.to_dict()


__all__ = ["Pipeline", "PipelineState"]
