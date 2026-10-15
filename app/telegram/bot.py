"""Telegram-бот «ПромВизора» — команды и запуск (наша зона ответственности).

Запуск:

    .venv/bin/python -m app.telegram.bot

Настройки берутся из `.env` (он уже читается проектом через
`app/core/env.py`), поэтому ключи те же, что и у backend'а:

    TELEGRAM_BOT_TOKEN=123456:ABC...
    TELEGRAM_CHAT_ID=-1001234567890
    TELEGRAM_API_URL=http://127.0.0.1:8000   # необязательно
    TELEGRAM_POLL_SECONDS=10                 # необязательно

Команды (раздел 20 Handoff):

    /start    — приветствие и что бот умеет;
    /status   — состояние прямо сейчас;
    /history  — последние события простоя;
    /stats    — итоги за период;
    /help     — то же, что /start.

Плюс автоматические уведомления: при начале простоя и при его
завершении.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from app.telegram.client import BackendUnavailable, PromVizorClient
from app.telegram.formatter import (
    EVENT_FINISHED,
    EVENT_STARTED,
    format_event,
    format_history,
    format_statistics,
    format_status,
    format_unavailable,
)
from app.telegram.monitor import EventMonitor
from app.telegram.proxy import (
    ProxySettings,
    build_telegram_proxy,
    load_proxy_settings,
)

logger = logging.getLogger(__name__)

#: Состояние прокси/соединения. Общая практика PTB: без неё
#: long polling работает заметно хуже на некоторых сетях.
REQUEST_TIMEOUT = 30.0

#: Курсор в файле. Хранится на диске, а не в памяти: иначе после
#: перезапуска бота он прислал бы всю историю заново — ровно то, чего
#: мы добиваемся курсором.
CURSOR_FILE = "telegram_cursor.json"


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #
def _read_settings() -> tuple[str, str, str, float]:
    """Достаёт токен, чат, адрес backend'а и период опроса.

    Значения приходят из окружения, куда их положил `app/core/config.py`
    при импорте. Пустой токен или чат — это не повод падать на
    импорте модуля: об этом честно сообщается при запуске.
    """
    from app.core.config import settings

    token = settings.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = settings.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    base_url = os.environ.get("TELEGRAM_API_URL", "http://127.0.0.1:8000")
    poll_seconds = float(os.environ.get("TELEGRAM_POLL_SECONDS", "10"))

    return token, chat_id, base_url, poll_seconds


def parse_chat_ids(raw: str) -> list[str]:
    """Разбирает список чатов из TELEGRAM_CHAT_ID.

    Уведомления о простое обычно нужны нескольким людям: мастеру смены
    и руководителю. Раньше поддерживался только один чат, и второму
    просто не приходило ничего — при этом в `.env` молча оставалось
    значение «750743167,8851242509», которое Telegram принимает за
    несуществующий чат и отвечает ошибкой 400.

    Формат: `id1,id2` через запятую, пробелы игнорируются. Для группы
    используется её id со знаком минус.
    """
    return [part.strip() for part in (raw or "").replace(";", ",").split(",") if part.strip()]


# --------------------------------------------------------------------------- #
# Курсор на диске
# --------------------------------------------------------------------------- #
def _load_cursor(path: Path) -> Optional[int]:
    """Читает сохранённый курсор. Отсутствие файла — это первый запуск."""
    try:
        raw = path.read_text(encoding="utf-8")
        return int(json.loads(raw)["last_id"])
    except (OSError, ValueError, KeyError, TypeError):
        # Битый файл курсора не должен мешать работать: считаем, что
        # это первый запуск, и пропустим историю. Повтор событий здесь
        # невозможен — курсор встаёт на конец базы.
        return None


def _save_cursor(path: Path, last_id: int) -> None:
    """Сохраняет курсор. Ошибка записи не должна ронять отправку."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_id": int(last_id)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("не удалось сохранить курсор в %s: %s", path, exc)


# --------------------------------------------------------------------------- #
# Приветствие
# --------------------------------------------------------------------------- #
WELCOME = (
    "👋 <b>ПромВизор — бот контроля простоев</b>\n\n"
    "Слежу за производственными линиями и сообщаю о простоях.\n"
    "Ущерб считает сервер, я только показываю готовую цифру.\n\n"
    "<b>Команды:</b>\n"
    "/status — состояние линии прямо сейчас\n"
    "/history — последние события простоя\n"
    "/stats — итоги за период\n"
    "/help — эта справка\n\n"
    "Уведомления приходят сами: при начале простоя и при его завершении."
)


