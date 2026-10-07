"""WEBCAM-источник: USB-камера через cv2.VideoCapture."""

from __future__ import annotations

from typing import Optional

from app.video.source import VideoSource


class WebcamSource(VideoSource):
    source_type = "webcam"
    is_demo = False

    def __init__(self, device_index: int = 0, width: int = 1280, height: int = 720) -> None:
        super().__init__()
        self.device_index = device_index
        self.requested_width = width
        self.requested_height = height

    def _create_capture(self, cv2):  # noqa: ANN001
        cap = cv2.VideoCapture(self.device_index)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_height)
        return cap

    @property
    def duration_seconds(self) -> Optional[float]:  # type: ignore[override]
        """У живой камеры длительности нет."""
        return None

    def describe(self) -> str:
        return f"WEBCAM source (устройство #{self.device_index})"
