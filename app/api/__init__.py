"""API «ПромВизор» (Этап 6 — FastAPI, Handoff раздел 16).

Структура по документу:

* `schemas.py` — схемы ответов, повторяют контракт `app/core/types.py`;
* `routes.py`  — endpoints, только чтение базы;
* `main.py`    — точка входа приложения, CORS, служебные маршруты;
* `demo_data.py` — демонстрационные данные по явному запросу.

Запуск:

    .venv/bin/python -m app.api.main

Документация: http://127.0.0.1:8000/docs

Здесь намеренно **нет** `from app.api.main import app`. Такой импорт
заставил бы `python -m app.api.main` загрузить `main` дважды: сначала
как часть пакета, затем как `__main__`. Два экземпляра приложения
держали бы по соединению с базёй, и предупреждение runpy об этом
предупреждает. Если нужен объект `app`, импортируйте его напрямую:

    from app.api.main import app
"""

from __future__ import annotations

from app.api.routes import close_database, get_database, get_repository, router

__all__ = ["router", "get_database", "get_repository", "close_database"]