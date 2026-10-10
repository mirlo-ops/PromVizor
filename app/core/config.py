"""Конфигурация backend-части «ПромВизор» (зона ответственности Lev)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.env import env_float, env_int, env_str, load_env


class DemoScenario:
    """Сценарии DEMO-симулятора."""

    NORMAL = "normal"  # 1. Нормальная работа
    STOPPED = "stopped"  # 2. Остановка линии
    PERSON_LEFT = "person_left"  # 3. Уход человека
    STOPPED_NO_PERSON = "stopped_no_person"  # 4. Остановка + отсутствие человека

    ALL = (NORMAL, STOPPED, PERSON_LEFT, STOPPED_NO_PERSON)


@dataclass
class Settings:
    # --- Расчёт ущерба (formula: duration_min * cost_per_minute) ---
    cost_per_minute: float = 750.0  # ₽ за минуту простоя

    # --- Event Engine ---
    downtime_threshold_seconds: int = 30  # N секунд до фиксации простоя
    # После восстановления движения простой держится ещё столько секунд,
    # прежде чем событие завершится. Защита от «дребезга»: микропаузы
    # конвейера не должны порождать пару STARTED/FINISHED на каждый кадр.
    downtime_resume_confirm_seconds: int = 3
    # Сколько секунд состояние UNKNOWN отменяет незавершённый простой.
    # Нет данных — не считаем, что линия заработала.
    downtime_unknown_grace_seconds: int = 10

    # --- Камеры ---
    camera_id: int = 1

    # --- DEMO ---
    # Видео короткие (~10 сек) и играют как есть, БЕЗ реального ускорения.
    # «Ускоренное демо-время» — фейковое: таймер интерфейса масштабирует
    # реальное время ролика в демонстрационные минуты.
    demo_video_seconds: int = 10  # реальная длительность ролика, сек
    demo_display_minutes: int = 100  # «демо-минут» на весь ролик (100 мин за 10 сек)
    # Порог фиксации простоя в РЕАЛЬНЫХ секундах (для DEMO-роликов ~10 сек
    # основной порог 30с не сработает, поэтому для DEMO используется меньший)
    downtime_threshold_demo_seconds: int = 3
    demo_video_dir: str = "demo/videos"
    demo_scenario_dir: str = "demo/scenarios"

    # --- Источники по умолчанию ---
    default_source: str = "demo"
    rtsp_url: str = ""  # из .env: RTSP_URL

    # --- База данных ---
    database_path: str = "promvizor.db"

    # --- ROI (контролируемая зона камеры, Этап 2) ---
    # Координаты нормализованы: 0.0–1.0 от размера кадра, поэтому ROI
    # работает при любом разрешении камеры.
    roi_left: float = 0.20
    roi_top: float = 0.35
    roi_right: float = 0.80
    roi_bottom: float = 0.90

    # --- Person Detection (YOLO, Этап 2) ---
    yolo_model: str = "yolov8n.pt"   # лёгкая модель для Alpha
    yolo_confidence: float = 0.35     # порог уверенности
    # Классы COCO, которые считаем рабочими. 0 = person.
    yolo_person_classes: tuple = (0,)

    # --- Motion Detection (Этап 2) ---
    # Детектор считает движение по кадрам ВНУТРИ ROI.
    motion_history_size: int = 30          # сколько последних кадров копится
    motion_min_samples: int = 10           # минимум для решения (прогрев)
    motion_diff_threshold: float = 0.012   # доля изменившихся пикселей
    # Гистерезис: после STOPPED нужно больше движения для возврата
    # в WORKING — защита от дрожания на границе.
    motion_working_threshold: float = 0.018
    motion_stopped_threshold: float = 0.008
    motion_blur_kernel: int = 5            # размытие против шума
    motion_resize_width: int = 320         # ширина для расчёта (ускорение)
    # Кадр отбраковывается, если средняя яркость изменилась сильнее этого
    # порога. Иначе заслон объектива или включение света выглядели бы
    # как «линия поехала» — ложное срабатывание.
    motion_lighting_threshold: float = 0.08

    # --- Telegram (Gasun читает через .env, но ключи согласованы) ---
    telegram_bot_token: str = ""  # TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = ""  # TELEGRAM_CHAT_ID


def _from_env() -> "Settings":
    """Собирает настройки с учётом переменных окружения и .env.

    Приоритет: переменная окружения > .env > значение по умолчанию.
    Здесь не задаются только те поля, которые не должны приходить
    извне (например, координаты ROI — это часть конфигурации
    конкретной камеры, а не окружения).
    """
    return Settings(
        # --- Экономика ---
        cost_per_minute=env_float("COST_PER_MINUTE", 750.0),
        # --- Event Engine ---
        downtime_threshold_seconds=env_int("DOWNTIME_THRESHOLD_SECONDS", 30),
        # --- Камеры ---
        camera_id=env_int("CAMERA_ID", 1),
        # --- Источники ---
        default_source=env_str("DEFAULT_SOURCE", "demo"),
        rtsp_url=env_str("RTSP_URL", ""),
        # --- База данных ---
        database_path=env_str("DATABASE_PATH", "promvizor.db"),
        # --- Telegram ---
        telegram_bot_token=env_str("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env_str("TELEGRAM_CHAT_ID", ""),
    )


# Файл .env читается при импорте. Реальное окружение имеет приоритет,
# поэтому на CI и в контейнере переменные подставляются снаружи.
load_env()

settings = _from_env()
