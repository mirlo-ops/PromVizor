"""Telegram-бот «ПромВизора» (зона ответственности Gasun).

Бот НЕ анализирует видео, НЕ определяет простой и НЕ считает ущерб.
Он читает готовые данные у backend'а Lev через HTTP API
(см. `TELEGRAM-GASUN.md`) и форматирует их в сообщения.

Слои:

* `client.py`   — чтение API Lev (единственное место, где ходим в сеть);
* `formatter.py` — тексты сообщений;
* `monitor.py`  — слежение за новыми событиями без повторов (курсор `id`);
* `bot.py`      — команды и запуск.

Главное правило проекта проходит через весь пакет: **ущерб берётся
готовым** из поля `estimated_loss` и никогда не пересчитывается на
стороне бота (Handoff, раздел 14).
"""

from app.telegram.client import PromVizorClient
from app.telegram.formatter import format_history, format_status, format_statistics
from app.telegram.monitor import EventMonitor

__all__ = [
    "PromVizorClient",
    "EventMonitor",
    "format_status",
    "format_history",
    "format_statistics",
]
