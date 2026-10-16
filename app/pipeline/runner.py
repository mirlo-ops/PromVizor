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

Чего конвейер НЕ делает. Он не отправляет уведомления в Telegram.
Отправкой занимается отдельный процесс (app.telegram), и это сознательное
разделение: конвейер не должен зависеть из-за сети Telegram, а бот —
знать что-либо про видео. Конвейер фиксирует событие в базе и отдаёт
его через API; дальше с ним работает бот.

Ответственность за Telegram теперь наша (раньше она была отдана Gasun),
но граница между процессами остаётся: конвейер по-прежнему не ходит в
Telegram сам.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core import camera_mode
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
from app.video.browsercam import BrowserSource

#: Частота кадров синтетической сцены. Совпадает с той, с какой её
#: рисует SyntheticSource, иначе сцена шла бы быстрее реального времени.
SYNTHETIC_FPS = 12.0

#: Больше этого числа кадров в секунду конвейер не берёт ни от одного
#: источника: оператору достаточно, а процессор и сеть берегуся.
MAX_PIPELINE_FPS = 15.0

#: Как часто конвейер опрашивает источник «Камера браузера». Кадр
#: приходит по сети в случайный момент, поэтому опрашивать надо чаще,
#: чем кадры приходят. Сами опросы бесплатны: повторяющиеся кадры
#: отбрасываются до YOLO.
BROWSER_POLL_FPS = 15.0


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
        # Порог записи простоя в историю. Короткие остановки (дребезг
        # камеры, дрожание границы «стоит/работает») в базу не идут.
        #
        # В ДЕМО-режиме порог не действует: там остановка линии
        # задана сценарием и показывать её нужно любой длительности,
        # иначе демонстрация перестаёт показывать простой вовсе.
        self.min_record_seconds = (
            0.0
            if self.state.mode in ("demo", "synthetic")
            else float(settings.downtime_min_record_seconds or 0)
        )

        # --- скорость детекции человека ---
        #: Как часто запускать YOLO: 1 — на каждом кадре (медленно,
        #: но точно), 3 — раз в три кадра. См. _detect_sparse.
        self.detect_every = int(getattr(settings, "detect_every_frames", 1) or 1)
        #: Прошлый результат детекции — переиспользуется между
        #: запусками YOLO, чтобы трек продолжался без новой детекции.
        self._last_detections: Optional[List[Any]] = None
        #: Сколько кадров прошло с последнего запуска YOLO
        self._frames_since_detect = 0

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

    def stop(self, timeout: float = 2.0) -> bool:
        """Просит конвейер завершиться и ждёт поток.

        Возвращает False, если обработчик не успел завершиться за timeout.
        В этом случае не закрываем источник из-под работающего потока:
        вызывающий код может сообщить о таймауте, а не запускать второй
        конвейер поверх первого.
        """
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        if thread and thread.is_alive():
            return False
        self._thread = None
        self.state.running = False
        self._close_source()
        with self._frame_lock:
            self._jpeg = b""
            self._preview_tracks = []
            self._preview_status = LineStatus.UNKNOWN
        with self._preview_lock:
            self._preview_jpeg = b""
            self._preview_captured_at = 0.0
        return True

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
        try:
            self._run_loop()
        finally:
            # Database хранит SQLite-соединения отдельно для каждого
            # потока. Закрывать соединение pipeline из API-потока нельзя
            # (check_same_thread), поэтому освобождаем его здесь, в том
            # же потоке, где оно было создано.
            self.repo.db.close_current_thread()

    def _run_loop(self) -> None:
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

                # Браузерная камера отдаёт последний кадр, пока браузер
                # на связи, — в том числе когда нового кадра ещё нет.
                # Считать тот же кадр заново незачем: YOLO дорог, а
                # результат не изменится. Пропускаем обработку, но не
                # публикацию: в окне остаётся последняя картинка, и
                # «зависшего» кадра оператор не видит.
                if self._frame_already_seen():
                    self._pace()
                    continue

                self._process(frame)
                self._pace()

            except Exception as exc:  # noqa: BLE001
                # Конвейер не должен умирать из-за одного кадра:
                # иначе демонстрация останавливалась бы навсегда
                self._set_error(f"{type(exc).__name__}: {exc}")
                self._close_source()
                time.sleep(1.0)

    def _frame_already_seen(self) -> bool:
        """Этот кадр уже обработан — True, когда обрабатывать нечего.

        Обычные источники всегда отдают следующий кадр, поэтому вопрос
        к ним не задаётся. Вопрос есть только у браузерной камеры: она
        отдаёт последний кадр, пока браузер на связи, и при медленной
        передаче один и тот же кадр приходит снова и снова.
        """
        getter = getattr(self.source, "frame_token", None)
        if not callable(getter):
            return False
        check = getattr(self.source, "already_processed", None)
        if not callable(check):
            return False
        try:
            return bool(check(getter()))
        except Exception:
            # Сбой проверки не должен останавливать конвейер: хуже
            # лишний просчёт, чем чёрное окно
            return False

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
        elif self.state.mode == camera_mode.MODE_BROWSER:
            # Браузерную камеру опрашиваем часто, а не по её частоте.
            # Кадр приходит из сети в произвольный момент, и при
            # опросе раз в секунду он ждал бы до секунды перед
            # обработкой — картинка в окне обновлялась бы рывками.
            # Сами опросы почти ничего не стоят: повторяющиеся кадры
            # отбрасываются в _frame_already_seen, до YOLO дело не
            # доходит.
            return 1.0 / BROWSER_POLL_FPS
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
        source = None
        try:
            source = self._build_source()
            source.open()
            self.source = source
        except Exception as exc:  # noqa: BLE001
            # Демо без роликов — не поломка, а отсутствие материала.
            # Раньше откат стоял вокруг create_source(), который
            # наличие файлов не проверяет, и синтетика не включалась
            # никогда: конвейер без конца показывал «DEMO-видео ещё
            # не добавлены» вместо работающей сцены.
            if self.state.mode == camera_mode.MODE_DEMO:
                try:
                    source.close()
                except Exception:
                    pass
                source = SyntheticSource()
                try:
                    source.open()
                    self.source = source
                except Exception as fallback_exc:  # noqa: BLE001
                    self.source = None
                    self._set_error(self._describe_open_error(fallback_exc))
                    return False
            else:
                self.source = None
                # Что именно пробовали открыть — без этого сообщение
                # «камера занята» одинаково выглядит и для занятой камеры,
                # и для неверного backend'а, и разобраться нечем.
                tried = getattr(source, "tried_backends", None)
                detail = ""
                if tried:
                    detail = " Пробовали: " + "; ".join(tried) + "."
                self._set_error(self._describe_open_error(exc) + detail)
                if source is not None:
                    try:
                        source.close()
                    except Exception:
                        pass
                return False

        self.roi = roi_from_settings()
        self.vision = VisionPipeline()
        self.detector = PersonDetector(roi=self.roi)
        self.tracker.reset()
        # Кэш детекций принадлежал прежнему источнику: у другого
        # разрешения и другой сцены прошлые боксы бессмысленны, и на
        # первом же кадре человек «появился бы» в чужом месте.
        self._last_detections = None
        self._frames_since_detect = 0

        mode = self.state.mode
        # Порог подтверждения простоя зависит от режима: для веб-камеры
        # 5 секунд (требование ТЗ), для демо — меньше, чтобы за короткий
        # ролик событие успело появиться.
        threshold = self._threshold_for(mode)
        self.engine = EventEngine(
            camera_id=self.camera_id,
            threshold_seconds=threshold,
            resume_confirm_seconds=0,
            # Таймер идёт по time.monotonic(), а «ЧЧ:ММ» в событии —
            # по стенным часам. Раньше подпись clock=lambda: 0.0
            # делала метки событий нулевыми, а format_clock() рисовал
            # из них время: на сервере событие в 23:38 записывалось
            # как «00:00». Монотонность таймера нужна, подмена часов —
            # нет.
        )
        self.state.detection_source = "synthetic" if mode == "synthetic" else "yolo"
        self.state.error = ""
        self.state.source_label = self.source.describe()
        return True

    def _build_source(self):
        mode = self.state.mode

        if mode == camera_mode.MODE_BROWSER:
            # Кадры приходят из браузера пользователя (POST на
            # /api/video/browser/frame). Источник открывается всегда:
            # пока браузер не подключился, он просто не отдаёт кадр.
            return BrowserSource()

        if mode == "webcam":
            return create_source("webcam", device_index=self.state.camera_index)

        if mode == "rtsp":
            # Сетевой камеры в Alpha нет: включать нечего, и вместо
            # картинки показывается «нет сигнала». Здесь настройка
            # проверяется, чтобы ошибка была внятной.
            if not settings.rtsp_url:
                raise RuntimeError("RTSP_URL не задан — сетевая камера недоступна")
            return create_source("rtsp", url=settings.rtsp_url)

        # Демо: настоящие ролики, если они есть, иначе синтетика.
        # Отсутствие роликов проверяется при open(), а не здесь,
        # поэтому запасной вариант подставляет _open_source.
        if mode == "demo":
            return create_source("demo", scenario=self.scenario or "normal")

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
        if mode in ("webcam", camera_mode.MODE_BROWSER):
            # ТЗ: в режиме веб-камеры 5 секунд — условность именно
            # этого режима, на сетевой камере и демо он другой.
            # Камера из браузера — тот же случай: человек ушёл из
            # кадра, всё считает сервер.
            return settings.downtime_threshold_webcam_seconds
        return settings.downtime_threshold_demo_seconds

    def _handle_no_frame(self) -> None:
        """Источник не дал кадр: это «нет сигнала»."""
        if self.state.mode == camera_mode.MODE_BROWSER:
            # Источник НЕ закрывается: браузер может вернуться через
            # минуту (переключили вкладку, разрешили камеру позже), и
            # закрытие обнулило бы трекер и секундомер простоя.
            self._set_error(
                "Нет сигнала с браузера: кадры не передаются. "
                "Разрешите доступ к камере на этой странице."
            )
            time.sleep(0.5)
            return
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
        # Кадр пошёл — значит сигнал есть, и прежнее «нет сигнала» больше
        # неправда. Раньше ошибка жила до переключения режима: браузер
        # подключался через секунду после запроса, а надпись «нет сигнала
        # с браузера» оставалась на экране навсегда, хотя видео шло.
        self._clear_transient_error()

        browser = self.state.mode == camera_mode.MODE_BROWSER

        if browser:
            # Кадр показываем СРАЗУ, не дожидаясь YOLO.
            #
            # Почему. Браузер шлёт кадры 3–5 раз в секунду, а
            # распознавание человека на сервере занимает от долей секунды
            # до двух. Публикация стояла ПОСЛЕ распознавания, поэтому
            # окно обновлялось раз в секунду-две: живая камера выглядела
            # замершей на одном кадре, и оператор считал, что видео не
            # идёт. Именно это и было сказано: «видео не идёт, стоит на
            # первом кадре».
            #
            # Теперь окно обновляется с той же частотой, с какой
            # приходят кадры. Рамки и статус отстают на одно
            # распознавание — на глаз это незаметно, зато картинка живая.
            self._publish_now(frame)

        detections = self._detect_sparse(frame)
        tracks = self.tracker.update(detections)

        person_present = any(self._box_in_roi(t.box) for t in tracks)

        result = self._result_for(frame, person_present)
        events = self.engine.update(result, now=time.monotonic())
        status = self.engine.status(result, now=time.monotonic())

        self._write_state(events, status)

        if browser:
            # Кадр уже показан. Обновляем только разметку для следующего.
            with self._frame_lock:
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
            return

        annotated = overlay.render_frame(
            frame.copy(),
            tracks,
            line_status=result.line_status,
            roi=self.roi,
        )
        if self.state.mode == "rtsp":
            annotated = overlay.draw_banner(annotated, "НЕТ СИГНАЛА", ok=False)

        # Кадры идут, но их не видно (чёрные): без полосы оператор видел бы
        # просто чёрный прямоугольник и гадал, в чём дело.
        problem = getattr(self.source, "problem", "") if self.source else ""
        if problem:
            annotated = overlay.draw_banner(annotated, "НЕТ СИГНАЛА", ok=False)
            self._set_error(problem)

        self._publish(annotated, tracks, result)

    def _publish_now(self, frame) -> None:
        """Показывает кадр с разметкой от прошлого распознавания.

        Вызывается до YOLO, чтобы окно жило. Дорогая часть конвейера
        (детектор и трекер) в это время ещё считает прошлый кадр.
        """
        status = self._preview_status
        annotated = overlay.render_frame(
            frame.copy(),
            self._preview_tracks,
            line_status=status,
            roi=self.roi,
        )
        problem = getattr(self.source, "problem", "") if self.source else ""
        if problem:
            annotated = overlay.draw_banner(annotated, "НЕТ СИГНАЛА", ok=False)

        # _publish ждёт ИТОГ КАДРА, а не строку статуса. Подставляем
        # результат, собранный из прошлого распознавания: публикуемый
        # кадр один и тот же, различается только источник данных о
        # состоянии линии.
        result = VisionResult(
            person_present=self.state.person_present,
            line_status=status,
            confidence=0.0,
            motion=0.0,
        )
        self._publish(annotated, self._preview_tracks, result)

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

    # ------------------------------------------------------------------ #
    # Скорость: детекция человека не каждый кадр
    # ------------------------------------------------------------------ #
    def _detect_sparse(self, frame) -> List[Any]:
        """Детектирует человека через DETECT_EVERY кадров, между ними
        отдаёт прошлый результат.

        Почему так. YOLO — единственная дорогая операция в конвейере:
        на сервере yolo26n @ 960 даёт около секунды на кадр, а сам
        детектор движения (сравнение соседних кадров) стоит единицы
        миллисекунд. Из-за одного YOLO окно камеры шло примерно один
        кадр в секунду — картинка дёргалась, и складывалось впечатление,
        будто система «тормозит».

        Почему это не вредит смыслу. Простой считается по стенным
        часам, а не по числу кадров, поэтому пропуск обнаружения не
        растягивает таймер: человек ушёл — простой пойдёт по часам
        точно так же. Детектор движения, который ловит саму
        остановку линии, продолжает работать на КАЖДОМ кадре.

        Что действительно меняется: между детекциями рамка человека
        берётся из прошлого результата и двигается по треку, а не
        по новому. Для Alpha это приемлемая цена за плавную картинку.
        """
        if self.state.mode == "synthetic":
            # Синтетика знает свой бокс без YOLO — там считать нечего.
            return self._detect(frame)

        every = max(1, int(self.detect_every))
        self._frames_since_detect += 1

        if self._last_detections is not None and self._frames_since_detect < every:
            return self._last_detections

        detections = self._detect(frame)
        self._last_detections = detections
        self._frames_since_detect = 0
        return detections

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
        if self.state.mode in ("webcam", "synthetic", camera_mode.MODE_BROWSER) and not person_present:
            line_status = LineStatus.STOPPED

        # --- Сценарии 2 и 4: линия стоит по сценарию ---
        #
        # В роликах «линия остановилась» и «линия стоит + рабочий ушёл»
        # конвейер нарисован, но НЕ ДВИГАЕТСЯ: между кадрами картинка
        # не меняется, и детектор движения честно видит «изменений нет».
        # Значит он правильно отвечает WORKING, а по смыслу сценария
        # линия стоит. На демонстрации это выглядит как ошибка системы:
        # на экране «линия остановилась», а в отчёте WORKING.
        #
        # Считать остановку по движению конвейера здесь нельзя —
        # конвейера в кадре нет, движение создаёт сам рабочий, а он в
        # этих сценариях как раз уходит. Поэтому для ДЕМО-сценариев
        # состояние линии задаётся явно: так оно и задумано роликом.
        #
        # На настоящую камеру это не распространяется: там движение
        # настоящее, и решает детектор.
        forced = self._forced_line_status()
        if forced is not None:
            line_status = forced

        return VisionResult(
            person_present=person_present,
            line_status=line_status,
            confidence=0.0,
            motion=motion_result.motion,
        )

    #: Сценарии, где линия стоит по замыслу ролика. Ключи — имена
    #: сценариев из app/core/config.py.
    SCENARIOS_LINE_STOPPED = ("stopped", "stopped_no_person")

    def _forced_line_status(self) -> Optional[LineStatus]:
        """Состояние линии, заданное сценарием. None — решает детектор.

        Работает только в демо-режиме: настоящая камера и камера из
        браузера определяются движением, и подменять им значение
        означало бы врать оператору.
        """
        if self.state.mode != camera_mode.MODE_DEMO:
            return None
        if (self.scenario or "") not in self.SCENARIOS_LINE_STOPPED:
            return None
        return LineStatus.STOPPED

    def _write_state(self, events, status: SystemStatus) -> None:
        """Пишет события и снимок состояния в базу."""
        for event in events:
            # Микропауза в историю не идёт. Открытая запись о начале
            # простоя к этому моменту уже есть, поэтому её снимаем:
            # иначе каждая короткая остановка камеры оставляла бы
            # «вечно открытый» простой, а их за сутки набирались
            # тысячи — вкладка «Линии» их подгружала и подвисала.
            if not event.is_started and event.duration_seconds < self.min_record_seconds:
                try:
                    self.repo.discard_open_event(event.camera_id)
                except Exception as exc:  # noqa: BLE001
                    self._set_error(f"Не удалось убрать микропростой: {exc}")
                continue

            contract = event.to_contract(self.engine.cost_per_minute)
            try:
                self.repo.record_event(contract)
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

    #: Ошибки, которые снимаются первым же успешным кадром: они
    #: описывают ОТСУТСТВИЕ сигнала, а не поломку конвейера.
    TRANSIENT_ERROR_PREFIXES = (
        "Нет сигнала",
        "Браузер не передаёт",
        "Передача с браузера прервалась",
    )

    def _clear_transient_error(self) -> None:
        error = self.state.error
        if error and error.startswith(self.TRANSIENT_ERROR_PREFIXES):
            self.state.error = ""

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
