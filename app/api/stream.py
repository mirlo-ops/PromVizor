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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core import camera_mode
from app.core.config import settings
from app.database.repository import EventRepository
from app.pipeline.runner import Pipeline
from app.video import browsercam

# Импорт делается на уровне модуля: routes.py не знает про этот файл,
# поэтому цикла импортов не возникает.
from app.api.routes import get_repository

router = APIRouter(prefix="/api/video", tags=["видео"])

#: Разделительная строка кадра в потоке MJPEG (требование протокола)
BOUNDARY = "frame"

#: Ограничиваем отдачу потока на уровне частоты обработки конвейера.
MAX_STREAM_FPS = 24.0


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
    # Обязательно кодируем в JPEG. Раньше здесь возвращался сырой
    # ndarray, а MJPEG сравнивал его с байтами и падал:
    # «truth value of an array... is ambiguous» на строке 96.
    return overlay.encode_jpeg(overlay.draw_banner(frame, text, ok=False))


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
    # Передача с браузера — отдельная история жизни источника, её
    # видно в интерфейсе: идёт поток или кадры давно перестали приходить.
    state["browser"] = browsercam.snapshot()
    return state


# --------------------------------------------------------------------------- #
# Камера из браузера
# --------------------------------------------------------------------------- #
#: Больше кадра принимать незачем: 1280x720 в JPEG — порядка 150 КБ,
#: а телефон может прислать 4 МБ. Лимит защищает сервер от случайной
#: отправки, а не ограничивает нормальную передачу.
MAX_BROWSER_FRAME_BYTES = 3 * 1024 * 1024


@router.post("/browser/frame", summary="Кадр с камеры браузера (JPEG)")
async def browser_frame(request: Request) -> JSONResponse:
    """Принимает один JPEG-кадр из браузера пользователя.

    Тело запроса — «сырой» JPEG, без multipart и без base64: так
    кадр уходит за один HTTP-запрос и не раздувается на треть.
    Обработка (декодирование) уходит в поток — иначе тяжёлый кадр
    блокировал бы цикл uvicorn и подтормаживал API.
    """
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="Пустой кадр")
    if len(payload) > MAX_BROWSER_FRAME_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Кадр слишком большой: {len(payload) // 1024} КБ. "
                f"Ограничение — {MAX_BROWSER_FRAME_BYTES // 1024 // 1024} МБ"
            ),
        )

    loop = asyncio.get_running_loop()
    accepted = await loop.run_in_executor(None, browsercam.submit_jpeg, payload)
    if not accepted:
        # Чаще всего это не JPEG: присылать нужно картинку, а не путь
        # или текст. Отвечаем 415 — по коду ответа причина понятна.
        raise HTTPException(
            status_code=415, detail="Не удалось декодировать кадр: ожидается JPEG"
        )

    return JSONResponse({"ok": True, **browsercam.snapshot()})


@router.post("/browser/stop", summary="Браузер прекратил передачу")
def browser_stop() -> dict:
    """Браузер закрыл вкладку или остановил передачу.

    Кадр забывается сразу: иначе окно камеры ещё полсекунды показывало
    бы картинку ушедшего браузера и «нет сигнала» пришло бы с задержкой.
    """
    browsercam.reset()
    return {"ok": True}


@router.get("/browser", summary="Состояние передачи с браузера")
def browser_state() -> dict:
    return browsercam.snapshot()


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
        "available": list(camera_mode.SELECTABLE_MODES),
        "downtime_threshold_webcam_seconds": settings.downtime_threshold_webcam_seconds,
    }


@router.get("/scenarios", summary="Демо-сценарии и их ролики")
def list_demo_scenarios() -> dict:
    """Список демо-сценариев: есть ли ролик и как он называется.

    Интерфейс строит по этому списку переключатель на таймлайне
    активности. Отдаём и наличие ролика, и его имя: без этого
    интерфейс предлагал бы выбрать сценарий, который нечем
    показать, и оператор видел бы пустую камеру вместо ролика.
    """
    from app.video import describe_scenarios

    items = describe_scenarios()
    return {
        "items": items,
        "available": [item["scenario"] for item in items if item["available"]],
        "current": camera_mode.load().demo_scenario,
        "labels": {
            "normal": "Всё нормально",
            "stopped": "Линия стоит, рабочий на месте",
            "person_left": "Рабочий ушёл, линия работает",
            "stopped_no_person": "Линия стоит, рабочий ушёл",
        },
    }


