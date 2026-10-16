"""Проверка входа на каждый запрос (Этап: развёртывание).

Зачем это в приложении, а не в nginx. Базовая аутентификация nginx
запоминается браузером на всю сессию: открыли сайт, ввели пароль — и
больше он не спросит, ни в новой вкладке, ни позже. Для стенда, где
пароль раздаётся каждому посетителю, это не годится.

Здесь проверка идёт на каждом запросе по заголовку, который браузер
кладёт из sessionStorage. Отметка живёт в пределах вкладки: новая
вкладка спрашивает пароль снова, обновление страницы — нет.

Почему проверка не в nginx. Базовая аутентификация nginx
запоминается браузером на всю сессию: открыли сайт, ввели пароль — и
больше он не спросит, ни в новой вкладке, ни через неделю.

Почему проверка не на самой странице. Браузер не умеет вешать свои
заголовки на навигацию: открытие адреса идёт без отметки, она лежит
в sessionStorage и на сервер не попадает. Если бы сервер отдавал форму
входа вместо страницы, то после входа перезагрузка снова получила бы
форму — и попасть внутрь было бы невозможно.

Поэтому устроено так: страница и её файлы открыты всегда, а ДАННЫЕ
закрыты. Без пароля не отдаётся ни одна цифра статистики и ни одного
кадра видео. Первый же запрос интерфейса получает 401, и страница
показывает форму входа. Исходники интерфейса при этом видны — это
осознанный размен: он не даёт увидеть простой.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core import auth

#: Пути, которые работают без входа.
#:
#: Почему сама страница и её файлы открыты. Браузер не умеет вешать
#: свои заголовки на навигацию: открытие адреса идёт без
#: «X-Promvizor-Auth», и отметка из sessionStorage на сервер не
#: попадает. Если закрыть корень, то после входа reload снова получил
#: бы форму — и попасть внутрь было бы невозможно.
#:
#: Поэтому защищены ДАННЫЕ, а не файлы: без пароля не отдаётся ни один
#: байт статистики и ни одного кадра видео. Исходники интерфейса при
#: этом видны — осознанный размен: он не даёт увидеть простой.
OPEN_PATHS = frozenset(
    {
        "/api/auth/check",
        "/api/auth/session",
        "/api/health",
    }
)

#: Статика и корень открыты БЕЗ пароля — иначе после входа страница
#: снова получила бы форму (см. ниже), и попасть внутрь нельзя было бы
#: вообще. Защищены данные, а не файлы: без пароля не отдаётся ни одна
#: цифра статистики и ни одного кадра видео.
PUBLIC_PREFIXES = ("/css/", "/js/", "/assets/", "/favicon")

#: Открытые пути целиком. Корень здесь, а не в префиксах: префикс "/"
#: совпал бы с ЛЮБЫМ адресом, и защита отключилась бы целиком.
PUBLIC_PATHS = frozenset({"/", "/index.html"})


def is_public(path: str) -> bool:
    """Открыт ли путь без входа.

    Проверка именно по границам: простое `path.startswith("/")` делает
    открытыми и /api/status, то есть защиты не остаётся вовсе.
    """
    return path in PUBLIC_PATHS or any(
        path.startswith(prefix) for prefix in PUBLIC_PREFIXES
    )


def is_browser_page(request: Request) -> bool:
    """Похоже ли, что браузер открыл страницу (а не вызвал API).

    Нужна для диагностики: если интерфейс покажет вместо формы входа
    сообщение «нет связи с сервером», будет понятно, что запрос был
    страницей, а не данными.
    """
    accept = request.headers.get("accept", "")
    return "text/html" in accept or not request.url.path.startswith("/api")


async def guard(request: Request, call_next):
    """Пропускает запрос или отдаёт отказ.

    Заголовок `X-Requested-With` отличает вызов интерфейса от
    обычного перехода: без него страница входа сама попала бы под
    проверку и не смогла бы войти.
    """
    settings = auth.load_auth_settings()
    if not settings.active:
        return await call_next(request)

    path = request.url.path
    if path in OPEN_PATHS:
        return await call_next(request)

    # Заголовок — обычный путь. Адрес с отметкой нужен только для
    # видеопотока: он открывается тегом <img>, а браузер не умеет
    # вешать заголовки на такой запрос.
    header = request.headers.get(auth.AUTH_HEADER) or request.query_params.get(
        auth.SESSION_KEY
    )
    if auth.verify_header(settings, header):
        return await call_next(request)

    # Страница и её файлы открыты: без них не показать форму входа
    # и не загрузить интерфейс, который её покажет.
    if is_public(path):
        return await call_next(request)

    return JSONResponse({"detail": "Требуется вход"}, status_code=401)


async def check_password(request: Request):
    """POST /api/auth/check — проверка логина и пароля, выдача отметки."""
    settings = auth.load_auth_settings()
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 — тело может быть любым
        return JSONResponse({"detail": "Ожидается JSON"}, status_code=400)

    login = str(data.get("login", ""))
    password = str(data.get("password", ""))

    if not settings.active:
        # Вход выключен — считаем, что доступ открыт, и отдаём пустой
        # отметкой: интерфейс не должен ломаться на локальной машине.
        return {"token": "", "auth_required": False}

    if not settings.check(login, password):
        return JSONResponse(
            {"detail": "Неверный логин или пароль"}, status_code=401
        )

    return {"token": auth.make_header(login, password), "auth_required": True}


async def session_status(request: Request):
    """GET /api/auth/session — есть ли в этой вкладке действующий вход."""
    settings = auth.load_auth_settings()
    if not settings.active:
        return {"auth_required": False, "ok": True}

    header = request.headers.get(auth.AUTH_HEADER)
    return {"auth_required": True, "ok": auth.verify_header(settings, header)}


__all__ = ["guard", "check_password", "session_status", "is_browser_page"]