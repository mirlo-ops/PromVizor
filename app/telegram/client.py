"""Чтение данных backend'а «ПромВизор» (зона Gasun).

Единственный модуль, который ходит в HTTP. Всё остальное работает с
его словарями и не знает, откуда они пришли.

Договорённости, которые здесь зафиксированы (TELEGRAM-GASUN.md):

* `estimated_loss` берётся **готовым**. Формулы ущерба в этом пакете
  нет и быть не должно: считает только backend (`calculate_loss()`).
* `line_status == "UNKNOWN"` — это «данных нет», а не «линия стоит».
  Сообщение о простое при таком состоянии отправлять нельзя.
* `items` в `/api/events` приходят **новыми сверху**, поэтому порядок
  обработки — снизу вверх.
* Пустая база отдаёт `UNKNOWN` с нулями. Это штатное состояние, а не
  ошибка, и клиент не должен из-за него падать.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

#: Ответ без данных — так выглядит система, у которой ещё нет ни одного
#: снимка состояния. Не ошибка.
STATUS_UNKNOWN = "UNKNOWN"

#: Состояния линии в контракте `app/core/types.py`.
LINE_WORKING = "WORKING"
LINE_STOPPED = "STOPPED"


class BackendUnavailable(RuntimeError):
    """Backend не ответил или ответил не тем, что можно разобрать.

    Отдельный тип нужен, чтобы вызывающий код мог отличить «сервис
    перезапускается» от «сервис ответил, но данных нет». Пользователю
    об этом сообщать нельзя (TELEGRAM-GASUN.md, раздел 8): при обрыве
    связи бот молча ждёт и переподключается.
    """


class PromVizorClient:
    """Тонкий клиент API Lev.

    Аргументы:
        base_url: адрес backend'а, например `http://127.0.0.1:8000`.
        timeout: таймаут одного запроса, сек. Короткий намеренно: при
            недоступном сервисе бот должен быстро уйти в ожидание, а не
            висеть на мёртвом сокете.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Внутреннее
    # ------------------------------------------------------------------ #
    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Один GET. Ошибку связи поднимает как `BackendUnavailable`."""
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            # 4xx — это ошибка нашего запроса, её надо показать в лог:
            # молча проглотать нельзя, иначе поломка формата запроса
            # выглядит как «backend не отвечает».
            logger.error(
                "backend %s вернул %s для %s: %s",
                url,
                exc.response.status_code,
                path,
                exc.response.text[:200],
            )
            raise BackendUnavailable(f"{path}: HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError — тело ответа не JSON.
            logger.warning("backend недоступен (%s): %s", url, exc)
            raise BackendUnavailable(f"{path}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Данные
    # ------------------------------------------------------------------ #
    async def get_status(self) -> dict:
        """Текущее состояние: `GET /api/status`."""
        return await self._get("/api/status")

    async def get_events(
        self,
        limit: int = 50,
        event_type: Optional[str] = None,
        camera_id: Optional[int] = None,
    ) -> list[dict]:
        """События простоя: `GET /api/events`.

        Возвращает список `items` как есть — **новыми сверху**.
        Порядок меняет вызывающий код, и намеренно: порядок сортировки
        источника данных — его дело, а решение «обрабатывать снизу вверх»
        принимает потребитель событий.
        """
        params: dict[str, Any] = {"limit": limit}
        if event_type is not None:
            params["event_type"] = event_type
        if camera_id is not None:
            params["camera_id"] = camera_id

        data = await self._get("/api/events", params=params)
        items = data.get("items") or []
        return list(items)

    async def get_statistics(self) -> dict:
        """Итоги за период: `GET /api/statistics`.

        Считаются только завершённые простоя: у начатого ещё нет ни
        длительности, ни ущерба.
        """
        return await self._get("/api/statistics")

    async def get_config(self) -> dict:
        """Настройки: `GET /api/config`.

        Нужны только для подписи («за минуту — 750 ₽»). Пересчитывать
        по `cost_per_minute` ущерб нельзя.
        """
        return await self._get("/api/config")

    async def get_latest_event_id(self) -> int:
        """id самого свежего события во всей базе — 0, если база пуста.

        Нужен для первого запуска: курсор ставится сюда, чтобы бот не
        засыпал сообщениями за всю накопленную историю.

        Запрашивается **без фильтра** по `event_type` намеренно:
        при фильтре в `total` лежит число отфильтрованных событий, а не
        максимальный `id` — курсор занизился бы, и бот прислал бы
        несколько старых событий повторно. И `items[0]` в выдаче без
        фильтра — действительно самое свежее событие базы, потому что
        страница приходит новыми сверху.
        """
        items = await self.get_events(limit=1)
        if not items:
            return 0
        return int(items[0].get("id") or 0)


__all__ = [
    "PromVizorClient",
    "BackendUnavailable",
    "STATUS_UNKNOWN",
    "LINE_WORKING",
    "LINE_STOPPED",
]
