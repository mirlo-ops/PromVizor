"""Загрузка переменных окружения из `.env` (Этап 4).

Проект сознательно обходится без `python-dotenv`: для набора из
нескольких переменных файл проще разобрать самостоятельно, чем
добавлять зависимость ради тридцати строк.

Правила разбора:

* `KEY=VALUE` — базовый формат;
* `#` в начале строки — комментарий;
* кавычки `'` и `"` вокруг значения снимаются;
* пустой `KEY=` даёт пустую строку;
* префикс `export ` отбрасывается.

Приоритет: **настоящее окружение важнее файла.** Если переменная уже
задана в окружении (например, в CI или контейнере), значение из `.env`
её НЕ перезаписывает. Иначе нельзя было бы подменить настройку на
проде, не редактируя файл.

Файл читается, но не перезаписывается: это не реестр секретов,
а источник значений по умолчанию.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

#: Разделитель строк в .env
DEFAULT_ENV_PATH = ".env"


def parse_env(text: str) -> Dict[str, str]:
    """Разбирает содержимое .env в словарь.

    Строки без `=` и с некорректным именем переменной пропускаются —
    лучше потерять одну строку, чем упасть при запуске.
    """
    result: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        # export KEY=VALUE → KEY=VALUE
        if line.startswith("export "):
            line = line[len("export "):].strip()

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()

        if not key or not _is_valid_key(key):
            continue

        result[key] = _unquote(value.strip())
    return result


def load_env(path: str = DEFAULT_ENV_PATH, override: bool = False) -> Dict[str, str]:
    """Загружает .env в `os.environ` и возвращает разобранные значения.

    Аргументы:
        path: путь к файлу. Отсутствующий файл — не ошибка.
        override: True — переменные из файла перезапишут окружение.
                  По умолчанию False: окружение важнее файла.

    Возвращает:
        Словарь «ключ → значение», которые заданы в файле
        (независимо от того, применились они или нет).
    """
    values = parse_env(_read(path))

    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value

    return values


def _read(path: str) -> str:
    """Читает файл; отсутствие файла — пустой результат."""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _is_valid_key(key: str) -> bool:
    """Имя переменной: буквы, цифры и подчёркивание, начиная с буквы."""
    if not key[0].isalpha() and key[0] != "_":
        return False
    return all(ch.isalnum() or ch == "_" for ch in key)


def _unquote(value: str) -> str:
    """Снимает обрамляющие кавычки и хвостовой комментарий."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    # Комментарий допускаем только при наличии пробела перед ним,
    # иначе знак # может быть частью значения.
    for marker in (" #", "\t#"):
        index = value.find(marker)
        if index != -1:
            return value[:index].strip()
    return value


# --------------------------------------------------------------------------- #
# Чтение типизированных значений
# --------------------------------------------------------------------------- #
def env_str(key: str, default: str = "") -> str:
    """Строковое значение или default, если переменной нет."""
    value = os.environ.get(key)
    return default if value is None or value == "" else value


def env_float(key: str, default: float) -> float:
    """Числовое значение. Некорректное значение не ломает запуск."""
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw.replace(",", ".").strip())
    except ValueError:
        return default


def env_int(key: str, default: int) -> int:
    """Целое значение. Некорректное значение не ломает запуск."""
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        try:
            return int(float(raw.strip()))
        except ValueError:
            return default


def env_bool(key: str, default: bool = False) -> bool:
    """Булево значение: 1/true/yes/on — истина."""
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def describe_source(key: str) -> str:
    """Откуда взялось значение — для отладки конфигурации."""
    if key in os.environ:
        value = os.environ[key]
        # Секреты не показываем: только факт наличия и длину
        return f"переменная окружения (длина {len(value)})"
    return "значение по умолчанию"


__all__ = [
    "parse_env",
    "load_env",
    "env_str",
    "env_float",
    "env_int",
    "env_bool",
    "describe_source",
    "Optional",
]