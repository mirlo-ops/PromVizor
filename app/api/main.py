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

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth as api_auth
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
# Вход на сайт
# --------------------------------------------------------------------------- #
#: Маршруты проверки пароля объявляются ДО включения защиты: иначе
#: страница входа сама оказалась бы за защитой и не смогла войти.
app.add_api_route(
    "/api/auth/check",
    api_auth.check_password,
    methods=["POST"],
    tags=["вход"],
    summary="Проверить логин и пароль",
)
app.add_api_route(
    "/api/auth/session",
    api_auth.session_status,
    methods=["GET"],
    tags=["вход"],
    summary="Состояние входа в этой вкладке",
)

app.middleware("http")(api_auth.guard)


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
#
# Кэширование статики. Без заголовков браузер берёт js/css из своего
# кэша и после правки продолжает выполнять СТАРЫЙ код: интерфейс
# выглядит исправленным в коде, но в браузере старая версия, и
# поломка «не чинится». Именно так выглядело «опять сломалось».
# no-cache заставляет браузер каждый раз уточнять у сервера,
# а версия в адресе файла (см. dashboard_index) делает адрес новым.
class DashboardFiles(StaticFiles):
    """StaticFiles, который не даёт браузеру держать старую вёрстку.

    Установленной версии Starlette не хватает параметра headers, поэтому
    заголовок проставляется подменой file_response — так работает любой
    ответ этого класса.
    """

    def file_response(self, full_path, stat_result=None, scope=None):  # noqa: ANN001
        response = super().file_response(full_path, stat_result=stat_result, scope=scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


#: Подключаемые файлы Dashboard: css/*.css и js/*.js из index.html
_ASSET_RE = re.compile(r'((?:src|href)=")((?!data:|https?:|//)[^"]+\.(?:css|js))(")')

#: Последняя отданная разметка и её версия — чтобы не пересобирать
#: index.html на каждый запрос.
_index_cache: tuple[Optional[str], str] = (None, "")


def _assets_version() -> str:
    """Версия статики: растёт при любой правке файлов в web/.

    Один этот параметр решает проблему «правка не применяется»: адрес
    файла меняется, и браузер физически не может отдать старый код из
    кэша. Считается по времени изменения — вручную проставлять версии
    в index.html забывают, и именно из-за этого интерфейс молча
    оставался прежним.
    """
    newest = 0.0
    try:
        for path in WEB_DIR.rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
    except OSError:
        pass
    return str(int(newest))


@app.get("/", include_in_schema=False)
def dashboard_index() -> HTMLResponse:
    """Отдаёт index.html с версией в адресах файлов.

    Регистрируется ДО монтирования StaticFiles, иначе тот перехватил
    бы "/" (он отвечает на всё, что не поймано раньше) и вернул бы
    разметку без версии — то есть ровно то, что нужно починить.
    """
    global _index_cache

    version = _assets_version()
    cached_version, cached_html = _index_cache
    if cached_version != version:
        path = WEB_DIR / "index.html"
        try:
            html = path.read_text(encoding="utf-8")
        except OSError:
            html = "<h1>Dashboard не найден</h1>"
        html = _ASSET_RE.sub(rf"\g<1>\g<2>?v={version}\g<3>", html)
        _index_cache = (version, html)
    # Разметку кэшировать нельзя: версия в адресах файлов бессмысленна,
    # если браузер покажет старую страницу со ссылками без неё. Именно
    # это и происходило — страница приходила из кэша, и правки JS
    # не доезжали до браузера совсем.
    return HTMLResponse(
        _index_cache[1], headers={"Cache-Control": "no-store, must-revalidate"}
    )


if WEB_DIR.is_dir():
    app.mount("/", DashboardFiles(directory=str(WEB_DIR), html=True), name="dashboard")
else:  # pragma: no cover — зависит от того, как развёрнут проект
    app.state.dashboard_missing = str(WEB_DIR)


if __name__ == "__main__":
    main()


__all__ = ["app", "main", "WEB_DIR"]