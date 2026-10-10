"""Тексты сообщений Telegram-бота (зона Gasun).

Здесь живёт всё, что человек видит в чате. Модуль намеренно «глупый»:
он получает словарь из API и превращает его в строку, ничего не зная
о видео, ROI и порогах простоя.

Два правила, которые нельзя нарушать (Handoff, разделы 13 и 18):

1. **Ущерб не пересчитывается.** Функции берут готовый
   `estimated_loss` из ответа API. Формулы `минуты × стоимость` здесь
   нет, и добавлять её нельзя: суммы разойдутся с Dashboard, и при
   расхождении никто не поймёт, где ошибка.
2. **Формулировки осторожные.** Камера видит только контролируемую
   зону, поэтому пишем «рабочий не обнаружен в контролируемой зоне
   камеры», а не «рабочий отсутствует на предприятии».
"""

from __future__ import annotations

from typing import Iterable, Optional

from app.telegram.client import (
    LINE_STOPPED,
    LINE_WORKING,
    STATUS_UNKNOWN,
)

#: Разделитель тысяч — неразрывный пробел. Telegram-разметка это
#: понимает, а обычный пробел в числах выглядит как «1 680.50».
THOUSAND_SEPARATOR = "\u202f"

#: Типы событий из контракта `app/core/types.py`.
EVENT_STARTED = "DOWNTIME_STARTED"
EVENT_FINISHED = "DOWNTIME_FINISHED"


def format_money(value: float) -> str:
    """Сумма в рублях из **готового** значения API.

    Разделитель разрядов — неразрывный пробел, копейки показываются
    только когда они есть: «1 680 ₽» читается лучше, чем «1 680,00 ₽».
    """
    amount = float(value or 0)
    if amount == int(amount):
        return f"{int(amount):,d}".replace(",", THOUSAND_SEPARATOR) + " ₽"

    whole, _, fraction = f"{amount:.2f}".partition(".")
    # Хвостовые нули дробной части не несут информации: 1 680,50 ₽
    # вместо 1 680,5000 ₽.
    fraction = fraction.rstrip("0")
    return f"{int(whole):,d}".replace(",", THOUSAND_SEPARATOR) + f",{fraction} ₽"


def format_duration(seconds: int) -> str:
    """Длительность простоя человеческим языком: «2 мин 14 сек»."""
    total = max(0, int(seconds or 0))
    if total < 60:
        return f"{total} сек"

    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)

    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs:
        parts.append(f"{secs} сек")
    return " ".join(parts)


def format_person(person_present: bool) -> str:
    """Рабочий в контролируемой зоне — или нет.

    Формулировка согласована с Lev: система видит только то, что
    попало в камеру, и утверждать об отсутствии человека на всём
    предприятии она не может.
    """
    if person_present:
        return "обнаружен в контролируемой зоне"
    return "не обнаружен в контролируемой зоне камеры"


def format_status(status: dict) -> str:
    """Текущее состояние — ответ на `/status` (раздел 20 Handoff)."""
    line_status = str(status.get("line_status") or STATUS_UNKNOWN)
    person_present = bool(status.get("person_present"))
    downtime_seconds = int(status.get("downtime_seconds") or 0)
    loss = float(status.get("estimated_loss") or 0.0)

    if line_status == LINE_WORKING:
        header = "🟢 СИСТЕМА РАБОТАЕТ"
    elif line_status == LINE_STOPPED:
        header = "⚠️ ЛИНИЯ ОСТАНОВЛЕНА"
    else:
        # UNKNOWN — данных нет. Это не «линия стоит» и не «всё хорошо»,
        # поэтому и заголовок другой: утверждать состояние нельзя.
        header = "⚪ НЕТ ДАННЫХ О СОСТОЯНИИ ЛИНИИ"

    lines = [
        f"<b>{header}</b>",
        "",
        f"Камера: №{status.get('camera_id', '?')}",
        f"Линия: {line_status}",
        f"Рабочий: {format_person(person_present)}",
    ]

    if downtime_seconds > 0:
        # Простой идёт прямо сейчас: его длительность и ущерб уже
        # посчитаны backend'ом и приходят готовыми.
        lines.append(f"Простой: {format_duration(downtime_seconds)}")
        if loss > 0:
            lines.append(f"Ущерб: {format_money(loss)}")
    else:
        lines.append("Простой: нет")

    return "\n".join(lines)


