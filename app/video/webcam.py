"""WEBCAM-источник: USB-камера через cv2.VideoCapture."""

from __future__ import annotations

import threading
import time
from typing import Optional

from app.video.source import VideoSource


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

    def _capture_frames(self) -> None:
        """Постоянно читает камеру, отбрасывая устаревшие кадры."""
        while not self._capture_stop.is_set():
            cap = self._cap
            if cap is None or not cap.isOpened():
                break
            ok, frame = cap.read()
            if ok and frame is not None:
                with self._frame_lock:
                    self._latest_frame = frame
                    self._latest_frame_at = time.monotonic()
                self._first_frame.set()
            else:
                self._capture_stop.wait(0.01)

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
        self._first_frame.clear()

    def _create_capture(self, cv2):  # noqa: ANN001
        cap = cv2.VideoCapture(self.device_index)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_height)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    @property
    def duration_seconds(self) -> Optional[float]:  # type: ignore[override]
        """У живой камеры длительности нет."""
        return None

    def describe(self) -> str:
        return f"WEBCAM source (устройство #{self.device_index})"
