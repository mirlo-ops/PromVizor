"""Точка входа API «ПромВизор» (Этап 6 — FastAPI).

Запуск:

    .venv/bin/python -m app.api.main

или

    .venv/bin/uvicorn app.api.main:app --reload

Документация после запуска: http://127.0.0.1:8000/docs

Замечание про демо-данные. Пустая база — это валидное состояние:
система запустилась, но ещё не обработала ни одного кадра. Наполнять
её выдуманными событиями молча было бы неверно — Dashboard показал бы
красивые цифры, которых на заводе нет. Поэтому пример данных
создаётся только по явному запросу (`API_SEED_DEMO=true` или
`POST /api/demo/seed`), и такие данные всегда помечены.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import close_database, get_database, router
from app.core.config import settings

DESCRIPTION = """
Backend «ПромВизор» — контроль простоя линии по видео.

API отдаёт данные Dashboard, Telegram Bot и внешним интеграциям.

**Ущерб не пересчитывается на стороне API.** Значение приходит из базы
таким, как его посчитал backend по формуле
`длительность (мин) × стоимость минуты`. Клиент только отображает его.
"""


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


# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
# Dashboard лежит в `web/` и в разработке открывается как локальный
# файл (`file://`) или с другого порта. Без CORS браузер заблокирует
# запрос. В Alpha разрешаем любой источник — это удобно, но для рабочего
# сервера origins нужно задать явно через API_CORS_ORIGINS.
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
@app.get("/", tags=["служебное"], summary="Информация о сервисе")
def root() -> dict:
    """Корень: где документация и какие есть разделы."""
    return {
        "service": "ПромВизор API",
        "version": "0.1.0",
        "docs": "/docs",
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


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]