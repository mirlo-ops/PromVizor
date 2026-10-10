"""Слежение за новыми событиями простоя (зона Gasun).

Главная задача модуля — **не прислать одно событие дважды** и **не
пропустить ни одного**. Обе задачи решает курсор: последний
обработанный `id` события.

Как это работает (TELEGRAM-GASUN.md, раздел 5):

* `id` события строго возрастает — это единственное, на чём можно
  опереться;
* страница событий приходит **новыми сверху**, поэтому обрабатывать
  её надо снизу вверх, иначе уведомления придут в обратном порядке;
* при первом запуске курсор ставится на самое свежее событие: иначе
  бот засыпает сообщениями за всю историю, накопившуюся за время
  работы системы;
* заглядывать вперёд по `id` нельзя — `id` выдаётся по мере событий,
  сегодняшний максимум ещё не завтрашний минимум.

Отдельно про два разных времени в событии (раздел 7 документа):
`start_time` / `end_time` — местное время машины с backend'ом, его
показывают человеку; `created_at` — всегда UTC, по нему фильтруют и
хранят. Модуль не смешивает их: в курсоре участвует только `id`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from app.telegram.client import BackendUnavailable, PromVizorClient

logger = logging.getLogger(__name__)

#: Что делать с событием. `AsyncCallback` возвращает True, если
#: событие обработано и курсор можно двигать.
EventHandler = Callable[[dict], Awaitable[bool]]


class EventMonitor:
    """Опрашивает backend и зовёт обработчик для каждого нового события.

    Аргументы:
        client: клиент API Lev.
        handler: функция `async def handler(event) -> bool`. True — курсор
            двигаем, False — событие не обработано и будет попробовано
            снова в следующем цикле.
        poll_seconds: пауза между опросами.
        cursor: последний обработанный `id`. `None` — первый запуск,
            курсор будет поставлен на текущий конец базы.
    """

    def __init__(
        self,
        client: PromVizorClient,
        handler: EventHandler,
        poll_seconds: float = 10.0,
        cursor: Optional[int] = None,
    ) -> None:
        self.client = client
        self.handler = handler
        self.poll_seconds = poll_seconds
        self.cursor = cursor

    # ------------------------------------------------------------------ #
    # Курсор
    # ------------------------------------------------------------------ #
    def is_first_run(self) -> bool:
        """Курсор ещё не инициализирован."""
        return self.cursor is None

    async def init_cursor(self) -> int:
        """Первый запуск: пропустить накопленную историю.

        Берётся `id` самого свежего события. Важно, что спрашивается
        **без фильтра** по типу события: при фильтре `items[0]` — это
        newest в отфильтрованной выборке, а не в базе, и часть истории
        вернулась бы повторно.
        """
        last_id = await self.client.get_latest_event_id()
        self.cursor = last_id
        logger.info("первый запуск: курсор поставлен на id=%s, история пропущена", last_id)
        return last_id

    # ------------------------------------------------------------------ #
    # Опрос
    # ------------------------------------------------------------------ #
    async def poll_once(self) -> int:
        """Один опрос. Возвращает число обработанных событий.

        Ошибки связи не поднимаются наружу: цикл обязан жить дальше.
        Иначе перезапуск backend'а останавливал бы уведомления, и
        события, накопившиеся за время простоя, пришли бы пачкой —
        либо потерялись бы вместе с процессом.
        """
        try:
            events = await self.client.get_events(limit=200)
        except BackendUnavailable as exc:
            logger.debug("опрос пропущен, backend недоступен: %s", exc)
            return 0

        if self.is_first_run():
            await self.init_cursor()
            return 0

        # items приходят новыми сверху — идём от старых к новым,
        # иначе уведомления придут в обратном порядке.
        fresh = [event for event in reversed(events) if int(event.get("id") or 0) > self.cursor]

        handled = 0
        for event in fresh:
            event_id = int(event.get("id") or 0)
            try:
                ok = await self.handler(event)
            except Exception:  # noqa: BLE001 — одно событие не должно ронять цикл
                logger.exception("не удалось обработать событие id=%s", event_id)
                # Курсор не двигаем: событие попробуем ещё раз.
                break

            if not ok:
                # Обработчик не справился (например, Telegram временно
                # недоступен). Стоп — иначе следующие события придут
                # не по порядку, а этот потеряется навсегда.
                logger.warning("событие id=%s не обработано, ждём следующего цикла", event_id)
                break

            self.cursor = event_id
            handled += 1

        return handled

    async def run(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Цикл опроса до остановки.

        `stop_event` позволяет завершиться чисто: пока он не установлен
        и его нельзя отменить, цикл крутится.
        """
        logger.info("слежение за событиями: каждые %.0f сек", self.poll_seconds)
        while stop_event is None or not stop_event.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                logger.info("слежение остановлено")
                raise
            except Exception:  # noqa: BLE001 — цикл не должен умирать
                logger.exception("непредвиденная ошибка в цикле наблюдения")

            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.poll_seconds)
                    break
                except asyncio.TimeoutError:
                    continue
            else:
                await asyncio.sleep(self.poll_seconds)


__all__ = ["EventMonitor", "EventHandler"]
