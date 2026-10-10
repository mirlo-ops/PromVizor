"""Видеопоток с камеры и переключение режима (Этап 10).

Требование ТЗ: в окне камер должно быть видно живое видео с зелёной
рамкой вокруг человека и линией пройденного пути.

Как это работает. Конвейер (app/pipeline) обрабатывает кадры в фоне
и отдаёт последний кадр в JPEG. Здесь он отдаётся в браузер как
поток MJPEG — обычная картинка, которая обновляется. Выбран MJPEG
потому, что встраивается в `<img src=...>` без единой строки
JavaScript и работает во всех браузерах.

Чего поток не делает: он не пишет в базу. Запись событий — дело
конвейера, поток только показывает.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.core import camera_mode
from app.core.config import settings
from app.database.repository import EventRepository
from app.pipeline.runner import Pipeline

# Импорт делается на уровне модуля: routes.py не знает про этот файл,
# поэтому цикла импортов не возникает.
from app.api.routes import get_repository

router = APIRouter(prefix="/api/video", tags=["видео"])

#: Разделительная строка кадра в потоке MJPEG (требование протокола)
BOUNDARY = "frame"

#: Как часто отдавать кадр, если источник не успевает. Меньше 10 Гц
#: незаметно глазу, а трафик заметно ниже.
MAX_STREAM_FPS = 10.0


# --------------------------------------------------------------------------- #
# Конвейер
# --------------------------------------------------------------------------- #
def get_pipeline(repo: EventRepository = Depends(get_repository)) -> Pipeline:
    """Отдаёт работающий конвейер, создавая его при первом обращении."""
    from app.api.runtime import get_or_create_pipeline

    return get_or_create_pipeline(repo)


# --------------------------------------------------------------------------- #
# Поток и снимок
# --------------------------------------------------------------------------- #
@router.get("/stream", summary="Живой видеопоток с камеры (MJPEG)")
async def stream(pipeline: Pipeline = Depends(get_pipeline)) -> StreamingResponse:
    """Отдаёт кадры с разметкой как поток MJPEG.

    Пока первый кадр не готов, отдаётся заглушка: иначе страница
    висела бы на пустом `<img>` до первого кадра, а оператор решил бы,
    что камера не работает.
    """
    return StreamingResponse(
        _mjpeg(pipeline),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


async def _mjpeg(pipeline: Pipeline) -> AsyncIterator[bytes]:
    """Отдаёт JPEG-кадры по одному, пока клиент подключён."""
    interval = 1.0 / MAX_STREAM_FPS
    last_sent = b""
    idle_sent = 0

    while True:
        frame = await asyncio.get_running_loop().run_in_executor(None, pipeline.latest_jpeg)

        if not frame:
            # Кадра ещё нет — говорим об этом прямо, а не молчим
            frame = await asyncio.get_running_loop().run_in_executor(
                None, _placeholder_frame, pipeline
            )
            # Заглушку не шлём каждый кадр: достаточно раз в секунду
            if idle_sent >= int(1.0 / interval):
                idle_sent = 0
            else:
                idle_sent += 1
                await asyncio.sleep(interval)
                continue
        idle_sent = 0

        # Кадр не изменился — повторно слать незачем, трафик тот же
        if frame != last_sent:
            last_sent = frame
            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                + frame + b"\r\n"
            )
        await asyncio.sleep(interval)


def _placeholder_frame(pipeline: Pipeline):
    """Заглушка на время ожидания первого кадра."""
    from app.pipeline import overlay

    import numpy as np

    frame = np.full((360, 640, 3), 38, dtype=np.uint8)
    text = pipeline.state.error or "Ожидание сигнала с камеры…"
    return overlay.draw_banner(frame, text, ok=False)


@router.get("/snapshot.jpg", summary="Один кадр (JPEG)")
async def snapshot(pipeline: Pipeline = Depends(get_pipeline)) -> JSONResponse:
    """Единичный кадр. Используется как запасной вариант и для проверок."""
    frame = pipeline.latest_jpeg()
    if not frame:
        return JSONResponse(status_code=503, content={"detail": "Кадр ещё не готов"})

    from fastapi.responses import Response

    return Response(content=frame, media_type="image/jpeg")


# --------------------------------------------------------------------------- #
# Состояние конвейера
# --------------------------------------------------------------------------- #
@router.get("/state", summary="Состояние конвейера")
def video_state(pipeline: Pipeline = Depends(get_pipeline)) -> dict:
    """Режим, источник, ошибка, число людей и треки."""
    state = pipeline.snapshot()
    state["mode_label"] = camera_mode.describe_mode(state["mode"])
    return state


# --------------------------------------------------------------------------- #
# Режим камеры
# --------------------------------------------------------------------------- #
@router.get("/mode", summary="Текущий режим камеры")
def get_mode() -> dict:
    """Что выбрано сейчас: режим, номер веб-камеры."""
    current = camera_mode.load()
    return {
        "mode": current.mode,
        "mode_label": camera_mode.describe_mode(current.mode),
        "webcam_index": current.webcam_index,
        "demo_scenario": current.demo_scenario,
        "available": list(camera_mode.ALL_MODES),
        "downtime_threshold_webcam_seconds": settings.downtime_threshold_webcam_seconds,
    }


@router.post("/mode", summary="Переключить режим камеры")
async def set_mode(
    mode: str = Query(..., description="demo | webcam | rtsp | synthetic"),
    webcam_index: int = Query(0, ge=0, le=20),
    demo_scenario: str = Query("normal"),
    repo: EventRepository = Depends(get_repository),
) -> dict:
    """Переключает режим и перезапускает конвейер.

    Перезапуск нужен, потому что смена режима означает смену источника
    видео: старый закрыть, новый открыть. Проверяем, что новый
    источник открылся, иначе оператор увидел бы пустую камеру.
    """
    if mode not in camera_mode.ALL_MODES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Неизвестный режим: {mode}. "
                f"Допустимые: {', '.join(camera_mode.ALL_MODES)}"
            ),
        )

    requested = camera_mode.CameraMode(
        mode=mode, webcam_index=webcam_index, demo_scenario=demo_scenario
    )
    applied = camera_mode.save(requested)

    from app.api.runtime import restart_pipeline

    pipeline = restart_pipeline(repo, mode=applied.mode, camera_index=applied.webcam_index)
    # Даём конвейеру секунду открыть источник: сразу после ответа
    # state ещё показывал бы состояние прежнего режима
    await asyncio.get_running_loop().run_in_executor(None, _wait_for_source, pipeline)

    return {
        "mode": applied.mode,
        "mode_label": camera_mode.describe_mode(applied.mode),
        "webcam_index": applied.webcam_index,
        "state": pipeline.snapshot(),
    }


def _wait_for_source(pipeline: Pipeline, timeout: float = 2.0) -> None:
    pipeline.wait_frame(timeout=timeout)


# --------------------------------------------------------------------------- #
# Веб-камеры
# --------------------------------------------------------------------------- #
@router.get("/cameras", summary="Список доступных веб-камер")
def list_webcams() -> dict:
    """Перечисляет камеры, подключённые к компьютеру.

    Перебираем индексы и пробуем открыть: у OpenCV нет нормального
    способа спросить список устройств. Ошибка открытия — не беда,
    камера просто не включается в список.
    """
    import cv2

    found: List[dict] = []
    for index in range(MAX_PROBE_DEVICES):
        capture = cv2.VideoCapture(index)
        try:
            if capture.isOpened():
                ok, frame = capture.read()
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                found.append(
                    {
                        "index": index,
                        "name": f"Камера №{index + 1}",
                        "width": width,
                        "height": height,
                        # Работает ли она отдаёт кадр, а не только
                        # открывается: иначе в списке будет вкладка,
                        # которая всегда молчит
                        "works": bool(ok and frame is not None),
                    }
                )
        finally:
            capture.release()

    return {
        "items": found,
        "count": len(found),
        "note": (
            "Список получен перебором устройств. Если камера не появилась — "
            "проверьте, что она подключена и не занята другой программой."
        ),
    }


#: Сколько устройств пробуем. Больше десяти смысла нет: на типовой
#: машине столько камер не бывает, а перебор занимает секунды.
MAX_PROBE_DEVICES = 10


__all__ = ["router"]