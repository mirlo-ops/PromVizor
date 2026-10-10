"""Endpoints API «ПромВизор» (Этап 6 — FastAPI, Handoff раздел 16).

API отдаёт данные сразу трём потребителям:

* Dashboard (`web/`) — текущее состояние и история;
* Telegram Bot (Gasun) — чтобы написать сообщение о простое;
* внешние интеграции.

Правило, которое проходит через весь файл: **ущерб не пересчитывается
здесь**. Он приходит из базы таким, как его посчитал backend по
`calculate_loss()`. API только отдаёт готовое число (Handoff, раздел 14).

Endpoints только читают. Запись идёт из процесса обработки видео
прямо в базу — так API остаётся тонким и его нельзя случайно
испортить внешним запросом.

Примеры:

    GET /api/status
    GET /api/events?limit=20&event_type=DOWNTIME_FINISHED
    GET /api/events/12
    GET /api/statistics?since=2026-10-01&until=2026-10-10
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.demo_data import seed as demo_seed
from app.api.schemas import (
    CameraListResponse,
    CameraResponse,
    CameraStatistics,
    ConfigResponse,
    EventListResponse,
    EventResponse,
    HealthResponse,
    StatisticsByCameraResponse,
    StatisticsResponse,
    StatusHistoryResponse,
    StatusResponse,
)
from app.core.config import settings
from app.core.types import EventType, LineStatus, SystemStatus
from app.database import EventRepository, utcnow_iso
from app.database.db import Database

router = APIRouter(prefix="/api", tags=["promvizor"])

#: 422 — запрос понятен, но параметры не проходят проверку.
#: Константа записана числом, а не именем из `starlette.status`:
#: имя переименовано в новых версиях, и импорт стал бы причиной
#: падения при обновлении библиотеки.
HTTP_422 = 422

#: 404 — такого события нет.
HTTP_404 = 404


# --------------------------------------------------------------------------- #
# Подключение к базе
# --------------------------------------------------------------------------- #
def get_database() -> Database:
    """Отдаёт подключение к базе (его же использует обработка видео).

    Соединение одно на процесс: SQLite и так работает через файл, а
    при каждом запросе открывать новое — лишние блокировки.
    Приложение закрывает соединение в `shutdown`.
    """
    if not hasattr(get_database, "_db"):
        db = Database(settings.database_path)
        db.init_schema()
        get_database._db = db
    return get_database._db


def get_repository(db: Database = Depends(get_database)) -> EventRepository:
    """Репозиторий поверх подключения — внедряется через Depends."""
    return EventRepository(db)


def close_database() -> None:
    """Закрывает соединение при остановке приложения."""
    db = getattr(get_database, "_db", None)
    if db is not None:
        db.close()
        delattr(get_database, "_db")


# --------------------------------------------------------------------------- #
# Период
# --------------------------------------------------------------------------- #
def _day_start(day: date) -> str:
    """Метка времени с 00:00:00 дня — для сравнения с created_at."""
    return datetime.combine(day, time.min, tzinfo=timezone.utc).isoformat()


def _day_end(day: date) -> str:
    """Метка времени с 23:59:59 дня.

    `created_at` хранится с секундами точности, поэтому «до конца дня»
    — это 23:59:59, а не начало следующих суток: иначе событие ровно
    в полночь попало бы в оба дня.
    """
    return datetime.combine(day, time.max.replace(microsecond=0), tzinfo=timezone.utc).isoformat()


def _check_period(since: Optional[date], until: Optional[date]) -> None:
    """Проверяет, что период осмысленный."""
    if since and until and since > until:
        raise HTTPException(
            status_code=HTTP_422,
            detail="Неверный период: since позже until",
        )


# --------------------------------------------------------------------------- #
# Текущее состояние
# --------------------------------------------------------------------------- #
@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Текущее состояние системы",
)
def get_status(repo: EventRepository = Depends(get_repository)) -> StatusResponse:
    """Состояние линии прямо сейчас.

    Если данных ещё нет (система только запустилась или база пуста),
    возвращается `UNKNOWN` с нулями, а не ошибка: клиенту нужно
    показать «нет данных», а не падать.
    """
    current = repo.latest_status()
    if current is None:
        return StatusResponse(
            camera_id=settings.camera_id,
            line_status=LineStatus.UNKNOWN,
            person_present=False,
            downtime_seconds=0,
            estimated_loss=0.0,
        )
    return StatusResponse(**current.to_dict())


@router.get(
    "/status/history",
    response_model=StatusHistoryResponse,
    summary="История снимков состояния",
)
def get_status_history(
    limit: int = Query(50, ge=1, le=1000, description="Сколько снимков вернуть"),
    camera_id: Optional[int] = Query(None, ge=1),
    repo: EventRepository = Depends(get_repository),
) -> StatusHistoryResponse:
    """Снимки состояния, свежие сверху. Нужны для графиков и отладки."""
    items = repo.status_history(camera_id=camera_id, limit=limit)
    return StatusHistoryResponse(
        items=[StatusResponse(**status.to_dict()) for status in items],
        count=len(items),
    )


# --------------------------------------------------------------------------- #
# История простоя
# --------------------------------------------------------------------------- #
@router.get(
    "/events",
    response_model=EventListResponse,
    summary="История событий простоя",
)
def list_events(
    limit: int = Query(50, ge=1, le=1000, description="Сколько событий вернуть"),
    offset: int = Query(0, ge=0, description="Сколько пропустить — для постраничного вывода"),
    camera_id: Optional[int] = Query(None, ge=1),
    event_type: Optional[str] = Query(
        None,
        description="DOWNTIME_STARTED или DOWNTIME_FINISHED; по умолчанию оба",
    ),
    since: Optional[date] = Query(None, description="С какой даты, включительно"),
    until: Optional[date] = Query(None, description="По какую дату, включительно"),
    repo: EventRepository = Depends(get_repository),
) -> EventListResponse:
    """События простоя, свежие сверху.

    Возвращаются оба типа событий: `DOWNTIME_STARTED` показывает, что
    линия стоит прямо сейчас, `DOWNTIME_FINISHED` — что простой
    завершился и ущерб посчитан.

    В `total` — сколько событий подходит под фильтр целиком, чтобы
    клиент показал «показано 20 из 143».
    """
    if event_type is not None and event_type not in EventType.ALL:
        raise HTTPException(
            status_code=HTTP_422,
            detail=f"Неизвестный тип события: {event_type}. Допустимо: {', '.join(EventType.ALL)}",
        )
    _check_period(since, until)

    since_iso = _day_start(since) if since else None
    until_iso = _day_end(until) if until else None

    total = repo.count_events(
        since=since_iso, until=until_iso, camera_id=camera_id, event_type=event_type
    )
    records = repo.events_with_time(
        since=since_iso,
        until=until_iso,
        camera_id=camera_id,
        event_type=event_type,
        limit=limit + offset,  # offset отсекаем вручную: страницы не перекрываются
    )
    page = records[offset : offset + limit]

    return EventListResponse(
        items=[EventResponse(**record.to_dict()) for record in page],
        count=len(page),
        total=total,
    )


@router.get(
    "/events/{event_id}",
    response_model=EventResponse,
    summary="Одно событие простоя",
)
def get_event(
    event_id: int = Path(..., ge=1),
    repo: EventRepository = Depends(get_repository),
) -> EventResponse:
    """Конкретное событие по его id.

    404, если события нет: это штатная ситуация (запрошено удалённое
    или выдуманное id), клиент должен это понимать, а не получать 500.
    """
    record = repo.get_event_with_time(event_id)
    if record is None:
        raise HTTPException(
            status_code=HTTP_404,
            detail=f"Событие с id={event_id} не найдено",
        )
    return EventResponse(**record.to_dict())


# --------------------------------------------------------------------------- #
# Агрегаты
# --------------------------------------------------------------------------- #
@router.get(
    "/statistics",
    response_model=StatisticsResponse,
    summary="Итоги за период",
)
def get_statistics(
    camera_id: Optional[int] = Query(None, ge=1),
    since: Optional[date] = Query(None, description="С какой даты, включительно"),
    until: Optional[date] = Query(None, description="По какую дату, включительно"),
    repo: EventRepository = Depends(get_repository),
) -> StatisticsResponse:
    """Суммарный простой, ущерб и количество простоя за период.

    Считаются только завершённые простоя: у начатого ещё нет ни
    длительности, ни ущерба, и включение его в отчёт искажало бы
    средние значения.
    """
    _check_period(since, until)
    stats = repo.statistics(
        since=_day_start(since) if since else None,
        until=_day_end(until) if until else None,
        camera_id=camera_id,
    )
    return StatisticsResponse(**stats.to_dict())


@router.get(
    "/statistics/by-camera",
    response_model=StatisticsByCameraResponse,
    summary="Итоги по камерам",
)
def get_statistics_by_camera(
    since: Optional[date] = Query(None),
    until: Optional[date] = Query(None),
    repo: EventRepository = Depends(get_repository),
) -> StatisticsByCameraResponse:
    """Простой и ущерб, сгруппированные по камерам."""
    _check_period(since, until)
    grouped = repo.downtime_by_camera(
        since=_day_start(since) if since else None,
        until=_day_end(until) if until else None,
    )
    items = [
        CameraStatistics(camera_id=camera_id, **values)
        for camera_id, values in sorted(grouped.items())
    ]
    return StatisticsByCameraResponse(items=items)


# --------------------------------------------------------------------------- #
# Камеры
# --------------------------------------------------------------------------- #
@router.get(
    "/cameras",
    response_model=CameraListResponse,
    summary="Зарегистрированные камеры",
)
def list_cameras(
    repo: EventRepository = Depends(get_repository),
) -> CameraListResponse:
    """Камеры, зарегистрированные при запуске обработки."""
    cameras = repo.list_cameras()
    return CameraListResponse(
        items=[
            CameraResponse(
                id=camera.id,
                name=camera.name,
                source_type=camera.source_type,
                is_demo=camera.is_demo,
                created_at=camera.created_at,
            )
            for camera in cameras
        ],
        count=len(cameras),
    )


# --------------------------------------------------------------------------- #
# Служебное
# --------------------------------------------------------------------------- #
@router.get(
    "/config",
    response_model=ConfigResponse,
    summary="Настройки, нужные для отображения",
)
def get_config() -> ConfigResponse:
    """Текущие настройки backend.

    `cost_per_minute` отдаётся, чтобы интерфейс мог подписать «за минуту
    — 750 ₽». Пересчитывать по нему ущерб нельзя: готовое значение
    всегда приходит в `estimated_loss`.
    """
    return ConfigResponse(
        cost_per_minute=settings.cost_per_minute,
        camera_id=settings.camera_id,
        default_source=settings.default_source,
        downtime_threshold_seconds=settings.downtime_threshold_seconds,
        timezone="UTC",
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверка живости",
)
def get_health(repo: EventRepository = Depends(get_repository)) -> HealthResponse:
    """Жив ли сервис и доступна ли база.

    Ошибка БД не превращается в 500: в ответе `database: "error"`,
    иначе мониторинг не отличит «сервис упал» от «база недоступна».
    """
    database_state = "ok"
    schema_version = 0
    try:
        schema_version = repo.db.schema_version()
    except Exception:  # noqa: BLE001 — health-check не должен падать
        database_state = "error"

    return HealthResponse(
        status="ok",
        database=database_state,
        schema_version=schema_version,
        server_time=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Демо-данные
# --------------------------------------------------------------------------- #
@router.post(
    "/demo/seed",
    summary="Наполнить базу демонстрационными данными",
    description=(
        "Заполняет базу правдоподобной историей простоя, чтобы Dashboard "
        "и Telegram было что показать до подключения видео. Ущерб считается "
        "той же формулой, что и в рабочем режиме. По умолчанию реальные "
        "данные не затираются — поставьте `replace=true`, чтобы очистить базу."
    ),
)
def seed_demo(
    days_back: int = Query(7, ge=1, le=90, description="Сколько дней истории создать"),
    replace: bool = Query(False, description="Очистить базу перед наполнением"),
    repo: EventRepository = Depends(get_repository),
) -> dict:
    """Наполнение демо-данными. Только явный вызов, автоматически — никогда."""
    return demo_seed(repo, days_back=days_back, replace=replace)


__all__ = ["router", "get_database", "get_repository", "close_database"]