"""Жизненный цикл конвейера внутри API-процесса.

Конвейер один на процесс, а не на каждый запрос: обработка видео идёт
непрерывно, и создавать её заново на каждый HTTP-запрос означало бы
постоянно открывать и закрывать камеру.

Потокобезопасность. Обращаться к конвейеру могут несколько запросов
одновременно (снимок, состояние, переключение режима), поэтому все
операции с ним проходят под замком.
"""

from __future__ import annotations

import threading
from typing import Optional

from app.core import camera_mode
from app.pipeline.runner import Pipeline

_lock = threading.Lock()
_pipeline: Optional[Pipeline] = None


def get_or_create_pipeline(repo, camera_id: Optional[int] = None) -> Pipeline:
    """Возвращает конвейер, создавая и запуская его при необходимости."""
    global _pipeline

    with _lock:
        if _pipeline is None:
            _pipeline = _build(repo, camera_id)
            _pipeline.start()
        return _pipeline


def _build(repo, camera_id: Optional[int]) -> Pipeline:
    mode = camera_mode.load()
    from app.core.config import settings

    return Pipeline(
        repository=repo,
        camera_id=camera_id or settings.camera_id,
        mode=mode.mode,
        camera_index=mode.webcam_index,
        scenario=mode.demo_scenario,
    )


def restart_pipeline(repo, mode: str, camera_index: int = 0) -> Pipeline:
    """Перезапускает конвейер в новом режиме.

    Порядок важен: старый источник закрывается ДО создания нового.
    Иначе при переключении на ту же камеру она осталась бы занята
    предыдущим подключением и не открылась бы второй раз.
    """
    global _pipeline

    with _lock:
        if _pipeline is not None:
            _pipeline.stop()
            _pipeline = None

        from app.core.config import settings

        _pipeline = Pipeline(
            repository=repo,
            camera_id=settings.camera_id,
            mode=mode,
            camera_index=camera_index,
        )
        _pipeline.start()
        return _pipeline


def stop_pipeline() -> None:
    """Останавливает конвейер при завершении приложения."""
    global _pipeline

    with _lock:
        if _pipeline is not None:
            _pipeline.stop()
            _pipeline = None


__all__ = ["get_or_create_pipeline", "restart_pipeline", "stop_pipeline"]