"""Подключение к SQLite и создание схемы (Этап 5 — Database).

Используется стандартный `sqlite3` — отдельная зависимость ради
одной базы не нужна.

Особенности, важные для системы видеонаблюдения:

* `WAL`-режим — запись статусов идёт постоянно, а читать их можно
  одновременно: блокировки не мешают друг другу;
* внешние ключи включены явно (`PRAGMA foreign_keys`) — в SQLite они
  выключены по умолчанию, и каскадное удаление не работало бы;
* **соединение на поток** — FastAPI выполняет обычные (не async)
  endpoints в пуле потоков, а `sqlite3` запрещает использовать
  соединение из чужого потока. Одно общее соединение падало бы с
  ошибкой на каждом запросе, поэтому у каждого потока своё;
* путь к базе берётся из настроек (`DATABASE_PATH`), по умолчанию
  `promvizor.db`.

Пример:

    with Database("promvizor.db") as db:
        repo = EventRepository(db)
        repo.save_event(event)
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.database.models import SCHEMA_STATEMENTS, SCHEMA_VERSION, Table

#: Где хранится версия схемы — служебная таблица.
META_TABLE = "schema_meta"
META_KEY = "version"

#: Имя общей базы в памяти для `:memory:`.
#: Обычный `:memory:` создаёт отдельную пустую базу в каждом потоке,
#: поэтому используется общий кэш: так все потоки видят одни данные.
_MEMORY_URI = "file:promvizor_shared?mode=memory&cache=shared"


class Database:
    """Обёртка над соединением с SQLite.

    Экземпляр можно использовать как менеджер контекста — соединения
    закроются автоматически:

        with Database() as db:
            db.execute("SELECT 1")

    Потокобезопасна: внутри держится не одно соединение, а по одному
    на поток (см. описание модуля).
    """

    def __init__(self, path: Optional[str] = None, timeout: float = 10.0) -> None:
        self.path = str(path or settings.database_path)
        self.timeout = float(timeout)

        # Приватное хранилище потока: у каждого потока своё соединение
        self._local = threading.local()
        # Реестр созданных соединений — чтобы close() закрыл их все
        self._connections: List[sqlite3.Connection] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Подключение
    # ------------------------------------------------------------------ #
    @property
    def conn(self) -> sqlite3.Connection:
        """Соединение текущего потока; при первом обращении создаётся."""
        existing = getattr(self._local, "conn", None)
        if existing is None:
            existing = self._open()
            self._local.conn = existing
        return existing

    @property
    def _conn(self) -> Optional[sqlite3.Connection]:
        """Совместимость: соединение текущего потока или None."""
        return getattr(self._local, "conn", None)

    def _open(self) -> sqlite3.Connection:
        """Создаёт соединение для текущего потока."""
        in_memory = self.path == ":memory:"
        if not in_memory:
            # Каталог создаётся один раз, а не при каждом подключении,
            # иначе потоки мешали бы друг другу записью в self.path
            resolved = Path(self.path).expanduser().resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            _MEMORY_URI if in_memory else str(Path(self.path).expanduser()),
            uri=in_memory,
            timeout=self.timeout,
            # Автокоммит. По умолчанию sqlite3 в Python сам открывает
            # транзакцию перед INSERT/UPDATE и НЕ коммитит её — данные
            # молча терялись при закрытии соединения. С isolation_level=None
            # каждое утверждение применяется сразу, а транзакции из
            # transaction() работают как обычные BEGIN/COMMIT/ROLLBACK.
            isolation_level=None,
        )
        # Строки выдаём по имени столбца — код переживает перестановку
        # столбцов в схеме
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        if self.path != ":memory:":
            # WAL надёжнее и быстрее при параллельной записи
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                # Некоторые файловые системы WAL не поддерживают —
                # это не повод падать, работаем на обычном журнале
                pass

        conn.execute("PRAGMA synchronous = NORMAL")

        with self._lock:
            self._connections.append(conn)
        return conn

    def close(self) -> None:
        """Закрывает все соединения, созданные в потоках."""
        with self._lock:
            connections, self._connections = self._connections, []

        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                # Закрытие не должно падать из-за одного соединения:
                # остальные всё равно нужно освободить
                pass

        self._local = threading.local()

    def close_current_thread(self) -> None:
        """Закрывает соединение текущего потока, не трогая остальные."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)
        try:
            conn.close()
        except sqlite3.Error:
            pass
        self._local = threading.local()

    # ------------------------------------------------------------------ #
    # Схема
    # ------------------------------------------------------------------ #
    def init_schema(self) -> None:
        """Создаёт таблицы и индексы, если их ещё нет.

        Идемпотентна: повторный вызов ничего не ломает.
        """
        with self.transaction():
            for statement in SCHEMA_STATEMENTS:
                self.conn.execute(statement)
            self._write_version(SCHEMA_VERSION)

    def schema_version(self) -> int:
        """Текущая версия схемы в базе (0 — база пустая)."""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (META_TABLE,),
        ).fetchone()
        if row is None:
            return 0

        found = self.conn.execute(
            f"SELECT value FROM {META_TABLE} WHERE key=?", (META_KEY,)
        ).fetchone()
        return int(found["value"]) if found else 0

    def _write_version(self, version: int) -> None:
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {META_TABLE} "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.conn.execute(
            f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES (?, ?)",
            (META_KEY, str(version)),
        )

    # ------------------------------------------------------------------ #
    # Выполнение запросов
    # ------------------------------------------------------------------ #
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Выполняет запрос. Коммитит автоматически (autocommit)."""
        return self.conn.execute(sql, params)

    def query_all(self, sql: str, params: tuple = ()) -> list:
        """Возвращает все строки запроса."""
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Возвращает первую строку или None."""
        return self.conn.execute(sql, params).fetchone()

    def query_value(self, sql: str, params: tuple = (), default=None):
        """Возвращает значение первого столбца первой строки."""
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row is not None else default

    # ------------------------------------------------------------------ #
    # Транзакции
    # ------------------------------------------------------------------ #
    class _Transaction:
        """Контекстный менеджер транзакции с откатом при ошибке."""

        def __init__(self, conn: sqlite3.Connection, own: bool = True) -> None:
            self._conn = conn
            # own=False для вложенного вызова: BEGIN повторно делать нельзя
            self._own = own

        def __enter__(self) -> sqlite3.Connection:
            if self._own:
                self._conn.execute("BEGIN")
            return self._conn

        def __exit__(self, exc_type, exc, tb) -> bool:
            if self._own:
                if exc_type is None:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute("ROLLBACK")
            return False  # не подавляем исключение

    def transaction(self) -> "Database._Transaction":
        """Транзакция: всё применяется разом либо откатывается.

        Вложенные вызовы безопасны: SQLite не допускает BEGIN внутри
        уже открытой транзакции, поэтому вложенный вызов присоединяется
        к внешней транзакции, а не открывает свою.
        """
        if self.conn.in_transaction:
            return Database._Transaction(self.conn, own=False)
        return Database._Transaction(self.conn, own=True)

    # ------------------------------------------------------------------ #
    # Обслуживание
    # ------------------------------------------------------------------ #
    def clear_all(self) -> None:
        """Очищает данные, но не схему. Только для тестов и демо."""
        with self.transaction():
            self.conn.execute(f"DELETE FROM {Table.DOWNTIME_EVENTS}")
            self.conn.execute(f"DELETE FROM {Table.STATUS_LOG}")
            self.conn.execute(f"DELETE FROM {Table.CAMERAS}")

    def table_counts(self) -> dict:
        """Сколько строк в каждой таблице — удобно для проверок."""
        return {
            table: self.query_value(
                f"SELECT COUNT(*) FROM {table}", default=0
            )
            for table in (Table.CAMERAS, Table.DOWNTIME_EVENTS, Table.STATUS_LOG)
        }

    # ------------------------------------------------------------------ #
    # Контекстный менеджер
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "Database":
        self.init_schema()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Database(path={self.path!r})"


__all__ = ["Database", "SCHEMA_VERSION"]