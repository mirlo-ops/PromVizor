# ПромВизор

Контроль простоя производственной линии по видеопотоку с камеры.

Система наблюдает за линией через камеру, определяет остановку
конвейера и уход рабочего из контролируемой зоны (ROI), фиксирует
простой, считает таймер и условный ущерб, сохраняет событие
и показывает результат на Dashboard.

**Стадия проекта:** MVP / Alpha — реализован только видео-этап,
ядро обнаружения простоя и API ещё не написаны.
Актуальный статус по этапам — в файле [`Handoff Lev.txt`](Handoff%20Lev.txt).

---

## Текущее состояние

| Этап | Статус |
|---|---|
| 1. Video (DEMO / WEBCAM / RTSP) | ✅ готов |
| 2. Computer Vision (YOLO, ROI, motion) | ✅ готов |
| 3. Event Engine | ✅ готов |
| 4. Экономика (downtime, loss) | ✅ готов |
| 5. Database (SQLite) | ❌ не начат |
| 6. API (FastAPI) | ❌ не начат |
| 7. Dashboard | ⚠️ интерфейс готов, данных нет |
| 8. Telegram | ❌ не начат (зона Gasun) |
| 9. Demo-видео | ❌ роликов нет |
| 10. Сквозная демонстрация | ❌ невозможна без этапов 5–6 |

Подробный разбор — раздел 22 файла Handoff.

---

## Быстрый старт

### Dashboard

Статика без сборки и зависимостей. Нужен любой HTTP-сервер:

```bash
cd web
python3 -m http.server 8777
```

Открыть <http://localhost:8777>.

Данные сейчас **моковые** — генерируются в браузере детерминированно
(один и тот же день всегда даёт одну и ту же картину). Подключения к
API пока нет.

Возможности интерфейса:

* **Мониторинг** — «Линии» (таймлайн простоев за сутки), «Камеры»
  (видеопоток с полосой активности), «Сводная» (KPI и последние события)
* **Сотрудники** — 120 человек, поиск, фильтр по линии, пагинация, экспорт CSV
* **Отчёты** — период день / неделя / месяц, разбивка по линиям и сотрудникам,
  распределение по дням, печать и экспорт CSV
* **Настройки** — расчёт ущерба, порог фиксации простоя, источник видео,
  уведомления, хранение данных, светлая и тёмная тема

Настройки и тема сохраняются в `localStorage` браузера.

### Video-подсистема (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> В системном `python3` OpenCV нет — запускайте через `.venv/bin/python`
> или активируйте окружение.

Использование:

```python
from app.video import create_source, list_available_scenarios

# Список сценариев, для которых ролики уже лежат в demo/videos/
print(list_available_scenarios())

# Имена сценариев: normal, stopped, person_left, stopped_no_person
with create_source("demo", scenario="stopped_no_person") as src:
    frame = src.get_frame()
```

Также доступны источники `"webcam"` и `"rtsp"` (URL берётся из
переменной `RTSP_URL`).

---

## Структура

```text
promvizor/
├── app/
│   ├── core/          # контракт, типы, настройки, формула ущерба
│   ├── video/         # VideoSource, DEMO / WEBCAM / RTSP, фабрика
│   ├── vision/        # ROI, детектор человека (YOLO), детектор движения
│   └── engine/        # Event Engine: простой, события, ущерб
│
├── web/               # Dashboard (HTML + CSS + JS, без сборки)
│   ├── css/styles.css
│   └── js/            # data, components, sections, app
│
├── tests/             # тесты vision и engine
├── demo/videos/       # DEMO-ролики сценариев (COMMON)
│
├── requirements.txt
├── .env.example
└── Handoff Lev.txt    # детальный статус проекта и план по этапам
```

---

## DEMO-режим

Ожидаются четыре ролика в `demo/videos/` (см. `demo/videos/README.md`):

