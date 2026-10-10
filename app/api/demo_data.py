"""Демонстрационные данные для API (Этап 6).

Зачем это нужно. Пока нет DEMO-роликов (Этап 9 ждёт файлы от Lev),
Dashboard и Telegram показывать нечего: база пустая, все графики
нулевые. Чтобы интерфейс можно было проверить уже сейчас, API умеет
наполнить базу правдоподобными примерами — по запросу, а не молча.

Как это устроено. Данные генерируются из фиксированного зерна, поэтому
одна и та же последовательность всегда даёт один и тот же результат:
скриншоты и расчёты воспроизводятся, а демонстрация выглядит
одинаково на любой машине.

Главное правило: **формула ущерба не переписывается и не подгоняется**.
Суммы считаются той же `calculate_loss()`, что и в рабочем режиме —
подобраны только длительности. Иначе демонстрация показывала бы
цифры, которых система никогда не выдаёт, и проверять по ним
интерфейс было бы бессмысленно.

Каждое событие возвращается вместе с меткой времени записи.
Без неё вся история оказалась бы с меткой «сейчас», и фильтр по
датам в Dashboard не нашёл бы ничего.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from app.core.config import settings
from app.core.types import (
    DowntimeEvent,
    EventType,
    LineStatus,
    SystemStatus,
    calculate_loss,
)

#: Зерно генератора. Фиксированное — демо всегда одинаковое.
SEED = 20261010

#: Камера, к которой относятся примеры.
DEMO_CAMERA_ID = 1
DEMO_CAMERA_NAME = "Камера №1"

#: Сколько дней истории создаём.
DAYS_BACK = 7

#: Когда линия обычно работает: с 7:00 до 22:00.
_WORKDAY_START_HOUR = 7
_WORKDAY_END_HOUR = 22

#: Длительность простоя, минуты. Короткие остановки — типичная картина
#: для конвейера: линия стоит несколько минут из-за заминки, а не часами.
_MIN_DOWNTIME_MINUTES = 4
_MAX_DOWNTIME_MINUTES = 40

#: Простоев в сутки: от одного до трёх.
_MIN_DOWNTIMES_PER_DAY = 1
_MAX_DOWNTIMES_PER_DAY = 3

#: Доля времени, когда линия стоит. Примерно 8% суток — правдоподобно:
#: если в демо линия простаивает половину времени, на неё не похоже.
_DOWNTIME_SHARE = 0.08

#: Событие + метка времени его записи в базе (ISO 8601 UTC).
Seeded = Tuple[DowntimeEvent, str]


def _stamp(moment: datetime) -> str:
    """Метка времени записи: ISO 8601 в UTC, как хранит база."""
    return moment.replace(microsecond=0).isoformat()


def _pick_downtimes(rng: random.Random) -> List[Tuple[datetime, datetime]]:
    """Выбирает интервалы простоя на день.

    Интервалы короткие (4–40 минут) и не пересекаются: иначе простой
    на полсмены выглядел бы как остановка завода, а не как заминки
    конвейера. Возвращает пары «момент внутри суток начала, конец».
    """
    shift_minutes = (_WORKDAY_END_HOUR - _WORKDAY_START_HOUR) * 60
    # Сколько минут суммарно линия может простаивать за смену
    budget = int(shift_minutes * _DOWNTIME_SHARE)

    count = rng.randint(_MIN_DOWNTIMES_PER_DAY, _MAX_DOWNTIMES_PER_DAY)
    spans = [
        rng.randint(_MIN_DOWNTIME_MINUTES, _MAX_DOWNTIME_MINUTES) for _ in range(count)
    ]

    # Ужимаем самый длинный простой, пока не влезем в бюджет
    while sum(spans) > budget:
        longest = max(spans)
        if longest <= _MIN_DOWNTIME_MINUTES:
            break
        spans[spans.index(longest)] = longest - 5

    # Раскидываем простоя по смене, оставляя промежутки рабочего времени
    slack = shift_minutes - sum(spans)
    gaps = sorted(rng.randint(0, slack) for _ in range(count + 1))

    intervals: List[Tuple[datetime, datetime]] = []
    cursor = _WORKDAY_START_HOUR * 60 + gaps[0]
    for index, span in enumerate(spans):
        start = datetime(2026, 1, 1) + timedelta(minutes=cursor)
        intervals.append((start, start + timedelta(minutes=span)))
        cursor += span + (gaps[index + 1] - gaps[index])
    return intervals


def generate_events(
    days_back: int = DAYS_BACK,
    cost_per_minute: float | None = None,
) -> List[Seeded]:
    """Правдоподобная история простоя за последние дни.

    Каждый простой даёт пару событий — `DOWNTIME_STARTED` в начале и
    `DOWNTIME_FINISHED` в конце, ровно как их порождает Event Engine.
    """
    rate = settings.cost_per_minute if cost_per_minute is None else cost_per_minute
    rng = random.Random(SEED)
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    events: List[Seeded] = []
    # Дни идут от старого к новому: запись в базу идёт по порядку,
    # и `recent_events` сортирует по id. Если вставлять наоборот,
    # история в Dashboard окажется перевёрнутой.
    for day_offset in reversed(range(days_back)):
        day = today - timedelta(days=day_offset)
        for start_in_day, end_in_day in _pick_downtimes(rng):
            # Смещение внутри суток прибавляем к началу дня
            start = day + timedelta(
                hours=start_in_day.hour, minutes=start_in_day.minute
            )
            end = day + timedelta(hours=end_in_day.hour, minutes=end_in_day.minute)

            seconds = int((end - start).total_seconds())
            if seconds <= 0:
                continue

            # Сегодняшний день наполнен на целые сутки, но «сейчас»
            # может быть утро: интервал, до которого линия ещё не
            # дошла, попал бы в базу с меткой времени из будущего и
            # показался бы в статистике за сегодня как случившееся.
            if end > now:
                continue

            events.append(
                (
                    DowntimeEvent(
                        camera_id=DEMO_CAMERA_ID,
                        event=EventType.DOWNTIME_STARTED,
                        start_time=start.strftime("%H:%M"),
                        end_time="",
                        duration_seconds=0,
                        estimated_loss=0.0,
                    ),
                    _stamp(start),
                )
            )
            events.append(
                (
                    DowntimeEvent(
                        camera_id=DEMO_CAMERA_ID,
                        event=EventType.DOWNTIME_FINISHED,
                        start_time=start.strftime("%H:%M"),
                        end_time=end.strftime("%H:%M"),
                        duration_seconds=seconds,
                        estimated_loss=calculate_loss(seconds, rate),
                    ),
                    _stamp(end),
                )
            )
    return events


def generate_statuses(
    cost_per_minute: float | None = None,
) -> List[Seeded]:
    """Снимки состояния за последние сутки.

    Первый снимок — «прямо сейчас»: линия стоит, рабочего нет,
    простой идёт. Так Dashboard сразу показывает живой пример, а
    не пустые нули.
    """
    rate = settings.cost_per_minute if cost_per_minute is None else cost_per_minute
    rng = random.Random(SEED + 1)
    now = datetime.now(timezone.utc)

    statuses: List[Seeded] = []
    # От старого к новому — см. комментарий в generate_events
    minutes_ago = 24 * 4
    while minutes_ago > 0:
        step = rng.randint(3, 9)
        minutes_ago -= step
        moment = now - timedelta(minutes=minutes_ago)

        if rng.random() < _DOWNTIME_SHARE:
            # Линия стоит: рабочий то есть, то нет — так выглядит простой
            seconds = rng.randint(60, 600)
            status = SystemStatus(
                camera_id=DEMO_CAMERA_ID,
                line_status=LineStatus.STOPPED,
                person_present=rng.random() < 0.3,
                downtime_seconds=seconds,
                estimated_loss=calculate_loss(seconds, rate),
            )
        else:
            status = SystemStatus(
                camera_id=DEMO_CAMERA_ID,
                line_status=LineStatus.WORKING,
                person_present=True,
                downtime_seconds=0,
                estimated_loss=0.0,
            )
        statuses.append((status, _stamp(moment)))

    # Последним идёт снимок «прямо сейчас»: 134 секунды простоя —
    # пример из Handoff (02:14 и 1 675 ₽). Dashboard сразу показывает
    # живое состояние, а не нули.
    current_downtime = 134
    statuses.append(
        (
            SystemStatus(
                camera_id=DEMO_CAMERA_ID,
                line_status=LineStatus.STOPPED,
                person_present=False,
                downtime_seconds=current_downtime,
                estimated_loss=calculate_loss(current_downtime, rate),
            ),
            _stamp(now),
        )
    )

    return statuses


def seed(
    repo,
    days_back: int = DAYS_BACK,
    replace: bool = False,
) -> dict:
    """Наполняет базу демонстрационными данными.

    `replace=True` сначала очищает базу. По умолчанию ничего не
    удаляется: если данные уже есть (например, приехали из реального
    видео), примеры добавляются к ним, а не затирают их.
    """
    if replace:
        repo.db.clear_all()

    repo.ensure_camera(DEMO_CAMERA_ID, DEMO_CAMERA_NAME, source_type="demo", is_demo=True)

    seeded_events = generate_events(days_back=days_back)
    seeded_statuses = generate_statuses()

    with repo.db.transaction():
        for event, created_at in seeded_events:
            repo.save_event(event, created_at=created_at)
        for status, created_at in seeded_statuses:
            repo.save_status(status, created_at=created_at)

    return {
        "camera_id": DEMO_CAMERA_ID,
        "events_created": len(seeded_events),
        "statuses_created": len(seeded_statuses),
        "days_back": days_back,
        "note": "Демонстрационные данные. Формула ущерба та же, что и в рабочем режиме.",
    }


__all__ = [
    "seed",
    "generate_events",
    "generate_statuses",
    "SEED",
    "DEMO_CAMERA_ID",
    "DEMO_CAMERA_NAME",
    "Seeded",
]