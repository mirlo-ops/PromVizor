"""WEBCAM-источник: USB-камера через cv2.VideoCapture.

Что здесь важно знать при разборе «камера не показывает видео».

Две разные поломки выглядят одинаково — чёрный прямоугольник, — но
причины у них разные, и лечатся по-разному:

1. **Камера не открылась вообще.** Так ведёт себя камера, занятая
   другой программой (браузер, OBS, Zoom). Лечится освобождением
   устройства, в коде — ничего.

2. **Камера открылась, но отдаёт чёрные кадры.** Это поведение macOS,
   когда процессу не выдано разрешение на доступ к камере (TCC):
   AVFoundation отдаёт кадры без ошибки, но они полностью чёрные.
   Раньше это доходило до оператора как просто чёрный экран без
   объяснения. Теперь источник это распознаёт и говорит причину —
   см. `problem` и `BLACK_FRAME_NOTICE`.

Поэтому источник открывает камеру через ЯВНЫЙ backend платформы
(V4L2 / AVFoundation / DSHOW), а не через «какой-нибудь подходящий»:
OpenCV 5 на macOS не всегда выбирает AVFoundation сам, и камера
молча не открывается.
"""

from __future__ import annotations

import platform
import threading
import time
from typing import List, Optional

from app.video.source import VideoSource


def _backend_candidates(cv2) -> List[int]:  # noqa: ANN001 - модуль cv2
    """Backend'ы, которые пробуем открыть камеру, по убыванию вероятности.

    Раньше был только `cv2.VideoCapture(index)` без backend'а: тогда
    выбор оставался за OpenCV, и на macOS камера открывалась через
    GStreamer, которого в обычной сборке нет, — то есть не открывалась
    вообще. Явный backend убирает эту зависимость от того, что OpenCV
    догадался выбрать.
    """
    system = platform.system()
    if system == "Darwin":
        native = getattr(cv2, "CAP_AVFOUNDATION", None)
    elif system == "Windows":
        native = getattr(cv2, "CAP_DSHOW", None)
    else:
        native = getattr(cv2, "CAP_V4L2", None)

    candidates = []
    if native is not None:
        candidates.append(int(native))
    candidates.append(int(cv2.CAP_ANY))
    return candidates


def _backend_label(cv2, backend: int) -> str:  # noqa: ANN001
    """Человеческое имя backend'а для сообщения об ошибке."""
    for name in (
        "CAP_V4L2",
        "CAP_AVFOUNDATION",
        "CAP_DSHOW",
        "CAP_GSTREAMER",
        "CAP_ANY",
    ):
        if int(getattr(cv2, name, -1)) == int(backend):
            return name.replace("CAP_", "")
    return str(backend)


#: Кадр считается чёрным, если в нём нет ни одного яркого пикселя и он
#: не содержит шума. Настоящая тёмная сцена всегда даёт шум (шум
#: матрицы) — у неё std больше нуля, поэтому порог заведомо
#: консервативен и тёмную комнату не сочтёт поломкой.
BLACK_LEVEL = 8
BLACK_SPREAD = 0.5

#: Сколько секунд непрерывной черноты считать поломкой, а не первыми
#: кадрами после включения камеры (у многих камер первые кадры темнее).
BLACK_STREAK_SECONDS = 2.5

#: Что сказать оператору, когда кадры идут, но они чёрные.
BLACK_FRAME_NOTICE = (
    "Камера отдаёт чёрные кадры. Обычно это запрет доступа к камере: "
    "в macOS — «Системные настройки → Конфиденциальность и безопасность → "
    "Камера», в Linux — membership в группе video."
)


