"""Проверка команд и отправки бота без реального Telegram (зона Gasun).

`tests/test_telegram.py` проверяет клиент, форматтер и курсор, а этот
файл — слой выше: сами обработчики `app/telegram/bot.py`, порядок
регистрации команд и решение «отправлено / не отправлено».

Реальный Telegram здесь не нужен: объект `Bot` подменяется заглушкой,
а `Application` собирается без обращения к сети — так проверка
остаётся быстрой и не зависит от интернета.

Запуск:

    .venv/bin/python tests/test_telegram_bot.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from telegram.error import Forbidden, NetworkError  # noqa: E402

from app.telegram.bot import (  # noqa: E402
    cmd_history,
    cmd_start,
    cmd_stats,
    cmd_status,
    handle_event,
)
from app.telegram.client import BackendUnavailable, PromVizorClient  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"\n         {detail}" if not ok and detail else ""))


def make_update():
    """Обновление с сообщением, которое можно перехватить."""
    update = MagicMock()
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


def make_context(client=None, chat_id="12345", bot=None):
    """Контекст как его видит обработчик PTB.

    Данные кладутся в `context.application.bot_data` — именно оттуда
    их читают обработчики (в реальном приложении это `bot_data`
    самого `Application`).
    """
    context = MagicMock()
    context.application.bot_data = {"client": client, "chat_id": chat_id}
    context.bot = bot or MagicMock()
    if bot is None:
        context.bot.send_message = AsyncMock()
    return context


class StubClient(PromVizorClient):
    def __init__(self, status=None, events=None, stats=None, config=None, available=True):
        super().__init__(base_url="http://test")
        self.status = status or {
            "camera_id": 1, "line_status": "STOPPED", "person_present": False,
            "downtime_seconds": 134, "estimated_loss": 1675.0,
        }
        self.events = events if events is not None else []
        self.stats = stats or {"total_downtime_seconds": 600, "total_loss": 7500.0, "events_count": 5}
        self.config = config or {"cost_per_minute": 750.0}
        self.available = available

    async def _get(self, path, params=None):
        if not self.available:
            raise BackendUnavailable("недоступно")
        return {
            "/api/status": self.status,
            "/api/events": {"items": self.events, "count": len(self.events), "total": len(self.events)},
            "/api/statistics": self.stats,
            "/api/config": self.config,
        }.get(path, {})


# --------------------------------------------------------------------------- #
async def test_commands() -> None:
    print("\n1. Команды")

    client = StubClient()
    context = make_context(client)

    update = make_update()
    await cmd_start(update, context)
    text = update.effective_message.reply_text.call_args[0][0]
    check("/start отвечает", "ПромВизор" in text, text)
    check("/start перечисляет команды", "/status" in text and "/history" in text, text)

    update = make_update()
    await cmd_status(update, context)
    text = update.effective_message.reply_text.call_args[0][0]
    check("/status показывает состояние", "ОСТАНОВЛЕНА" in text, text)
    check("/status показывает готовый ущерб", "1\u202f675 ₽" in text, text)
    check("/status отправлен в HTML",
          update.effective_message.reply_text.call_args.kwargs.get("parse_mode") is not None)

    update = make_update()
    await cmd_history(update, context)
    text = update.effective_message.reply_text.call_args[0][0]
    check("/history на пустой базе честен", "пока нет" in text, text)

    update = make_update()
    await cmd_stats(update, context)
    text = update.effective_message.reply_text.call_args[0][0]
    check("/stats показывает итоги", "7\u202f500 ₽" in text, text)
    check("/stats подписывает стоимость минуты", "Стоимость минуты" in text, text)

    # Пустая база: UNKNOWN с нулями — это не ошибка.
    empty = StubClient(status={
        "camera_id": 1, "line_status": "UNKNOWN", "person_present": False,
        "downtime_seconds": 0, "estimated_loss": 0.0,
    }, stats={"total_downtime_seconds": 0, "total_loss": 0.0, "events_count": 0})
    context = make_context(empty)
    update = make_update()
    await cmd_status(update, context)
    text = update.effective_message.reply_text.call_args[0][0]
    check("/status при пустой базе не падает", "НЕТ ДАННЫХ" in text, text)

    update = make_update()
    await cmd_history(update, empty and context)
    check("/history при пустой базе не падает", update.effective_message.reply_text.await_count > 0)


# --------------------------------------------------------------------------- #
async def test_backend_down() -> None:
    print("\n2. Backend недоступен")

    client = StubClient(available=False)
    context = make_context(client)

    for name, handler in (("/status", cmd_status), ("/history", cmd_history), ("/stats", cmd_stats)):
        update = make_update()
        await handler(update, context)
        text = update.effective_message.reply_text.call_args[0][0]
        check(f"{name} при обрыве связи отвечает, а не падает", "недоступен" in text, text)


# --------------------------------------------------------------------------- #
async def test_notifications() -> None:
    print("\n3. Уведомления")

    context = make_context()

    started = {
        "id": 1, "camera_id": 1, "event": "DOWNTIME_STARTED",
        "start_time": "12:40", "end_time": "",
        "duration_seconds": 0, "estimated_loss": 0.0,
    }
    ok = await handle_event(started, context)
    check("начало простоя отправлено", ok is True)
    text = context.bot.send_message.call_args.kwargs["text"]
    check("в уведомлении о начале нет суммы", "₽" not in text, text)

    finished = {
        "id": 2, "camera_id": 1, "event": "DOWNTIME_FINISHED",
        "start_time": "12:40", "end_time": "12:45",
        "duration_seconds": 300, "estimated_loss": 3750.0,
    }
    ok = await handle_event(finished, context)
    text = context.bot.send_message.call_args.kwargs["text"]
    check("завершение простоя отправлено", ok is True)
    check("в уведомлении о завершении есть готовая сумма", "3\u202f750 ₽" in text, text)
    check("уведомление отправлено в чат из настроек",
          context.bot.send_message.call_args.kwargs["chat_id"] == "12345")

    # Неизвестный тип события: не повторяем, но и не роняем цикл.
    before = context.bot.send_message.await_count
    ok = await handle_event({"id": 3, "event": "ВЗРЫВ"}, context)
    check("неизвестный тип не отправляется", context.bot.send_message.await_count == before)
    check("неизвестный тип не блокирует курсор", ok is True)

    # Сбой Telegram: событие должно остаться необработанным.
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=NetworkError("нет сети"))
    context = make_context(bot=bot)
    ok = await handle_event(finished, context)
    check("при обрыве Telegram событие не отмечено обработанным", ok is False)

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=Forbidden("чат заблокирован"))
    context = make_context(bot=bot)
    ok = await handle_event(finished, context)
    check("при блокировке чата событие ждёт", ok is False)


# --------------------------------------------------------------------------- #
async def test_registration() -> None:
    print("\n4. Регистрация команд")

    from app.telegram.bot import build_application

    # Токен заведомо нерабочий: обращения к сети здесь не происходит,
    # токен используется только как идентификатор бота внутри PTB.
    app = build_application("123456:TEST-TEST-TEST-TEST-TESTTESTTEST")

    registered = {}
    for handlers in app.handlers.values():
        for handler in handlers:
            for command in handler.commands:
                registered[command] = handler.callback.__name__

    check("/start зарегистрирован", "start" in registered, str(registered))
    check("/help зарегистрирован", "help" in registered, str(registered))
    check("/status зарегистрирован", "status" in registered, str(registered))
    check("/history зарегистрирован", "history" in registered, str(registered))
    check("/stats зарегистрирован", "stats" in registered, str(registered))
    check("обработчик /status ведёт в cmd_status", registered.get("status") == "cmd_status", str(registered))


# --------------------------------------------------------------------------- #
async def test_cursor_persistence() -> None:
    print("\n5. Курсор переживает перезапуск")

    from app.telegram.bot import CURSOR_FILE, _load_cursor, _save_cursor

    path = Path(CURSOR_FILE)
    backup = path.read_text(encoding="utf-8") if path.exists() else None

    # Каталог для проверки mkdir берём во временной папке: иначе тест
    # оставлял бы мусор в репозитории после каждого запуска.
    tmp_dir = Path(tempfile.mkdtemp(prefix="promvizor_tg_"))

    try:
        path.unlink(missing_ok=True)
        _save_cursor(path, 500)

        check("курсор сохранён в файл", _load_cursor(path) == 500, str(_load_cursor(path)))

        # «Перезапуск»: новый процесс читает курсор из того же файла.
        check("после перезапуска курсор тот же", _load_cursor(path) == 500)

        # Команда без файла — первый запуск, а не падение.
        path.unlink(missing_ok=True)
        check("нет файла = первый запуск", _load_cursor(path) is None)

        # Файл в каталоге, которого нет, не должен ронять бота.
        missing_dir = tmp_dir / "подкаталог" / "курсор.json"
        _save_cursor(missing_dir, 1)
        check("сохранение создаёт каталог", missing_dir.exists())
    finally:
        path.unlink(missing_ok=True)
        if backup is not None:
            path.write_text(backup, encoding="utf-8")
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def main() -> int:
    await test_commands()
    await test_backend_down()
    await test_notifications()
    await test_registration()
    await test_cursor_persistence()

    print(f"\n{'=' * 52}")
    print(f"Пройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    if FAILED:
        print("\nПровалено:")
        for n in FAILED:
            print(f"  - {n}")
        return 1
    print("Команды и уведомления работают.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
