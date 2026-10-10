"""Проверка примера из TELEGRAM-GASUN.md против живого API.

Запуск (сервер должен быть поднят на BASE):

    .venv/bin/python tests/test_telegram_example.py http://127.0.0.1:8060

Скрипт НЕ отправляет сообщения в Telegram: вместо этого печатает,
что было бы отправлено. Задача — убедиться, что пример из документа
разбирает живой ответ и ведёт курсор правильно.
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def get_json(path: str, params: dict | None = None) -> dict:
    url = BASE + path
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


# ---- ровно те функции, что в документе ----
def fetch_new_events(last_id):
    try:
        data = get_json("/api/events", {"limit": 200, "event_type": "DOWNTIME_FINISHED"})
    except Exception as exc:  # noqa: BLE001
        print("не удалось прочитать события:", exc)
        return [], last_id

    if last_id is None:
        # Первый запуск: курсор ставим по последнему событию БЕЗ
        # фильтра. При фильтре event_type поле total — это число
        # отфильтрованных событий, а не максимальный id.
        probe = get_json("/api/events", {"limit": 1})
        items = probe.get("items") or []
        last_id = items[0]["id"] if items else 0
        print(f"первый запуск: курсор = {last_id} (история пропущена)")
        return [], last_id

    fresh = []
    # items приходят новыми сверху — обрабатываем от старых к новым
    for event in reversed(data.get("items", [])):
        if event["id"] > last_id:
            fresh.append(event)
            last_id = event["id"]
    return fresh, last_id


def format_downtime(event) -> str:
    minutes = event["duration_seconds"] / 60
    return (
        "🔴 <b>Простой на линии</b>\n"
        f"Камера №{event['camera_id']}\n"
        f"Время: {event['start_time']} – {event['end_time']}\n"
        f"Длительность: {event['duration_seconds']} сек "
        f"({minutes:.1f} мин)\n"
        f"Ущерб: {event['estimated_loss']:,.2f} ₽".replace(",", " ")
    )


def main() -> int:
    print(f"Проверка примера из TELEGRAM-GASUN.md на {BASE}\n")

    status = get_json("/api/status")
    print("1. /api/status:", status)
    assert "line_status" in status
    assert "estimated_loss" in status

    # Первый запуск: история пропускается
    events, cursor = fetch_new_events(None)
    assert events == [], "при первом запуске история не должна приходить"
    print(f"2. первый запуск: курсор = {cursor}, событий 0 (история пропущена)")

    # Второй запуск: прийти могут только события строго новее курсора.
    # Конвейер за это время мог зафиксировать новый простой — это
    # нормально. Важно, чтобы старые не пришли повторно.
    events, cursor2 = fetch_new_events(cursor)
    for event in events:
        assert event["id"] > cursor, f"повторно пришло событие {event['id']}"
    assert cursor2 >= cursor, "курсор уехал назад"
    print(f"3. повторный опрос: новых событий {len(events)}, "
          f"дублей нет, курсор {cursor} → {cursor2}")

    # Проверяем форматирование на реальном событии из базы
    raw = get_json("/api/events", {"limit": 1, "event_type": "DOWNTIME_FINISHED"})
    if raw.get("items"):
        event = raw["items"][0]
        text = format_downtime(event)
        print("\n4. Пример сообщения (событие с id=%s):" % event["id"])
        print("---")
        print(text)
        print("---")
        # Ущерб в тексте совпадает с готовым значением backend'а
        assert str(int(event["estimated_loss"])) in text.replace(" ", ""), (
            "сумма в сообщении разошлась с estimated_loss"
        )
        assert str(event["duration_seconds"]) in text
    else:
        print("\n4. Завершённых простоев пока нет — формат сообщения не проверялся")

    print("\nПример работает: поля совпадают, дублей нет, курсор устойчив.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