| Сценарий | Файл | Содержание |
|---|---|---|
| `normal` | `normal.mp4` | Линия движется, рабочий в ROI |
| `stopped` | `stopped.mp4` | Линия стоит, рабочий на месте |
| `person_left` | `person_left.mp4` | Линия движется, рабочий ушёл из ROI |
| `stopped_no_person` | `downtime.mp4` | Линия стоит + рабочего нет → ПРОСТОЙ |

Ролики короткие (~10 секунд) и воспроизводятся как есть, без реального
ускорения. «Ускоренное демо-время» существует только в таймере
интерфейса.

DEMO-видео — демонстрационный поток, имитирующий производственную
камеру. Выдавать его за реальную запись предприятия нельзя.

---

## Ядро: видео → vision → простой

```python
from app.video import create_source
from app.vision import VisionPipeline
from app.engine import EventEngine

vision = VisionPipeline()
engine = EventEngine(camera_id=1, threshold_seconds=30)

with create_source("demo", scenario="stopped_no_person") as src:
    while (frame := src.get_frame()) is not None:
        result = vision.process(frame)

        for event in engine.update(result):
            print(event.describe())

        print(engine.status(result).to_dict())
```

`engine.status()` возвращает `SystemStatus` — ровно тот объект,
который по контракту уходит в Dashboard и Telegram.

Простой фиксируется, когда **линия стоит И рабочего нет дольше
порога**. Рабочий ушёл, а линия работает — простоя нет. Линия стоит,
но рабочий на месте — вероятно обслуживание, простоя нет.

## Тесты

```bash
.venv/bin/python tests/test_vision.py      # 17 тестов
.venv/bin/python tests/test_engine.py      # 19 тестов
.venv/bin/python tests/test_economics.py   # 21 тест
```

Тесты не требуют видео и работают без YOLO: движок принимает
результаты Vision напрямую, а детектор человека — подставной backend.

---

## Расчёт ущерба

```text
loss = downtime_minutes × cost_per_minute
```

Пример: `100 мин × 750 ₽ = 75 000 ₽`

Формула реализована **один раз** в `app/core/types.py` → `calculate_loss()`.
Считает backend (Lev). Dashboard и Telegram только отображают
готовое значение и не пересчитывают его самостоятельно —
это требование раздела 14 Handoff.

---

## Переменные окружения

Скопировать `.env.example` в `.env` и заполнить:

| Переменная | Назначение |
|---|---|
| `COST_PER_MINUTE` | стоимость минуты простоя, ₽ (по умолчанию 750) |
| `DOWNTIME_THRESHOLD_SECONDS` | порог подтверждения простоя, сек |
| `CAMERA_ID` | номер камеры |
| `DEFAULT_SOURCE` | `demo` / `webcam` / `rtsp` |
| `RTSP_URL` | адрес IP-камеры для источника RTSP |
| `DATABASE_PATH` | путь к файлу SQLite |
| `TELEGRAM_BOT_TOKEN` | токен Telegram-бота (зона Gasun) |
| `TELEGRAM_CHAT_ID` | чат для уведомлений (зона Gasun) |

Приоритет: **переменная окружения важнее `.env`**, а значение по
умолчанию используется, если не задано ни то, ни другое. Поэтому
настройки можно перекрыть на CI или в контейнере, не редактируя файл.

Файл `.env` не попадает в Git.

---

## Границы ответственности

* **Lev** — всё ядро: видео, компьютерное зрение, event engine,
  простой, расчёт ущерба, БД, FastAPI, Dashboard, демо
* **Gasun** — только Telegram-бот: команды, уведомления, форматирование

Контракт обмена данными — `app/core/CONTRACT.md`. Любое изменение
состава полей `SystemStatus` / `DowntimeEvent` / `Statistics`
согласуется между Lev и Gasun.

---

## Что дальше

Ближайшая задача — **Этап 2 (Computer Vision)**: YOLO для определения
человека, детектор движения конвейера и ROI. Без него Event Engine
запускать не на чем.

Подробный план и разбор каждого этапа — в
[`Handoff Lev.txt`](Handoff%20Lev.txt).