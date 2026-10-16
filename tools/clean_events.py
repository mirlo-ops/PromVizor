"""Чистка истории простоя: `python tools/clean_events.py [--apply]`.

Зачем. Простой пишется в базу как пара записей — начало и конец.
При дребезге камеры (а он есть почти везде: линия на пару секунд
показалась стоящей) за сутки набирались сотни и тысячи строк. Вкладка
«Линии» подгружала их целиком и подвисала, а бот слал уведомления о
микропаузах.

Что делает скрипт (по умолчанию — только показывает, ничего не меняет):

1. Схлопывает пары «начало + конец» в одну запись: начало удаляется,
   конец остаётся со своей длительностью и ущербом.
2. Удаляет «призрачные» открытые простои — записи о начале, у которых
   нет пары «конец». Это ровно те строки, из-за которых в интерфейсе
   простоя не закрываются. Самый свежий незакрытый простой на камеру
   сохраняется: возможно, линия стоит прямо сейчас.
3. Удаляет завершённые простои короче порога
   `DOWNTIME_MIN_RECORD_SECONDS` (по умолчанию 30 сек).
4. При желании подрезает `status_log` (снимки состояния): `--prune-status N`.

Перед правками делается резервная копия базы рядом с ней.

Примеры:

    python tools/clean_events.py                 # что будет удалено
    python tools/clean_events.py --apply         # почистить
    python tools/clean_events.py --apply --prune-status 20000
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


#: Порог «слишком короткого» простоя, если он не задан в .env
DEFAULT_MIN_SECONDS = 30
#: Сколько минут открытому простою можно быть, чтобы он считался живым
DEFAULT_KEEP_OPEN_MINUTES = 15


def _iso_utc(offset_minutes: int = 0) -> str:
    stamp = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    return stamp.replace(microsecond=0).isoformat()


def _parse_iso(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def resolve_db_path(argument: str | None) -> Path:
    """Путь к базе: аргумент → DATABASE_PATH → promvizor.db рядом с проектом."""
    if argument:
        return Path(argument).expanduser()
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_PATH="):
                raw = line.split("=", 1)[1].strip().strip("\"'")
                return (root / raw) if raw else (root / "promvizor.db")
    return Path(os.environ.get("DATABASE_PATH") or (root / "promvizor.db"))


def collect(conn: sqlite3.Connection, min_seconds: int, keep_open_minutes: int) -> dict:
    """Находит строки к удалению. Ничего не меняет."""
    started = conn.execute(
        "SELECT id, camera_id, start_time, created_at FROM downtime_events "
        "WHERE event = 'DOWNTIME_STARTED'"
    ).fetchall()
    finished = conn.execute(
        "SELECT id, camera_id, start_time, duration_seconds FROM downtime_events "
        "WHERE event = 'DOWNTIME_FINISHED'"
    ).fetchall()

    # Ключ пары: камера + метка начала простоя («ЧЧ:ММ» в минутах).
    finished_keys: dict[tuple, list[int]] = {}
    for row in finished:
        finished_keys.setdefault((int(row[1]), str(row[2])), []).append(int(row[0]))

    newest_open: dict[int, tuple[int, str]] = {}
    for row in started:
        key = (int(row[1]), str(row[2]))
        current = newest_open.get(int(row[1]))
        created = str(row[3] or "")
        if current is None or created > current[1]:
            newest_open[int(row[1])] = (int(row[0]), created)

    paired: list[int] = []
    phantom: list[int] = []
    keep_fresh = _iso_utc(-keep_open_minutes)

    for row in started:
        row_id, camera_id, start_time, created_at = int(row[0]), int(row[1]), str(row[2]), str(row[3] or "")
        if finished_keys.get((camera_id, start_time)):
            paired.append(row_id)
            continue
        # Пары нет — это либо живой простой, либо «призрак».
        is_latest = newest_open.get(camera_id, (None, ""))[0] == row_id
        if is_latest and created_at >= keep_fresh:
            continue
        phantom.append(row_id)

    short = [
        int(row[0])
        for row in finished
        if min_seconds > 0 and int(row[3] or 0) < min_seconds
    ]

    return {
        "total": int(conn.execute("SELECT COUNT(*) FROM downtime_events").fetchone()[0]),
        "paired_starts": paired,
        "phantom_starts": phantom,
        "short_finished": short,
    }


def backup(path: Path) -> Path:
    """Копия базы рядом с оригиналом."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Чистка истории простоя PromVizor")
    parser.add_argument("--db", help="путь к базе (по умолчанию DATABASE_PATH из .env)")
    parser.add_argument(
        "--min-seconds",
        type=int,
        default=int(os.environ.get("DOWNTIME_MIN_RECORD_SECONDS", DEFAULT_MIN_SECONDS)),
        help=f"короткие простои короче этого порога удаляются (0 — не удалять, "
             f"по умолчанию {DEFAULT_MIN_SECONDS})",
    )
    parser.add_argument(
        "--keep-open-minutes",
        type=int,
        default=DEFAULT_KEEP_OPEN_MINUTES,
        help="свежие открытые простоы старше этого возраста тоже считаются призраками",
    )
    parser.add_argument("--prune-status", type=int, default=0,
                        help="оставить только N свежих снимков состояния (status_log)")
    parser.add_argument("--apply", action="store_true",
                        help="внести правки (без этого флага — только отчёт)")
    args = parser.parse_args(argv)

    path = resolve_db_path(args.db)
    if not path.is_file():
        print(f"База не найдена: {path}")
        return 1

    conn = sqlite3.connect(str(path))
    try:
        plan = collect(conn, args.min_seconds, args.keep_open_minutes)
        print(f"База: {path}")
        print(f"Всего записей простоя: {plan['total']}")
        print(f"  начало, у которого есть конец (схлопывается): {len(plan['paired_starts'])}")
        print(f"  начало без конца (призраки, удаляются):        {len(plan['phantom_starts'])}")
        print(f"  простой короче {args.min_seconds} сек (удаляются):{len(plan['short_finished']):>4}")
        if args.prune_status:
            total_status = int(conn.execute("SELECT COUNT(*) FROM status_log").fetchone()[0])
            print(f"Снимков состояния: {total_status} → останется {args.prune_status}")

        if not args.apply:
            print("\nЭто был пробный запуск. Повторите с --apply, чтобы почистить.")
            return 0

        ids = plan["paired_starts"] + plan["phantom_starts"] + plan["short_finished"]
        copy = backup(path)
        print(f"\nРезервная копия: {copy}")

        conn.execute("BEGIN")
        for row_id in ids:
            conn.execute("DELETE FROM downtime_events WHERE id = ?", (int(row_id),))
        if args.prune_status:
            conn.execute(
                "DELETE FROM status_log WHERE id NOT IN ("
                "SELECT id FROM status_log ORDER BY id DESC LIMIT ?)",
                (int(args.prune_status),),
            )
        conn.commit()

        left = int(conn.execute("SELECT COUNT(*) FROM downtime_events").fetchone()[0])
        print(f"Удалено записей: {len(ids)}. Осталось простоя: {left}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())