def format_downtime_started(event: dict) -> str:
    """Уведомление о начале простоя.

    У события `DOWNTIME_STARTED` ущерб всегда 0.0 — он считается при
    завершении. Поэтому здесь его нет вовсе: присылать «Ущерб: 0 ₽»
    значило бы утверждать, что потерь нет, а они наверняка идут.
    """
    return (
        "⚠️ <b>ОБНАРУЖЕН ПРОСТОЙ</b>\n\n"
        f"Камера: №{event.get('camera_id', '?')}\n"
        f"Линия: {event.get('line_status') or LINE_STOPPED}\n"
        f"Начало: {event.get('start_time') or '—'}\n"
        "Рабочий: не обнаружен в контролируемой зоне камеры"
    )


def format_downtime_finished(event: dict) -> str:
    """Уведомление о завершении простоя — с готовым ущербом."""
    duration = int(event.get("duration_seconds") or 0)
    loss = float(event.get("estimated_loss") or 0.0)

    start = event.get("start_time") or "—"
    end = event.get("end_time") or "—"

    return (
        "✅ <b>ПРОСТОЙ ЗАВЕРШЁН</b>\n\n"
        f"Камера: №{event.get('camera_id', '?')}\n"
        f"Время: {start} – {end}\n"
        f"Длительность: {format_duration(duration)}\n"
        f"Ущерб: {format_money(loss)}"
    )


def format_event(event: dict) -> Optional[str]:
    """Текст сообщения по событию; `None` — за него сообщать нечего.

    Вызывается только для событий, которые бот ещё не показывал.
    """
    event_type = event.get("event")
    if event_type == EVENT_FINISHED:
        return format_downtime_finished(event)
    if event_type == EVENT_STARTED:
        return format_downtime_started(event)

    raise ValueError(f"Неизвестный тип события: {event_type!r}")


def format_history(events: Iterable[dict]) -> str:
    """Последние события — ответ на `/history`."""
    items = list(events)
    if not items:
        return "📋 <b>Событий простоя пока нет</b>\n\nСобытия появятся здесь автоматически."

    lines = ["📋 <b>Последние события простоя</b>", ""]

    for event in items:
        event_type = str(event.get("event") or "")
        start = event.get("start_time") or "—"
        camera = event.get("camera_id", "?")

        if event_type == EVENT_FINISHED:
            duration = format_duration(int(event.get("duration_seconds") or 0))
            loss = format_money(float(event.get("estimated_loss") or 0.0))
            lines.append(f"• <code>{start}</code> — простой {duration}, ущерб {loss}")
        elif event_type == EVENT_STARTED:
            lines.append(f"• <code>{start}</code> — простой начался")
        else:
            # Неизвестный тип не должен ломать всю выдачу: показываем
            # то, что есть, и продолжаем.
            lines.append(f"• <code>{start}</code> — событие: {event_type or '—'}")
        lines.append(f"  камера №{camera}")

    return "\n".join(lines)


def format_statistics(stats: dict, cost_per_minute: Optional[float] = None) -> str:
    """Итоги за период — по данным `/api/statistics`.

    Считаются только завершённые простоя: у начатого ещё нет ни
    длительности, ни ущерба, и показывать его в итогах значило бы
    занижать суммы.

    `cost_per_minute` идёт только подписью («за минуту — 750 ₽»).
    Ни одна сумма в этом тексте по нему не выводится.
    """
    events_count = int(stats.get("events_count") or 0)
    total_seconds = int(stats.get("total_downtime_seconds") or 0)
    total_loss = float(stats.get("total_loss") or 0.0)

    if events_count == 0:
        text = "📊 <b>Итоги</b>\n\nЗавершённых простоев пока нет."
    else:
        text = (
            "📊 <b>Итоги</b>\n\n"
            f"Завершённых простоев: {events_count}\n"
            f"Общий простой: {format_duration(total_seconds)}\n"
            f"Общий ущерб: {format_money(total_loss)}"
        )

    if cost_per_minute:
        text += f"\n\n<i>Стоимость минуты простоя: {format_money(cost_per_minute)}</i>"
    return text


def format_unavailable() -> str:
    """Ответ, когда backend недоступен.

    Показывается только по прямой команде пользователя. В фоне бот об
    обрыве связи молчит: перезапуск сервиса не должен выглядеть как
    авария (раздел 2 TELEGRAM-GASUN.md).
    """
    return (
        "🔌 <b>Backend недоступен</b>\n\n"
        "Не удалось получить данные системы. Бот продолжит опрос и "
        "пришлёт уведомление, когда связь восстановится."
    )


__all__ = [
    "EVENT_STARTED",
    "EVENT_FINISHED",
    "format_money",
    "format_duration",
    "format_person",
    "format_status",
    "format_event",
    "format_downtime_started",
    "format_downtime_finished",
    "format_history",
    "format_statistics",
    "format_unavailable",
]
