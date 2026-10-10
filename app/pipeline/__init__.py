"""Конвейер обработки видео «ПромВизор».

Соединяет модули, которые до этого существовали по отдельности:
видео (app/video), детекторы (app/vision), движок событий (app/engine)
и базу (app/database).

    from app.pipeline import Pipeline

    pipeline = Pipeline(repository, camera_id=1, mode="synthetic")
    pipeline.start()
    ...
    pipeline.stop()

Состав:

* `runner.py`    — цикл: кадр → детекция → события → база → разметка;
* `tracker.py`   — сопоставление людей между кадрами, история пути;
* `overlay.py`   — отрисовка рамки, номера человека и пути;
* `synthetic.py` — сцена без DEMO-роликов (витрина механики).

Отправка в Telegram здесь НЕ делается: это зона Gasun (Handoff,
этап 8). Конвейер фиксирует событие в базе и отдаёт его через API.
"""

from __future__ import annotations

from app.pipeline import overlay
from app.pipeline.overlay import encode_jpeg, render_frame
from app.pipeline.runner import Pipeline, PipelineState
from app.pipeline.synthetic import SyntheticSource
from app.pipeline.tracker import PersonTracker, Track

__all__ = [
    "Pipeline",
    "PipelineState",
    "PersonTracker",
    "Track",
    "SyntheticSource",
    "overlay",
    "render_frame",
    "encode_jpeg",
]