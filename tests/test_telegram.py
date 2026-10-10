"""Тесты Telegram-бота (зона Gasun).

Проверяется не «работает ли код», а соблюдение договорённостей из
`TELEGRAM-GASUN.md`, потому что именно на них чаще всего ломается
интеграция:

* ущерб берётся готовым и не пересчитывается;
* при `UNKNOWN` сообщение о простое не отправляется;
* события обрабатываются снизу вверх и не повторяются;
* при первом запуске история пропускается;
* обрыв связи не роняет ни цикл, ни курсор.

Запуск (видео, YOLO и Telegram не нужны):

    .venv/bin/python tests/test_telegram.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Импорт по пути репозитория — тест запускается из любого каталога.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Консоль Windows по умолчанию cp1251 и не печатает «₽» и «✅».
# Тесты печатают эти символы, поэтому кодировку выставляем явно —
# иначе проверка падала бы на выводе, а не по существу.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.telegram.client import BackendUnavailable, PromVizorClient  # noqa: E402
from app.telegram.formatter import (  # noqa: E402
    format_duration,
    format_event,
    format_history,
    format_money,
    format_person,
    format_statistics,
    format_status,
)
from app.telegram.monitor import EventMonitor  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}" + (f"\n         {detail}" if detail else ""))


class FakeClient(PromVizorClient):
    """Клиент API на заглушке — сеть в тестах не нужна."""

    def __init__(self, events=None, status=None, available=True):
        super().__init__(base_url="http://test", timeout=0.01)
        self.events = events if events is not None else []
        self.status = status or {}
        self.available = available
        self.calls: list[tuple] = []

    async def _get(self, path, params=None):
        self.calls.append((path, params))
        if not self.available:
            raise BackendUnavailable("сервис недоступен")
        if path == "/api/status":
            return self.status
        if path == "/api/events":
            return {"items": list(self.events), "count": len(self.events), "total": len(self.events)}
        if path == "/api/statistics":
            return {"total_downtime_seconds": 600, "total_loss": 7500.0, "events_count": 5}
        return {}


def make_event(event_id: int, event_type="DOWNTIME_FINISHED", **kwargs) -> dict:
    base = {
        "id": event_id,
        "camera_id": 1,
        "event": event_type,
        "start_time": "12:40",
        "end_time": "12:45",
        "duration_seconds": 300,
        "estimated_loss": 3750.0,
        "created_at": "2026-10-10T14:46:10+00:00",
    }
    base.update(kwargs)
    return base


# --------------------------------------------------------------------------- #
# 1. Форматирование
# --------------------------------------------------------------------------- #
def test_formatting() -> None:
    print("\n1. Форматирование")

    check("деньги: целое без копеек", format_money(1680.0) == "1\u202f680 ₽", format_money(1680.0))
    check("деньги: копейки без хвостовых нулей", format_money(112.5) == "112,5 ₽", format_money(112.5))
    check("деньги: ноль", format_money(0) == "0 ₽", format_money(0))
    check("деньги: отрицательное не ломает формат", format_money(-5) == "-5 ₽", format_money(-5))

    check("длительность: секунды", format_duration(45) == "45 сек", format_duration(45))
    check("длительность: минуты и секунды", format_duration(134) == "2 мин 14 сек", format_duration(134))
    check("длительность: часы", format_duration(3671) == "1 ч 1 мин 11 сек", format_duration(3671))
    check("длительность: ноль", format_duration(0) == "0 сек", format_duration(0))

    check(
        "формулировка о рабочем согласована с Lev",
        format_person(False) == "не обнаружен в контролируемой зоне камеры",
        format_person(False),
    )
    check("формулировка о рабочем (есть)", format_person(True) == "обнаружен в контролируемой зоне")


# --------------------------------------------------------------------------- #
# 2. Ущерб не пересчитывается
# --------------------------------------------------------------------------- #
def test_loss_not_recalculated() -> None:
    print("\n2. Ущерб берётся готовым")

    # Событие с «неудобной» суммой: 1 234,56 ₽ за 99 секунд.
    # Никакая формула в боте не дала бы это значение из duration.
    event = make_event(1, duration_seconds=99, estimated_loss=1234.56)
    text = format_event(event)

    check("сумма в сообщении = estimated_loss из API", "1\u202f234,56 ₽" in text, text)

    # Та же длительность, другая готовая сумма — текст обязан измениться.
    event2 = make_event(1, duration_seconds=99, estimated_loss=9999.99)
    text2 = format_event(event2)
    check("сумма берётся из данных, а не из длительности", "9\u202f999,99 ₽" in text2, text2)

    # Стоимость минуты в подписи не влияет на суммы итогов.
    stats = {"events_count": 5, "total_downtime_seconds": 600, "total_loss": 7500.0}
    with_cost = format_statistics(stats, cost_per_minute=750.0)
    without_cost = format_statistics(stats)
    check("стоимость минуты только в подписи", "7\u202f500 ₽" in with_cost, with_cost)
    check("подпись о стоимости отделена", "Стоимость минуты" in with_cost)
    check("итоги без стоимости тоже верны", "7\u202f500 ₽" in without_cost, without_cost)

    source = (Path(__file__).resolve().parent.parent / "app" / "telegram")
    code = "".join(p.read_text(encoding="utf-8") for p in source.glob("*.py"))
    check("в коде бота нет формулы умножения на минуты", "cost_per_minute *" not in code)


# --------------------------------------------------------------------------- #
# 3. UNKNOWN — не простой
# --------------------------------------------------------------------------- #
def test_unknown_status() -> None:
    print("\n3. UNKNOWN не равен простоям")

    unknown = {
        "camera_id": 1,
        "line_status": "UNKNOWN",
        "person_present": False,
        "downtime_seconds": 0,
        "estimated_loss": 0.0,
    }
    text = format_status(unknown)
    check("заголовок простоя не показывается при UNKNOWN", "ОБНАРУЖЕН ПРОСТОЙ" not in text, text)
    check("состояние UNKNOWN показано честно", "НЕТ ДАННЫХ" in text, text)

    working = {
        "camera_id": 1,
        "line_status": "WORKING",
        "person_present": True,
        "downtime_seconds": 0,
        "estimated_loss": 0.0,
    }
    text = format_status(working)
    check("работающая линия — зелёный заголовок", "СИСТЕМА РАБОТАЕТ" in text, text)
    check("простоя нет", "Простой: нет" in text, text)

    stopped = {
        "camera_id": 1,
        "line_status": "STOPPED",
        "person_present": False,
        "downtime_seconds": 134,
        "estimated_loss": 1675.0,
    }
    text = format_status(stopped)
    check("остановка видна", "ОСТАНОВЛЕНА" in text, text)
    check("текущий простой показан", "2 мин 14 сек" in text, text)
    check("текущий ущерб показан", "1\u202f675 ₽" in text, text)


# --------------------------------------------------------------------------- #
# 4. Начало простоя: ущерба ещё нет
# --------------------------------------------------------------------------- #
def test_started_has_no_loss() -> None:
    print("\n4. Событие начала простоя")

    started = make_event(
        7,
        event_type="DOWNTIME_STARTED",
        end_time="",
        duration_seconds=0,
        estimated_loss=0.0,
    )
    text = format_event(started)

    check("заголовок о простое", "ОБНАРУЖЕН ПРОСТОЙ" in text, text)
    check("нулевой ущерб НЕ показывается как «0 ₽»", "₽" not in text, text)
    check("завершение присутствует", "ПРОСТОЙ ЗАВЕРШЁН" in format_event(make_event(7)), "")

    try:
        format_event(make_event(1, event_type="ВЗРЫВ"))
        check("неизвестный тип события поднимает ошибку", False)
    except ValueError:
        check("неизвестный тип события поднимает ошибку", True)


# --------------------------------------------------------------------------- #
# 5. История
# --------------------------------------------------------------------------- #
def test_history() -> None:
    print("\n5. История событий")

    check("пустая история", "пока нет" in format_history([]), format_history([]))

    events = [make_event(3), make_event(2, event_type="DOWNTIME_STARTED", end_time="", duration_seconds=0, estimated_loss=0.0)]
    text = format_history(events)
    check("завершённый простой в истории", "простой 5 мин" in text, text)
    check("начало простоя в истории", "простой начался" in text, text)

    broken = [make_event(9, event_type="ВЗРЫВ"), make_event(8)]
    text = format_history(broken)
    check("неизвестный тип не ломает всю выдачу", "событие: ВЗРЫВ" in text and "простой 5 мин" in text, text)


# --------------------------------------------------------------------------- #
# 6. Курсор: порядок, дубли, первый запуск
# --------------------------------------------------------------------------- #
def run_monitor(client, handler, cursor=None, limit=0) -> EventMonitor:
    monitor = EventMonitor(client=client, handler=handler, poll_seconds=0.01, cursor=cursor)
    if limit:
        monitor.cursor = limit
    return monitor


def test_cursor() -> None:
    print("\n6. Курсор событий")

    received: list[dict] = []

    async def handler(event: dict) -> bool:
        received.append(event)
        return True

    # --- первый запуск: история пропускается ---
    client = FakeClient(events=[make_event(10), make_event(9)])
    monitor = run_monitor(client, handler)
    asyncio.get_event_loop().run_until_complete(monitor.poll_once())
    check("первый запуск: событий нет", received == [], str(received))
    check("первый запуск: курсор на конец базы", monitor.cursor == 10, str(monitor.cursor))
    check("первый запуск: спрашиваем без фильтра типа", client.calls[0][1].get("event_type") is None, str(client.calls[0]))

    # --- порядок: снизу вверх ---
    received.clear()
    client = FakeClient(events=[make_event(13), make_event(12), make_event(11)])
    monitor = run_monitor(client, handler, cursor=10)
    asyncio.get_event_loop().run_until_complete(monitor.poll_once())
    order = [e["id"] for e in received]
    check("события идут от старых к новым", order == [11, 12, 13], str(order))

    # --- без повторов ---
    received.clear()
    asyncio.get_event_loop().run_until_complete(monitor.poll_once())
    check("повторный опрос не присылает старые", received == [], str(received))
    check("курсор не уехал назад", monitor.cursor == 13, str(monitor.cursor))

    # --- сбой отправки: событие не теряется ---
    fail_once = {"count": 0}
    received.clear()

    async def flaky(event: dict) -> bool:
        received.append(event)
        fail_once["count"] += 1
        return fail_once["count"] != 1  # первый раз «не отправили»

    client = FakeClient(events=[make_event(21), make_event(20)])
    monitor = run_monitor(client, flaky, cursor=19)
    asyncio.get_event_loop().run_until_complete(monitor.poll_once())
    check("при сбое курсор не двигается", monitor.cursor == 19, str(monitor.cursor))
    check("при сбое обработано только одно событие", [e["id"] for e in received] == [20], str(received))

    # --- повторная попытка: событие доставляется первым, затем остальные ---
    received.clear()
    asyncio.get_event_loop().run_until_complete(monitor.poll_once())
    check(
        "недоставленное событие приходит снова и идёт первым",
        [e["id"] for e in received] == [20, 21],
        str(received),
    )
    check("после успеха курсор двинулся", monitor.cursor == 21, str(monitor.cursor))

    # --- обрыв связи не роняет цикл ---
    received.clear()
    client = FakeClient(events=[make_event(30)], available=False)
    monitor = run_monitor(client, handler, cursor=29)
    result = asyncio.get_event_loop().run_until_complete(monitor.poll_once())
    check("обрыв связи не считается событием", result == 0, str(result))
    check("при обрыве ничего не отправлено", received == [], str(received))
    check("курсор при обрыве не сдвинулся", monitor.cursor == 29, str(monitor.cursor))

    # --- пустая база ---
    client = FakeClient(events=[])
    monitor = run_monitor(client, handler)
    asyncio.get_event_loop().run_until_complete(monitor.poll_once())
    check("пустая база: курсор 0", monitor.cursor == 0, str(monitor.cursor))


# --------------------------------------------------------------------------- #
# 7. Курсор на диске
# --------------------------------------------------------------------------- #
def test_cursor_file() -> None:
    print("\n7. Курсор на диске")
    from app.telegram.bot import _load_cursor, _save_cursor

    path = Path(__file__).parent / ".tg_cursor_test.json"
    try:
        check("нет файла = первый запуск", _load_cursor(path) is None)

        _save_cursor(path, 42)
        check("курсор сохранён", _load_cursor(path) == 42, str(_load_cursor(path)))

        path.write_text("{битый json", encoding="utf-8")
        check("битый файл = первый запуск, а не падение", _load_cursor(path) is None)
    finally:
        if path.exists():
            path.unlink()


def main() -> int:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        test_formatting()
        test_loss_not_recalculated()
        test_unknown_status()
        test_started_has_no_loss()
        test_history()
        test_cursor()
        test_cursor_file()
    finally:
        loop.close()

    print(f"\n{'=' * 52}")
    print(f"Пройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    if FAILED:
        print("\nПровалено:")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
