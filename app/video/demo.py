"""DEMO-источник видео: подготовленные ролики по сценариям.

Договорённость по DEMO:
- видео короткие (~10 секунд), воспроизводятся как есть, БЕЗ реального ускорения;
- «ускоренное демо-время» — фейковое, живёт только в таймере интерфейса;
- ролики кладутся в demo/videos/ с именами из SCENARIO_FILES.
"""

from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

from app.core.config import DemoScenario, settings
from app.video.source import VideoSource

if TYPE_CHECKING:
    import numpy as np


class DemoSource(VideoSource):
    source_type = "demo"
    is_demo = True

    # Ожидаемые файлы в demo/videos/ (COMMON-папка).
    # Первое имя — каноническое, второе — то, под каким ролик
    # положили фактически. Раньше принималось только одно имя, и
    # ролики, залитые как «1_vidos.mp4», не находились: демо-режим
    # молча падал в «видео ещё не добавлены», хотя файлы лежали в
    # папке. Теперь подходит любое из двух имён.
    SCENARIO_FILES = {
        # 1. Всё нормально: линия работает, рабочий в кадре
        DemoScenario.NORMAL: ("normal.mp4", "1_vidos.mp4"),
        # 2. Линия остановилась, рабочий в кадре (обслуживание)
        DemoScenario.STOPPED: ("stopped.mp4", "2_vidos.mp4"),
        # 3. Рабочий уходит из кадра, линия работает
        DemoScenario.PERSON_LEFT: ("person_left.mp4", "3_vidos.mp4"),
        # 4. Главный сценарий: линия стоит и рабочий ушёл → ПРОСТОЙ
        DemoScenario.STOPPED_NO_PERSON: ("downtime.mp4", "4_vidos.mp4"),
    }

    def __init__(self, scenario: str = DemoScenario.NORMAL, loop: bool = True) -> None:
        super().__init__()
        if scenario not in self.SCENARIO_FILES:
            raise ValueError(
                f"Неизвестный DEMO-сценарий: {scenario!r}. "
                f"Допустимые: {', '.join(self.SCENARIO_FILES)}"
            )
        self.scenario = scenario
        self.loop = loop  # зацикливание короткого ролика

    # ------------------------------------------------------------------ #
    @property
    def video_path(self) -> str:
        """Путь к ролику сценария — первый из существующих."""
        names = self.SCENARIO_FILES[self.scenario]
        for name in names:
            candidate = os.path.join(settings.demo_video_dir, name)
            if os.path.isfile(candidate):
                return candidate
        # Ни одного нет — возвращаем каноническое имя, чтобы в
        # сообщении об ошибке было видно, что искалось.
        return os.path.join(settings.demo_video_dir, names[0])

    def _create_capture(self, cv2):  # noqa: ANN001
        path = self.video_path
        if not os.path.isfile(path):
            raise RuntimeError(
                "DEMO-видео ещё не добавлены.\n"
                f"Ожидался файл: {path}\n"
                "Ожидаемые имена (demo/videos/):\n"
                + "\n".join(
                    f"  {scenario:<18} → {' или '.join(names)}"
                    for scenario, names in self.SCENARIO_FILES.items()
                )
            )
        return cv2.VideoCapture(path)

    def _on_end(self) -> bool:
        """Конец ролика: при loop=True перематываем в начало."""
        if not self.loop or self._cap is None:
            return False
        self._cap.set(0, 0)  # CAP_PROP_POS_MSEC = 0
        return True

    # ------------------------------------------------------------------ #
    @property
    def duration_seconds(self) -> Optional[float]:
        """Реальная длительность ролика (~10 сек)."""
        return super().duration_seconds

    @property
    def progress(self) -> float:
        """Прогресс ролика 0.0–1.0 — основа для фейкового DEMO-таймера."""
        total = self.duration_seconds
        if not total:
            return 0.0
        return min(max(self.position_seconds / total, 0.0), 1.0)

    @property
    def is_available(self) -> bool:
        """Если ролик уже добавлен в demo/videos/."""
        return os.path.isfile(self.video_path)

    def describe(self) -> str:
        state = "файл на месте" if self.is_available else "файл ещё не добавлен"
        return f"DEMO source, сценарий '{self.scenario}' ({state})"


def list_available_scenarios() -> list[str]:
    """Сценарии, для которых ролики уже лежат в demo/videos/."""
    available = []
    for scenario, names in DemoSource.SCENARIO_FILES.items():
        if any(
            os.path.isfile(os.path.join(settings.demo_video_dir, name)) for name in names
        ):
            available.append(scenario)
    return available


def scenario_file(scenario: str) -> Optional[str]:
    """Имя файла ролика для сценария, если он есть. Иначе None."""
    names = DemoSource.SCENARIO_FILES.get(scenario)
    if not names:
        return None
    for name in names:
        if os.path.isfile(os.path.join(settings.demo_video_dir, name)):
            return name
    return None


def describe_scenarios() -> list[dict]:
    """Список сценариев для интерфейса: есть ли ролик и как он называется."""
    result = []
    for scenario, names in DemoSource.SCENARIO_FILES.items():
        filename = scenario_file(scenario)
        result.append(
            {
                "scenario": scenario,
                "file": filename or names[0],
                "available": filename is not None,
            }
        )
    return result