class WebcamSource(VideoSource):
    source_type = "webcam"
    is_demo = False

    def __init__(self, device_index: int = 0, width: int = 640, height: int = 480) -> None:
        super().__init__()
        self.device_index = device_index
        self.requested_width = width
        self.requested_height = height
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_frame_at = 0.0
        self._first_frame = threading.Event()
        self._reported_fps = 30.0
        self._capture_stop = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        #: Имя backend'а, которым реально открылась камера
        self.backend_name = ""
        #: Открытые backend'ы, которые не сработали — для внятной ошибки
        self.tried_backends: List[str] = []
        self._black_since: float = 0.0
        self._problem = ""

    def open(self) -> "WebcamSource":
        """Открывает камеру и запускает отдельный поток захвата кадров."""
        super().open()
        if self._capture_thread and self._capture_thread.is_alive():
            return self

        import cv2

        reported = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._reported_fps = reported if 1.0 <= reported <= 120.0 else 30.0

        self._capture_stop.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_frames,
            name=f"promvizor-webcam-{self.device_index}",
            daemon=True,
        )
        self._capture_thread.start()
        return self

    @property
    def problem(self) -> str:
        """Причина, по которой кадры идут, но их не видно. Пусто — всё в порядке."""
        with self._frame_lock:
            return self._problem

    def _capture_frames(self) -> None:
        """Постоянно читает камеру, отбрасывая устаревшие кадры."""
        while not self._capture_stop.is_set():
            cap = self._cap
            if cap is None or not cap.isOpened():
                break
            ok, frame = cap.read()
            if ok and frame is not None:
                self._note_frame(frame)
                with self._frame_lock:
                    self._latest_frame = frame
                    self._latest_frame_at = time.monotonic()
                self._first_frame.set()
            else:
                self._capture_stop.wait(0.01)

    def _note_frame(self, frame) -> None:
        """Следит за чёрными кадрами: они выглядят как «камера молчит».

        Кадры при этом ИДУТ, поэтому конвейер считает, что с камерой всё
        хорошо, а оператор видит чёрный экран без причины. Раз кадры
        продолжают приходить, рвать источник нельзя — только объяснить,
        что именно с ним не так.
        """
        import cv2
        import numpy as np

        # Полностью чёрный кадр: нет ни ярких пикселей, ни шума матрицы
        is_black = (
            float(np.max(frame)) < BLACK_LEVEL and float(np.std(frame)) < BLACK_SPREAD
        )
        now = time.monotonic()
        with self._frame_lock:
            if not is_black:
                self._black_since = 0.0
                self._problem = ""
                return
            if self._black_since == 0.0:
                self._black_since = now
                return
            if now - self._black_since >= BLACK_STREAK_SECONDS:
                self._problem = BLACK_FRAME_NOTICE

    def get_frame(self):
        """Возвращает самый свежий кадр, не ожидая чтения устройства."""
        if not self._first_frame.is_set() and not self._capture_stop.is_set():
            self._first_frame.wait(timeout=2.0)
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            if time.monotonic() - self._latest_frame_at > 1.5:
                return None
            return self._latest_frame.copy()

    def latest_frame(self):
        """Копия последнего кадра для живого предпросмотра."""
        snapshot = self.latest_frame_snapshot()
        return snapshot[0] if snapshot else None

    def latest_frame_snapshot(self):
        """Кадр и время захвата для кэширования его JPEG-представления."""
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            if time.monotonic() - self._latest_frame_at > 1.5:
                return None
            return self._latest_frame.copy(), self._latest_frame_at

    @property
    def fps(self) -> float:
        """Частота камеры; некоторые драйверы возвращают 0 несмотря на видео."""
        return self._reported_fps

    def close(self) -> None:
        self._capture_stop.set()
        thread = self._capture_thread
        cap = self._cap
        # release() прерывает блокирующее read() у большинства backend'ов.
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        self._capture_thread = None
        self._cap = None
        with self._frame_lock:
            self._latest_frame = None
            self._latest_frame_at = 0.0
            self._black_since = 0.0
            self._problem = ""
        self._first_frame.clear()

    def _create_capture(self, cv2):  # noqa: ANN001
        """Открывает камеру, перебирая backend'ы платформы.

        Возвращает первый открывшийся capture. Если не открылся ни один —
        возвращает последний (неоткрытый), чтобы базовый класс поднял
        свою понятную ошибку, а в `self.tried_backends` осталось видно,
        что именно пробовали.
        """
        self.tried_backends = []
        capture = None
        for backend in _backend_candidates(cv2):
            try:
                candidate = cv2.VideoCapture(self.device_index, backend)
            except Exception as exc:  # noqa: BLE001
                self.tried_backends.append(f"{_backend_label(cv2, backend)}: {exc}")
                continue

            if candidate is not None and candidate.isOpened():
                self.backend_name = candidate.getBackendName() or _backend_label(
                    cv2, backend
                )
                capture = candidate
                break

            label = _backend_label(cv2, backend)
            if candidate is not None:
                try:
                    candidate.release()
                except Exception:
                    pass
            self.tried_backends.append(f"{label}: не открылась")
            capture = candidate

        if capture is not None and capture.isOpened():
            self._apply_settings(cv2, capture)
        return capture

    def _apply_settings(self, cv2, capture) -> None:  # noqa: ANN001
        """Задаёт разрешение и частоту. Ошибки здесь не должны ронять камеру.

        Не все backend'ы (особенно AVFoundation на macOS) умеют менять
        эти параметры на лету и бросают исключение вместо того, чтобы
        просто проигнорировать запрос. Раньше такое исключение
        поднималось из open(), и камера, которая уже отдавала кадры,
        объявлялась «недоступной».
        """
        for prop, value in (
            (cv2.CAP_PROP_FRAME_WIDTH, self.requested_width),
            (cv2.CAP_PROP_FRAME_HEIGHT, self.requested_height),
            (cv2.CAP_PROP_FPS, 30),
            (cv2.CAP_PROP_BUFFERSIZE, 1),
        ):
            try:
                capture.set(prop, value)
            except Exception:  # noqa: BLE001, PERF203
                continue

    @property
    def duration_seconds(self) -> Optional[float]:  # type: ignore[override]
        """У живой камеры длительности нет."""
        return None

    def describe(self) -> str:
        backend = f" через {self.backend_name}" if self.backend_name else ""
        return f"WEBCAM source (устройство #{self.device_index}{backend})"