# --------------------------------------------------------------------------- #
# Обработчики команд
# --------------------------------------------------------------------------- #
async def _reply(update: Update, text: str) -> None:
    """Отвечает в HTML, переживая сбои Telegram.

    Ошибка отправки не должна валить обработчик команды: иначе
    пользователь получит вместо ответа ничего и решит, что бот сломан.
    """
    message = update.effective_message
    if message is None:
        return

    try:
        await message.reply_text(text, parse_mode=ParseMode.HTML)
    except Forbidden:
        # Чат заблокировал бота. Молча: повторно пробовать бесполезно.
        logger.warning("чат заблокировал бота, сообщение не доставлено")
    except (NetworkError, TelegramError) as exc:
        logger.warning("не удалось отправить сообщение: %s", exc)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — приветствие."""
    await _reply(update, WELCOME)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — список команд."""
    await _reply(update, WELCOME)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — состояние прямо сейчас."""
    client: PromVizorClient = context.application.bot_data["client"]

    try:
        status = await client.get_status()
    except BackendUnavailable:
        # Только здесь пользователь узнаёт про обрыв: он сам спросил,
        # и молчать на прямой вопрос нельзя. В фоне бот об этом молчит.
        await _reply(update, format_unavailable())
        return

    await _reply(update, format_status(status))


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history — последние события простоя."""
    client: PromVizorClient = context.application.bot_data["client"]

    try:
        events = await client.get_events(limit=10)
    except BackendUnavailable:
        await _reply(update, format_unavailable())
        return

    await _reply(update, format_history(events))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — итоги за период."""
    client: PromVizorClient = context.application.bot_data["client"]

    try:
        stats = await client.get_statistics()
    except BackendUnavailable:
        await _reply(update, format_unavailable())
        return

    # Стоимость минуты нужна только подписью. Если запрос не прошёл —
    # показываем итоги без неё, а не отказываем в ответе.
    cost_per_minute = None
    try:
        config = await client.get_config()
        cost_per_minute = config.get("cost_per_minute")
    except BackendUnavailable:
        logger.debug("не удалось прочитать /api/config, итоги без подписи")

    await _reply(update, format_statistics(stats, cost_per_minute))


# --------------------------------------------------------------------------- #
# Уведомления
# --------------------------------------------------------------------------- #
async def _notify(context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Отправляет уведомление. True — доставлено, курсор можно двигать.

    Сообщение уходит всем чатам из TELEGRAM_CHAT_ID: уведомление о
    простое обычно нужно и мастеру, и руководителю. Событие считается
    доставленным, только когда дошло до всех: иначе чат, который не
    принял сообщение, потерял бы его навсегда.
    """
    chat_ids: list[str] = context.application.bot_data["chat_ids"]
    delivered = False

    for chat_id in chat_ids:
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
            )
            delivered = True
        except Forbidden:
            # Чат заблокировал бота: это навсегда, и повторять
            # бессмысленно — но и событие не помечаем доставленным,
            # если оно ушло хоть кому-то.
            logger.warning("чат %s заблокировал бота", chat_id)
        except (NetworkError, TelegramError) as exc:
            logger.warning("не удалось отправить в чат %s: %s", chat_id, exc)

    return delivered


async def handle_event(update: dict, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обработчик события для `EventMonitor`.

    Возвращает True, когда событие доставлено и курсор можно сдвинуть.
    """
    event_type = update.get("event")

    if event_type not in (EVENT_STARTED, EVENT_FINISHED):
        logger.warning("неизвестный тип события %r, пропускаю", event_type)
        return True  # это не наша ошибка — повторять бессмысленно

    try:
        text = format_event(update)
    except ValueError as exc:  # pragma: no cover — защита формата
        logger.error("не удалось сформатировать событие: %s", exc)
        return True

    if text is None:
        return True

    return await _notify(context, text)


# --------------------------------------------------------------------------- #
# Запуск
# --------------------------------------------------------------------------- #
def build_application(
    token: str, proxy: Optional[ProxySettings] = None
) -> Application:
    """Собирает приложение бота с командами.

    `proxy` — только для связи с Telegram. Обращения к backend'у идут
    по-прежнему напрямую: пускать данные о простое завода через
    сторонний прокси незачем, а при неверной настройке прокси это ещё
    и выглядело бы как «бот не читает данные».
    """
    builder = (
        ApplicationBuilder()
        .token(token)
        .connect_timeout(REQUEST_TIMEOUT)
        .read_timeout(REQUEST_TIMEOUT)
    )

    proxy_settings = proxy or load_proxy_settings()
    if proxy_settings.configured:
        # Здесь может подняться ValueError из-за адреса без логина —
        # это понятная причина, и она попадёт в журнал бота вместе с
        # подсказкой, а не молча превратится в «Telegram недоступен».
        builder = builder.proxy(build_telegram_proxy(proxy_settings))
        logger.info("Telegram: %s", proxy_settings.describe())

    application = builder.build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("stats", cmd_stats))

    return application


def main() -> int:
    """Точка входа: `python -m app.telegram.bot`."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        level=logging.INFO,
    )

    token, chat_id, base_url, poll_seconds = _read_settings()
    chat_ids = parse_chat_ids(chat_id)

    if not token or not chat_ids:
        logger.error(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.\n"
            "Добавьте их в .env (см. .env.example) и запустите бота снова."
        )
        return 1

    proxy = load_proxy_settings()
    logger.info(
        "backend: %s, чат: %s, опрос: %.0f сек, Telegram: %s",
        base_url,
        chat_ids,
        poll_seconds,
        proxy.describe(),
    )

    application = build_application(token, proxy)
    client = PromVizorClient(base_url)
    application.bot_data["client"] = client
    application.bot_data["chat_ids"] = chat_ids
    application.bot_data["chat_id"] = chat_ids[0] if chat_ids else ""

    cursor_path = Path(CURSOR_FILE)

    async def _watch() -> None:
        monitor = EventMonitor(
            client=client,
            handler=lambda event: handle_event(event, application),
            poll_seconds=poll_seconds,
            cursor=_load_cursor(cursor_path),
        )
        last_saved = -1
        while True:
            try:
                await monitor.poll_once()
            except Exception:  # noqa: BLE001 — фон не должен умирать
                logger.exception("ошибка в цикле наблюдения")

            # Курсор сохраняем после каждого успешного шага: падение
            # бота не должно приводить к повторной рассылке.
            if monitor.cursor is not None and monitor.cursor != last_saved:
                _save_cursor(cursor_path, monitor.cursor)
                last_saved = monitor.cursor

            await asyncio.sleep(poll_seconds)

    async def _post_init(application: Application) -> None:
        application.create_task(_watch())

    application.post_init = _post_init

    application.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
