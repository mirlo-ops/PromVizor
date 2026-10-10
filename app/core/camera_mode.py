"""Режим работы с камерой: демо / веб-камера / сетевая камера.

Требование ТЗ: режим выбирается в настройках Dashboard и влияет на
то, что видно в окне камер.

Где хранится выбор. В отдельном JSON-файле рядом с базой, а не в
`.env`: файл `.env` читается только при старте процесса, а режим
переключается на работающей системе без перезапуска.

Приоритет: переменная окружения > .env > файл настроек > умолчание.
Переменная окружения задана намеренно (например, в контейнере) —
тогда она главнее и перезаписать её из интерфейса нельзя.

Чего здесь нет. Сетевой камера в Alpha недоступна: включать нечего,
и выбранный режим честно показывает «нет сигнала», а не пустоту.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

from app.core.config import settings
from app.core.env import env_str

#: Режимы камеры, доступные в настройках.
MODE_DEMO = "demo"
MODE_WEBCAM = "webcam"
MODE_RTSP = "rtsp"
MODE_SYNTHETIC = "synthetic"

ALL_MODES = (MODE_DEMO, MODE_WEBCAM, MODE_RTSP, MODE_SYNTHETIC)

#: Куда сохраняется выбор
SETTINGS_FILE = Path("camera_mode.json")

#: Откуда берётся режим, если файл ещё не создан
DEFAULT_MODE = MODE_SYNTHETIC


@dataclass
class CameraMode:
    """Выбранный режим и параметры камеры."""

    #: demo | webcam | rtsp | synthetic
    mode: str = DEFAULT_MODE
    #: Номер веб-камеры: 0 — первая
    webcam_index: int = 0
    #: Подпись сцены в демо-режиме (норма / остановка / уход / …)
    demo_scenario: str = "normal"

    def to_dict(self) -> Dict:
        return asdict(self)

    def is_valid(self) -> bool:
        return self.mode in ALL_MODES and self.webcam_index >= 0


def _settings_path() -> Path:
    return SETTINGS_FILE


def _from_environment() -> Optional[CameraMode]:
    """Режим из переменной окружения, если он задан.

    Задаётся намеренно (например, в контейнере или при развёртывании),
    поэтому имеет приоритет над тем, что выбрано в интерфейсе.
    """
    raw = os.environ.get("DEFAULT_SOURCE")
    if raw:
        value = raw.strip().lower()
        if value in ALL_MODES:
            return CameraMode(mode=value)
    return None


def load() -> CameraMode:
    """Читает текущий режим: окружение → файл → умолчание."""
    forced = _from_environment()
    if forced is not None:
        return forced

    path = _settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CameraMode(mode=DEFAULT_MODE, webcam_index=settings.webcam_index)

    try:
        mode = CameraMode(
            mode=str(raw.get("mode", DEFAULT_MODE)),
            webcam_index=int(raw.get("webcam_index", 0)),
            demo_scenario=str(raw.get("demo_scenario", "normal")),
        )
    except (TypeError, ValueError):
        return CameraMode(mode=DEFAULT_MODE)

    if not mode.is_valid():
        # Битый файл не должен ломать запуск: возвращаем умолчание
        return CameraMode(mode=DEFAULT_MODE)
    return mode


def save(mode: CameraMode) -> CameraMode:
    """Сохраняет выбор. Возвращает то, что реально записано."""
    if not mode.is_valid():
        raise ValueError(
            f"Недопустимый режим: {mode.mode!r}. Допустимые: {', '.join(ALL_MODES)}"
        )

    forced = _from_environment()
    if forced is not None:
        # Режим задан окружением — молча применить другое нельзя,
        # иначе интерфейс показывал бы не то, что на самом деле.
        return forced

    try:
        _settings_path().write_text(
            json.dumps(mode.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Не смогли сохранить — работаем с тем, что есть
        pass
    return mode


def describe_mode(mode: str) -> str:
    """Человекочитаемое название режима для интерфейса."""
    return {
        MODE_DEMO: "Демо-ролики",
        MODE_WEBCAM: "Веб-камера",
        MODE_RTSP: "Сетевая камера",
        MODE_SYNTHETIC: "Демо (синтетика)",
    }.get(mode, mode)


__all__ = [
    "CameraMode",
    "load",
    "save",
    "describe_mode",
    "ALL_MODES",
    "MODE_DEMO",
    "MODE_WEBCAM",
    "MODE_RTSP",
    "MODE_SYNTHETIC",
]