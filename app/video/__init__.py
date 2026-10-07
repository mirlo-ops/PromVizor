"""Видео-подсистема «ПромВизор»: единая точка создания источников.

    from app.video import create_source, DemoSource

    with create_source("demo", scenario="downtime") as src:
        frame = src.get_frame()
"""

from __future__ import annotations

from app.core.config import DemoScenario
from app.video.demo import DemoSource, list_available_scenarios
from app.video.rtsp import RtspSource
from app.video.source import VideoSource
from app.video.webcam import WebcamSource

__all__ = [
    "VideoSource",
    "DemoSource",
    "WebcamSource",
    "RtspSource",
    "create_source",
    "list_available_scenarios",
]


def create_source(source: str, scenario: str = DemoScenario.NORMAL, **kwargs) -> VideoSource:
    """Фабрика источников по строковому типу: demo / webcam / rtsp."""
    source = (source or "").lower()

    if source == "demo":
        return DemoSource(scenario=scenario, **kwargs)
    if source == "webcam":
        return WebcamSource(**kwargs)
    if source == "rtsp":
        return RtspSource(**kwargs)

    raise ValueError(
        f"Неизвестный источник: {source!r}. Допустимые: demo, webcam, rtsp"
    )