@router.post("/scenario", summary="Показать демо-сценарий")
async def set_demo_scenario(
    scenario: str = Query(
        ..., description="normal | stopped | person_left | stopped_no_person"
    ),
    repo: EventRepository = Depends(get_repository),
) -> dict:
    """Включает режим demo с выбранным сценарием.

    Отдельный эндпоинт, а не только параметр у /mode: переключение
    отрезка таймлайна — самое частое действие в демо, и оно должно
    быть одним щелчком. Переключение сценария внутри demo перезапускает
    конвейер: ролик другой, источник надо открыть заново.
    """
    from app.video import describe_scenarios
    from app.video.demo import DemoSource

    if scenario not in DemoSource.SCENARIO_FILES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Неизвестный сценарий: {scenario}. "
                f"Допустимые: {', '.join(DemoSource.SCENARIO_FILES)}"
            ),
        )

    available = {item["scenario"]: item for item in describe_scenarios()}
    if not available[scenario]["available"]:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Ролик сценария «{scenario}» не найден. "
                f"Ожидался файл: {available[scenario]['file']} в demo/videos/"
            ),
        )

    current = camera_mode.load()
    applied = camera_mode.save(
        camera_mode.CameraMode(
            mode=camera_mode.MODE_DEMO,
            webcam_index=current.webcam_index,
            demo_scenario=scenario,
        )
    )

    from app.api.runtime import restart_pipeline

    pipeline = restart_pipeline(
        repo,
        mode=applied.mode,
        camera_index=applied.webcam_index,
        scenario=applied.demo_scenario,
    )
    # Даём конвейеру открыть ролик: сразу после ответа state ещё
    # показывал бы прежний сценарий.
    await asyncio.get_running_loop().run_in_executor(None, _wait_for_source, pipeline)

    return {
        "scenario": applied.demo_scenario,
        "mode": applied.mode,
        "state": pipeline.snapshot(),
    }


@router.post("/mode", summary="Переключить режим камеры")
async def set_mode(
    mode: str = Query(..., description="demo | webcam | rtsp | synthetic | browser"),
    webcam_index: int = Query(0, ge=0, le=20),
    demo_scenario: str = Query("normal"),
    repo: EventRepository = Depends(get_repository),
) -> dict:
    """Переключает режим и перезапускает конвейер.

    Перезапуск нужен, потому что смена режима означает смену источника
    видео: старый закрыть, новый открыть. Проверяем, что новый
    источник открылся, иначе оператор увидел бы пустую камеру.
    """
    if mode not in camera_mode.SELECTABLE_MODES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Недоступный режим: {mode}. "
                f"Выбрать можно: {', '.join(camera_mode.SELECTABLE_MODES)}"
            ),
        )

    requested = camera_mode.CameraMode(
        mode=mode, webcam_index=webcam_index, demo_scenario=demo_scenario
    )
    applied = camera_mode.save(requested)

    from app.api.runtime import restart_pipeline

    pipeline = restart_pipeline(
        repo,
        mode=applied.mode,
        camera_index=applied.webcam_index,
        scenario=applied.demo_scenario,
    )
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
def list_webcams(pipeline: Pipeline = Depends(get_pipeline)) -> dict:
    """Перечисляет камеры, подключённые к компьютеру.

    Перебор устройств честен, но у него есть особенность: камера, которую
    уже держит конвейер, открыть повторно НЕЛЬЗЯ — она занята. Раньше
    из-за этого список настроек оказывался пуст ровно тогда, когда камера
    работала: оператор открывал «Настройки» и не видел ни одной камеры,
    хотя видео на вкладке «Камеры» шло. Поэтому камеру конвейера мы
    добавляем в список отдельно и по факту работы источника, а не по
    результату чужой попытки её открыть.

    У OpenCV нет нормального способа спросить список устройств, поэтому
    перебираются индексы. Ошибка открытия — не беда, камера просто не
    попадает в список.
    """
    import cv2

    state = pipeline.snapshot()
    active_index = state.get("camera_index", 0) if state.get("mode") == "webcam" else None
    # Источник отдаёт кадры — значит, устройство рабочее, просто занято
    # нами самими. Это единственный достоверный признак «камера жива».
    active_works = active_index is not None and pipeline.has_frame()

    found: List[dict] = []
    for index in _probe_indices():
        if index == active_index:
            found.append(
                {
                    "index": index,
                    "name": f"Камера №{index + 1}",
                    "width": 0,
                    "height": 0,
                    "works": active_works,
                    "in_use": True,
                    "note": "сейчас используется конвейером",
                }
            )
            continue

        capture = cv2.VideoCapture(index)
        try:
            if capture.isOpened():
                ok, frame = capture.read()
                found.append(
                    {
                        "index": index,
                        "name": f"Камера №{index + 1}",
                        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                        # Работает ли она отдаёт кадр, а не только
                        # открывается: иначе в списке будет вкладка,
                        # которая всегда молчит
                        "works": bool(ok and frame is not None),
                        "in_use": False,
                        "note": "",
                    }
                )
        finally:
            capture.release()

    return {
        "items": found,
        "count": len(found),
        "active_index": active_index,
        "note": (
            "Список получен перебором устройств. Камера, которую держит "
            "конвейер, помечена как используемая — её нельзя открыть "
            "второй раз, и это не поломка."
        ),
    }


def _probe_indices() -> range:
    """Индексы устройств для перебора.

    На Linux индексы соответствуют узлам /dev/videoN, поэтому берём их
    оттуда: так в список попадут только реально существующие камеры, а
    не десять несуществующих. На macOS и Windows таких узлов нет, и
    индексы перебираются подряд, как и раньше.
    """
    import glob

    indices = set()
    for path in glob.glob("/dev/video*"):
        tail = path.rsplit("video", 1)[-1]
        if tail.isdigit():
            index = int(tail)
            if index < MAX_PROBE_DEVICES:
                indices.add(index)
    if indices:
        return range(min(indices), max(indices) + 1)
    return range(MAX_PROBE_DEVICES)


#: Сколько устройств пробуем. Больше десяти смысла нет: на типовой
#: машине столько камер не бывает, а перебор занимает секунды.
MAX_PROBE_DEVICES = 10


__all__ = ["router"]
