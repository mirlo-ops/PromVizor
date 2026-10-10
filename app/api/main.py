"""Точка входа API «ПромВизор» (Этап 6 — FastAPI, Этап 7 — раздача Dashboard).

Запуск:

    .venv/bin/python -m app.api.main

или

    .venv/bin/uvicorn app.api.main:app --reload

После запуска доступны:

* Dashboard — http://127.0.0.1:8000/
* Документация API — http://127.0.0.1:8000/docs

Dashboard раздаётся тем же сервером, а не открывается как локальный
файл. Причина практическая: браузер запрещает `fetch()` со страницы,
открытой по `file://`, — «ПромВизор» не смог бы обратиться к API.
Через один сервер источник один, CORS не нужен вовсе и открыть
достаточно один адрес вместо двух.

Замечание про демо-данные. Пустая база — это валидное состояние:
система запустилась, но ещё не обработала ни одного кадра. Наполнять
её выдуманными событиями молча было бы неверно — Dashboard показал бы
красивые цифры, которых на заводе нет. Поэтому пример данных
создаётся только по явному запросу (`POST /api/demo/seed`), и такие
данные всегда помечены.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import close_database, get_database, router
from app.api.runtime import stop_pipeline
from app.api.stream import router as video_router
from app.core.config import settings

DESCRIPTION = """
Backend «ПромВизор» — контроль простоя линии по видео.

API отдаёт данные Dashboard, Telegram Bot и внешним интеграциям.

**Ущерб не пересчитывается на стороне API.** Значение приходит из базы
таким, как его посчитал backend по формуле
`длительность (мин) × стоимость минуты`. Клиент только отображает его.
"""

#: Где лежит Dashboard. Путь считается от корня репозитория, а не от
#: текущего рабочего каталога: запуск из другой папки иначе не нашёл бы
#: интерфейс.
WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Открывает базу при старте и закрывает при остановке.

    Без этого соединение осталось бы открытым после выключения
    процесса: незакрытая БД может надолго удерживать файл на Windows.
    """
    get_database()  # создаёт схему, если её ещё нет
    try:
        yield
    finally:
        # Конвейер закрываем до базы: он ходит в базу из своего потока,
        # и при остановке мог бы писать в уже закрытое соединение.
        stop_pipeline()
        close_database()


app = FastAPI(
    title="ПромВизор API",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    # Контракт заморожен: SystemStatus → /api/status, DowntimeEvent →
    # /api/events, Statistics → /api/statistics. Имена соответствуют
    # Handoff, раздел 16, — чтобы Gasun не пришлось ничего угадывать.
)

app.include_router(router)
app.include_router(video_router)


# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
# Dashboard раздаётся тем же сервером (см. WEB_DIR ниже), поэтому в
# штатном режиме CORS не нужен вовсе: источник один. Он остаётся на
# случай, когда Dashboard открывают отдельно — с другого порта или
# как локальный файл (тогда запросы блокирует браузер).
# В Alpha пустой список означает «любой источник»; для рабочего сервера
# origins задаётся явно через API_CORS_ORIGINS.
_origins = [origin.strip() for origin in settings.api_cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,  # вместе с "*" недопустимо
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Корневые служебные маршруты
# --------------------------------------------------------------------------- #
@app.get("/api", tags=["служебное"], summary="Информация о сервисе")
def root() -> dict:
    """Список разделов API и адрес Dashboard."""
    return {
        "service": "ПромВизор API",
        "version": "0.1.0",
        "docs": "/docs",
        "dashboard": "/" if WEB_DIR.is_dir() else None,
        "endpoints": {
            "status": "/api/status",
            "status_history": "/api/status/history",
            "events": "/api/events",
            "event": "/api/events/{id}",
            "statistics": "/api/statistics",
            "statistics_by_camera": "/api/statistics/by-camera",
            "cameras": "/api/cameras",
            "config": "/api/config",
            "health": "/api/health",
        },
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    """Запуск сервера разработки."""
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,  # при reload процесс стартует дважды и база откроется дважды
    )


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
# Монтируется последним: StaticFiles отвечает на всё, что не поймано
# раньше, и иначе перехватил бы /docs и /api.
#
# html=True отдаёт web/index.html по адресу "/". Если папки нет
# (например, при установке только backend'а), монтирование
# пропускается: API продолжит работать, просто Dashboard не откроется.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="dashboard")
else:  # pragma: no cover — зависит от того, как развёрнут проект
    app.state.dashboard_missing = str(WEB_DIR)


if __name__ == "__main__":
    main()


__all__ = ["app", "main", "WEB_DIR"]