"""Операции с данными (Этап 5 — Database).

Репозиторий — единственное место, где пишутся SQL-запросы.
Он работает с контрактными объектами (`SystemStatus`,
`DowntimeEvent`, `Statistics`) и не отдаёт наружу строки sqlite3.

Пример:

    with Database(":memory:") as db:
        repo = EventRepository(db)
        repo.ensure_camera(1, "Камера №1")
        event_id = repo.save_event(downtime_event)
        history = repo.recent_events(limit=20)
        stats = repo.statistics()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence

from app.core.types import (
    DowntimeEvent,
    EventType,
    LineStatus,
    Statistics,
    SystemStatus,
)
from app.database.db import Database
from app.database.models import (
    CameraRow,
    Column,
    EventRecord,
    Table,
    build_statistics,
    row_to_downtime_event,
    row_to_event_record,
    row_to_system_status,
)


def utcnow_iso() -> str:
    """Метка времени записи в UTC, ISO 8601.

    Хранится в БД всегда в UTC: локальное время процесса может
    меняться (часы, переезд, перевод на другую зону), а история
    простоя от этого меняться не должна.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class EventRepository:
    """Чтение и запись событий простоя и статусов."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ #
    # Камеры
    # ------------------------------------------------------------------ #
    def ensure_camera(
        self,
        camera_id: int,
        name: str = "",
        source_type: str = "demo",
        is_demo: bool = False,
    ) -> int:
        """Регистрирует камеру, если её ещё нет. Возвращает её id.

        Вызывается при старте обработки: камера с данным id должна
        существовать, иначе запись события нарушит внешний ключ.
        """
        existing = self.db.query_value(
            f"SELECT {Column.ID} FROM {Table.CAMERAS} WHERE {Column.ID} = ?",
            (int(camera_id),),
        )
        if existing is not None:
            return int(existing)

        self.db.execute(
            f"INSERT INTO {Table.CAMERAS} "
            f"({Column.ID}, {Column.NAME}, {Column.SOURCE_TYPE}, "
            f" {Column.IS_DEMO}, {Column.CREATED_AT}) "
            f"VALUES (?, ?, ?, ?, ?)",
            (
                int(camera_id),
                name or f"КАМ {camera_id}",
                source_type,
                int(bool(is_demo)),
                utcnow_iso(),
            ),
        )
        return int(camera_id)

    def get_camera(self, camera_id: int) -> Optional[CameraRow]:
        """Камера по id или None."""
        row = self.db.query_one(
            f"SELECT * FROM {Table.CAMERAS} WHERE {Column.ID} = ?", (int(camera_id),)
        )
        return CameraRow.from_row(row) if row else None

    def list_cameras(self) -> List[CameraRow]:
        """Все зарегистрированные камеры."""
        rows = self.db.query_all(f"SELECT * FROM {Table.CAMERAS} ORDER BY {Column.ID}")
        return [CameraRow.from_row(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Запись событий простоя
    # ------------------------------------------------------------------ #
    def save_event(self, event: DowntimeEvent, created_at: Optional[str] = None) -> int:
        """Сохраняет контрактное событие простоя. Возвращает id записи."""
        cursor = self.db.execute(
            f"INSERT INTO {Table.DOWNTIME_EVENTS} "
            f"({Column.CAMERA_ID}, {Column.EVENT}, {Column.START_TIME}, "
            f" {Column.END_TIME}, {Column.DURATION_SECONDS}, "
            f" {Column.ESTIMATED_LOSS}, {Column.CREATED_AT}) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                int(event.camera_id),
                str(event.event),
                str(event.start_time),
                str(event.end_time or ""),
                int(event.duration_seconds),
                float(event.estimated_loss),
                created_at or utcnow_iso(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def save_events(self, events: Iterable[DowntimeEvent]) -> List[int]:
        """Сохраняет несколько событий в одной транзакции.

        Часть успешной записи или вся: при ошибке откатывается всё,
        чтобы в истории не осталось половины события.
        """
        ids: List[int] = []
        with self.db.transaction():
            for event in events:
                cursor = self.db.execute(
                    f"INSERT INTO {Table.DOWNTIME_EVENTS} "
                    f"({Column.CAMERA_ID}, {Column.EVENT}, {Column.START_TIME}, "
                    f" {Column.END_TIME}, {Column.DURATION_SECONDS}, "
                    f" {Column.ESTIMATED_LOSS}, {Column.CREATED_AT}) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(event.camera_id),
                        str(event.event),
                        str(event.start_time),
                        str(event.end_time or ""),
                        int(event.duration_seconds),
                        float(event.estimated_loss),
                        utcnow_iso(),
                    ),
                )
                ids.append(int(cursor.lastrowid or 0))
        return ids

    # ------------------------------------------------------------------ #
    # Запись статусов
    # ------------------------------------------------------------------ #
    def save_status(
        self,
        status: SystemStatus,
        created_at: Optional[str] = None,
    ) -> int:
        """Сохраняет снимок состояния системы. Возвращает id записи."""
        cursor = self.db.execute(
            f"INSERT INTO {Table.STATUS_LOG} "
            f"({Column.CAMERA_ID}, {Column.LINE_STATUS}, {Column.PERSON_PRESENT}, "
            f" {Column.DOWNTIME_SECONDS}, {Column.ESTIMATED_LOSS}, {Column.CREATED_AT}) "
            f"VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(status.camera_id),
                str(status.line_status),
                int(bool(status.person_present)),
                int(status.downtime_seconds),
                float(status.estimated_loss),
                created_at or utcnow_iso(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def latest_status(self, camera_id: Optional[int] = None) -> Optional[SystemStatus]:
        """Последний снимок состояния — ответ для `/api/status`."""
        if camera_id is None:
            row = self.db.query_one(
                f"SELECT * FROM {Table.STATUS_LOG} ORDER BY {Column.ID} DESC LIMIT 1"
            )
        else:
            row = self.db.query_one(
                f"SELECT * FROM {Table.STATUS_LOG} WHERE {Column.CAMERA_ID} = ? "
                f"ORDER BY {Column.ID} DESC LIMIT 1",
                (int(camera_id),),
            )
        return row_to_system_status(row) if row else None

    def status_history(
        self,
        camera_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[SystemStatus]:
        """История снимков состояния, свежие сверху."""
        limit = _safe_limit(limit)
        if camera_id is None:
            rows = self.db.query_all(
                f"SELECT * FROM {Table.STATUS_LOG} "
                f"ORDER BY {Column.ID} DESC LIMIT ?", (limit,)
            )
        else:
            rows = self.db.query_all(
                f"SELECT * FROM {Table.STATUS_LOG} WHERE {Column.CAMERA_ID} = ? "
                f"ORDER BY {Column.ID} DESC LIMIT ?",
                (int(camera_id), limit),
            )
        return [row_to_system_status(r) for r in rows]

    # ------------------------------------------------------------------ #
    # История простоя
    # ------------------------------------------------------------------ #
    def recent_events(
        self,
        limit: int = 50,
        camera_id: Optional[int] = None,
        event_type: Optional[str] = None,
    ) -> List[DowntimeEvent]:
        """Последние события простоя, свежие сверху.

        Возвращаются оба типа событий: и DOWNTIME_STARTED, и
        DOWNTIME_FINISHED. Начатое простой нужно видеть в ленте —
        оно показывает, что линия стоит прямо сейчас. Длительность и
        ущерб у него нулевые, поскольку простой ещё не завершён.
        """
        limit = _safe_limit(limit)
        where: List[str] = []
        params: List[object] = []

        if camera_id is not None:
            where.append(f"{Column.CAMERA_ID} = ?")
            params.append(int(camera_id))
        if event_type is not None:
            where.append(f"{Column.EVENT} = ?")
            params.append(event_type)

        params.append(limit)
        sql = f"SELECT * FROM {Table.DOWNTIME_EVENTS}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {Column.ID} DESC LIMIT ?"

        return [row_to_downtime_event(r) for r in self.db.query_all(sql, tuple(params))]

    def get_event(self, event_id: int) -> Optional[DowntimeEvent]:
        """Событие по id или None."""
        row = self.db.query_one(
            f"SELECT * FROM {Table.DOWNTIME_EVENTS} WHERE {Column.ID} = ?", (int(event_id),)
        )
        return row_to_downtime_event(row) if row else None

    def get_event_with_time(self, event_id: int) -> Optional[EventRecord]:
        """Событие по id вместе с меткой времени записи или None."""
        row = self.db.query_one(
            f"SELECT * FROM {Table.DOWNTIME_EVENTS} WHERE {Column.ID} = ?", (int(event_id),)
        )
        return row_to_event_record(row) if row else None

    def count_events(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        camera_id: Optional[int] = None,
        event_type: Optional[str] = None,
    ) -> int:
        """Сколько событий подходит под фильтр — без учёта limit.

        Нужно API, чтобы показать «показано 20 из 143»: по ограниченной
        выборке это число узнать нельзя, а пользователю полезно.
        """
        where: List[str] = []
        params: List[object] = []

        if since:
            where.append(f"{Column.CREATED_AT} >= ?")
            params.append(since)
        if until:
            where.append(f"{Column.CREATED_AT} <= ?")
            params.append(until)
        if camera_id is not None:
            where.append(f"{Column.CAMERA_ID} = ?")
            params.append(int(camera_id))
        if event_type is not None:
            where.append(f"{Column.EVENT} = ?")
            params.append(event_type)

        sql = f"SELECT COUNT(*) FROM {Table.DOWNTIME_EVENTS}"
        if where:
            sql += " WHERE " + " AND ".join(where)

        return int(self.db.query_value(sql, tuple(params), default=0))

    def events_between(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        camera_id: Optional[int] = None,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[DowntimeEvent]:
        """События за период по метке времени записи (ISO, UTC)."""
        where: List[str] = []
        params: List[object] = []

        if since:
            where.append(f"{Column.CREATED_AT} >= ?")
            params.append(since)
        if until:
            where.append(f"{Column.CREATED_AT} <= ?")
            params.append(until)
        if camera_id is not None:
            where.append(f"{Column.CAMERA_ID} = ?")
            params.append(int(camera_id))
        if event_type is not None:
            where.append(f"{Column.EVENT} = ?")
            params.append(event_type)

        params.append(_safe_limit(limit, maximum=100_000))
        sql = f"SELECT * FROM {Table.DOWNTIME_EVENTS}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {Column.ID} ASC LIMIT ?"

        return [row_to_downtime_event(r) for r in self.db.query_all(sql, tuple(params))]

    def events_with_time(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        camera_id: Optional[int] = None,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[EventRecord]:
        """События за период вместе с меткой времени записи.

        Нужно интерфейсу: в контракте только «ЧЧ:ММ», а сгруппировать
        события по суткам (отчёт за месяц, таймлайн за день) по часам
        и минутам невозможно.
        """
        where: List[str] = []
        params: List[object] = []

        if since:
            where.append(f"{Column.CREATED_AT} >= ?")
            params.append(since)
        if until:
            where.append(f"{Column.CREATED_AT} <= ?")
            params.append(until)
        if camera_id is not None:
            where.append(f"{Column.CAMERA_ID} = ?")
            params.append(int(camera_id))
        if event_type is not None:
            where.append(f"{Column.EVENT} = ?")
            params.append(event_type)

        params.append(_safe_limit(limit, maximum=100_000))
        sql = f"SELECT * FROM {Table.DOWNTIME_EVENTS}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {Column.ID} ASC LIMIT ?"

        return [row_to_event_record(r) for r in self.db.query_all(sql, tuple(params))]

    # ------------------------------------------------------------------ #
    # Агрегаты
    # ------------------------------------------------------------------ #
    def statistics(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        camera_id: Optional[int] = None,
    ) -> Statistics:
        """Итоги за период — ответ для `/api/statistics`.

        Считаются только завершённые простоя: у начатого ещё нет
        ни длительности, ни ущерба, и его включение занижало бы
        средние значения.
        """
        where = [f"{Column.EVENT} = ?"]
        params: List[object] = [EventType.DOWNTIME_FINISHED]

        if since:
            where.append(f"{Column.CREATED_AT} >= ?")
            params.append(since)
        if until:
            where.append(f"{Column.CREATED_AT} <= ?")
            params.append(until)
        if camera_id is not None:
            where.append(f"{Column.CAMERA_ID} = ?")
            params.append(int(camera_id))

        clause = " AND ".join(where)
        row = self.db.query_one(
            f"SELECT COUNT(*) AS events_count, "
            f"COALESCE(SUM({Column.DURATION_SECONDS}), 0) AS total_seconds, "
            f"COALESCE(SUM({Column.ESTIMATED_LOSS}), 0.0) AS total_loss "
            f"FROM {Table.DOWNTIME_EVENTS} WHERE {clause}",
            tuple(params),
        )

        return build_statistics(
            total_seconds=row["total_seconds"] if row else 0,
            total_loss=row["total_loss"] if row else 0.0,
            events_count=row["events_count"] if row else 0,
        )

    def downtime_by_camera(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> dict:
        """Суммарный простой и ущерб по камерам."""
        where = [f"{Column.EVENT} = ?"]
        params: List[object] = [EventType.DOWNTIME_FINISHED]
        if since:
            where.append(f"{Column.CREATED_AT} >= ?")
            params.append(since)
        if until:
            where.append(f"{Column.CREATED_AT} <= ?")
            params.append(until)

        rows = self.db.query_all(
            f"SELECT {Column.CAMERA_ID} AS camera_id, "
            f"COALESCE(SUM({Column.DURATION_SECONDS}), 0) AS total_seconds, "
            f"COALESCE(SUM({Column.ESTIMATED_LOSS}), 0.0) AS total_loss, "
            f"COUNT(*) AS events_count "
            f"FROM {Table.DOWNTIME_EVENTS} WHERE {' AND '.join(where)} "
            f"GROUP BY {Column.CAMERA_ID} ORDER BY {Column.CAMERA_ID}",
            tuple(params),
        )
        return {
            int(r["camera_id"]): {
                "total_downtime_seconds": int(r["total_seconds"]),
                "total_loss": round(float(r["total_loss"]), 2),
                "events_count": int(r["events_count"]),
            }
            for r in rows
        }

    def delete_event(self, event_id: int) -> bool:
        """Удаляет событие. True, если строка была."""
        cursor = self.db.execute(
            f"DELETE FROM {Table.DOWNTIME_EVENTS} WHERE {Column.ID} = ?", (int(event_id),)
        )
        return bool(cursor.rowcount)

    # ------------------------------------------------------------------ #
    # Целостность
    # ------------------------------------------------------------------ #
    def prune_status_log(self, keep_last: int = 10000) -> int:
        """Оставляет N последних снимков состояния, старые удаляет.

        Снимки состояния пишутся постоянно и быстро занимают место,
        а нужны только для отладки и графиков. События простоя
        не трогаются — это история, её храним целиком.
        """
        cursor = self.db.execute(
            f"DELETE FROM {Table.STATUS_LOG} WHERE {Column.ID} NOT IN ("
            f"SELECT {Column.ID} FROM {Table.STATUS_LOG} "
            f"ORDER BY {Column.ID} DESC LIMIT ?)",
            (int(keep_last),),
        )
        return int(cursor.rowcount or 0)


def _safe_limit(limit: int, maximum: int = 10_000) -> int:
    """Ограничивает limit разумным диапазоном.

    Отрицательный LIMIT в SQLite — это не «все строки», а «без
    ограничения снизу», что для нашего случая опасно.
    """
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 50
    return max(1, min(value, maximum))


__all__ = ["EventRepository", "utcnow_iso", "LineStatus", "Statistics"]