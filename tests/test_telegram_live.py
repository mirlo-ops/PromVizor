"""Проверка бота против ЖИВОГО backend'а.

Скрипт ничего не отправляет в Telegram: вместо этого печатает, что
бот увидел бы. Нужен, чтобы убедиться, что клиент и форматтер
разбирают настоящий ответ API, а не только заглушку из
`tests/test_telegram.py`.

Запуск (сервер должен быть поднят):

    .venv/bin/python -m app.api.main
    .venv/bin/python tests/test_telegram_live.py [http://127.0.0.1:8000]

Дополнительно можно проверить сквозной сценарий с живым циклом
курсора: скрипт подставит события в базу через backend и убедится,
что бот отправит их ровно один раз и в правильном порядке.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.telegram.client import BackendUnavailable, PromVizorClient  # noqa: E402
from app.telegram.formatter import (  # noqa: E402
    format_history,
    format_statistics,
    format_status,
)
from app.telegram.monitor import EventMonitor  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"\n         {detail}" if not ok and detail else ""))


async def main() -> int:
    client = PromVizorClient(BASE)

    # --- 1. Текущее состояние ---
    print(f"\nБот против живого backend'а: {BASE}\n")
    print("1. /api/status — что покажет /status")

    status = await client.get_status()
    text = format_status(status)
    print("---")
    print(text)
    print("---")

    check("состояние прочитано", "line_status" in status)
    check("заголовок соответствует состоянию",
          (status["line_status"] == "WORKING" and "СИСТЕМА РАБОТАЕТ" in text)
          or (status["line_status"] == "STOPPED" and "ОСТАНОВЛЕНА" in text)
          or (status["line_status"] == "UNKNOWN" and "НЕТ ДАННЫХ" in text),
          text)
    # Сумма в тексте должна быть ровно та, что прислал backend.
    # Сравниваем распознанное число, а не подстроку: формат вывода
    # (разделитель разрядов, копейки) — вопрос оформления, а важно
    # именно то, что значение не изменилось по дороге.
    if status.get("estimated_loss"):
        shown = float(text.split("Ущерб:")[1].split("₽")[0].replace("\u202f", "").replace(" ", "").replace(",", "."))
        check("сумма взята из estimated_loss, а не посчитана",
              shown == float(status["estimated_loss"]),
              f"в тексте {shown}, прислано {status['estimated_loss']}")

    # --- 2. История ---
    print("\n2. /api/events — что покажет /history")
    events = await client.get_events(limit=5)
    text = format_history(events)
    print("---")
    print(text)
    print("---")
    check("события прочитаны", isinstance(events, list))
    check("порядок API соблюдён (новые сверху)",
          [e["id"] for e in events] == sorted([e["id"] for e in events], reverse=True),
          str([e["id"] for e in events]))

    # --- 3. Итоги ---
    print("\n3. /api/statistics — что покажет /stats")
    stats = await client.get_statistics()
    config = await client.get_config()
    text = format_statistics(stats, config.get("cost_per_minute"))
    print("---")
    print(text)
    print("---")
    check("итоги прочитаны", "events_count" in stats)

    # --- 4. Живой цикл курсора ---
    print("\n4. Курсор на живых данных")
    delivered: list[int] = []

    async def handler(event: dict) -> bool:
        delivered.append(int(event["id"]))
        return True

    # Первый запуск: история пропускается.
    monitor = EventMonitor(client=client, handler=handler, poll_seconds=0.01)
    await monitor.poll_once()
    check("первый запуск: ничего не отправлено", delivered == [], str(delivered))
    check("первый запуск: курсор на конец базы",
          monitor.cursor == max((e["id"] for e in events), default=0), str(monitor.cursor))

    # Второй запуск: новых событий ещё нет.
    await monitor.poll_once()
    check("повторный опрос не присылает старые события", delivered == [], str(delivered))

    # Подставляем события в базу через API backend'а и проверяем,
    # что бот пришлёт их ровно один раз и по порядку.
    import json
    import urllib.request

    def post(path: str, payload: dict):
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        # Пишем события напрямую в ту же базу, что читает API.
        from app.core.config import settings as _settings
        from app.database import EventRepository
        from app.database.db import Database
        from app.core.types import DowntimeEvent, EventType

        db = Database(_settings.database_path)
        db.init_schema()
        repo = EventRepository(db)
        repo.ensure_camera(1, name="Камера №1", source_type="demo", is_demo=True)

        start_cursor = monitor.cursor or 0
        repo.save_events([
            DowntimeEvent(camera_id=1, event=EventType.DOWNTIME_STARTED,
                          start_time="15:01"),
            DowntimeEvent(camera_id=1, event=EventType.DOWNTIME_FINISHED,
                          start_time="15:01", end_time="15:06",
                          duration_seconds=300, estimated_loss=3750.0),
        ])

        await monitor.poll_once()
        expected = [start_cursor + 1, start_cursor + 2]
        check("новые события пришли", delivered == expected, f"{delivered} != {expected}")
        check("порядок от старых к новым", delivered == sorted(delivered), str(delivered))

        # Повторно: дубликатов быть не должно.
        delivered.clear()
        await monitor.poll_once()
        check("повторов нет", delivered == [], str(delivered))
    finally:
        try:
            db.close()
        except Exception:
            pass

    # --- 5. Обрыв связи ---
    print("\n5. Обрыв связи")
    dead = PromVizorClient("http://127.0.0.1:1", timeout=0.5)
    try:
        await dead.get_status()
        check("недоступный backend поднимает BackendUnavailable", False)
    except BackendUnavailable:
        check("недоступный backend поднимает BackendUnavailable", True)

    sent: list[int] = []

    async def handler2(event: dict) -> bool:
        sent.append(int(event["id"]))
        return True

    m2 = EventMonitor(client=dead, handler=handler2, poll_seconds=0.01, cursor=1)
    result = await m2.poll_once()
    check("цикл переживает обрыв связи", result == 0 and sent == [], str(result))
    check("курсор не сдвинулся при обрыве", m2.cursor == 1, str(m2.cursor))

    print(f"\n{'=' * 52}")
    print(f"Пройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    if FAILED:
        print("\nПровалено:")
        for n in FAILED:
            print(f"  - {n}")
        return 1
    print("Бот корректно работает с живым backend'ом.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
