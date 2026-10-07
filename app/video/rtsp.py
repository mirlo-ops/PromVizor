"""RTSP-источник: IP-камера по URL (RTSP_URL из .env)."""

from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.video.source import VideoSource


class RtspSource(VideoSource):
    source_type = "rtsp"
    is_demo = False

    def __init__(self, url: str = "") -> None:
        super().__init__()
        # URL берём из аргумента или из настроек (.env → RTSP_URL)
        self.url = url or settings.rtsp_url

    def _create_capture(self, cv2):  # noqa: ANN001
        if not self.url:
            raise RuntimeError(
                "RTSP URL не задан. Укажите параметр или переменную RTSP_URL в .env"
            )
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            # Небольшой буфер — чтобы анализ шёл на «свежих» кадрах
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        return cap

    @property
    def duration_seconds(self) -> Optional[float]:  # type: ignore[override]
        """У живого потока длительности нет."""
        return None

    def describe(self) -> str:
        # Не выводим полный URL (в нём могут быть креды)
        host = self.url.split("@")[-1].split("/")[0] if self.url else "(нет URL)"
        return f"RTSP source ({host})"
