# Контракт Lev ↔ Gasun

Единая точка обмена данными между backend (Lev) и интерфейсом (Gasun).

## SystemStatus

Главный объект состояния. Создаётся Lev, отображается Gasun.

| Поле | Тип | Описание |
|---|---|---|
| `camera_id` | `int` | Идентификатор камеры/линии |
| `line_status` | `str` | `WORKING` / `STOPPED` / `UNKNOWN` |
| `person_present` | `bool` | Человек обнаружен в контролируемой зоне |
| `downtime_seconds` | `int` | Продолжительность текущего простоя, сек |
| `estimated_loss` | `float` | Расчётный ущерб, ₽ |

## DowntimeEvent

Событие для истории (`GET /api/events`):

| Поле | Тип | Описание |
|---|---|---|
| `camera_id` | `int` | Камера |
| `event` | `str` | `DOWNTIME_STARTED` / `DOWNTIME_FINISHED` |
| `start_time` | `str` | Начало, `HH:MM` |
| `end_time` | `str` | Конец, `HH:MM` (пусто, если ongoing) |
| `duration_seconds` | `int` | Продолжительность |
| `estimated_loss` | `float` | Ущерб, ₽ |

## Statistics

Агрегаты (`GET /api/statistics`): `total_downtime_seconds`, `total_loss`, `events_count`.

## Правила

- **Любое изменение полей — только через согласование Lev и Gasun.**
- Gasun не должен добавлять в объект «удобные» поля без обсуждения.
- Lev не должен менять смысл существующих полей без уведомления Gasun.
