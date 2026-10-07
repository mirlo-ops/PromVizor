"""Конфигурация backend-части «ПромВизор» (зона ответственности Lev)."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    # --- Камеры ---
    camera_id: int = 1

    # --- DEMO ---
    # 30 секунд реального времени = 100 минут демонстрационного времени → ×200
    demo_time_scale: int = 200
    demo_video_dir: str = "demo/videos"
    demo_scenario_dir: str = "demo/scenarios"

    # --- Источники по умолчанию ---
    default_source: str = "demo"
    rtsp_url: str = ""  # из .env: RTSP_URL

    # --- База данных ---
    database_path: str = "promvizor.db"

    # --- Telegram (Gasun читает через .env, но ключи согласованы) ---
    telegram_bot_token: str = ""  # TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = ""  # TELEGRAM_CHAT_ID


settings = Settings()
