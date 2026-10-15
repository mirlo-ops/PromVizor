"""HTTP-прокси для Telegram (Этап 8).

Зачем. С части хостингов до `api.telegram.org` не достучаться: соединение
висит до таймаута, бот молча ничего не шлёт. Обход — HTTP-прокси.

Почему именно тут, а не в докере и не в systemd. Прокси нужен клиенту
Telegram внутри бота (`python-telegram-bot`), то есть коду нашего
проекта. Значит, он задаётся переменными окружения — Docker Compose и
systemd пробрасывают их без правок кода.

Что важно: прокси применяется ТОЛЬКО к Telegram и НИКОГДА к API
ПромВизора. Иначе «бот не отвечает» превратилось бы в «бот читает
чужие данные» — например, в прокси стороннего провайдера. Разделение
сделано отдельным объектом, а не общим параметром.

Как задать:

```bash
TELEGRAM_PROXY_URL=http://пользователь:пароль@хост:порт
TELEGRAM_PROXY_ENABLED=1
```

Пустой `TELEGRAM_PROXY_URL` — прокси не используется, поведение
прежнее. Значение в `.env` на сервере: секрет, в Git не попадает.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from app.core.env import env_str


@dataclass
class ProxySettings:
    """Что бот знает о прокси."""

    url: str = ""
    enabled: bool = False

    @property
    def configured(self) -> bool:
        """Прокси реально применится: включён И адрес задан."""
        return bool(self.enabled and self.url.strip())

    def describe(self) -> str:
        """Короткое описание для журнала — без логина и пароля."""
        if not self.configured:
            return "напрямую, без прокси"
        scheme = self.url.split("://", 1)[0] if "://" in self.url else "?"
        host = self.url.rsplit("@", 1)[-1]
        return f"через прокси ({scheme}://{host})"


def load_proxy_settings(env: Optional[dict] = None) -> ProxySettings:
    """Читает настройки прокси из окружения.

    `env` передаётся в тестах, чтобы не подменять окружение процесса.
    """
    source = env if env is not None else os.environ
    url = env_str("TELEGRAM_PROXY_URL", source.get("TELEGRAM_PROXY_URL", ""))
    # По умолчанию прокси включается, если адрес задан: иначе оператор
    # вписал адрес, а бот молча пошёл напрямую и не работал — причина
    # неочевидна. Явное TELEGRAM_PROXY_ENABLED=0 всё ещё отключает.
    raw_flag = source.get("TELEGRAM_PROXY_ENABLED")
    enabled = bool(url.strip()) if raw_flag is None else raw_flag.strip() not in {
        "0", "false", "no", "off", "нет", "",
    }
    return ProxySettings(url=url.strip(), enabled=enabled)


def build_telegram_proxy(proxy: ProxySettings) -> Optional[str]:
    """Возвращает адрес прокси для python-telegram-bot либо None.

    Именно строка, а не готовый объект запроса: у билдера PTB есть
    перегрузка `.proxy(url: str)`, которая сама создаёт клиент с этим
    прокси. Передача объекта приводит к ошибке «HTTPXRequest object has
    no attribute url» — выглядит как поломка библиотеки.

    Проверка здесь не пустая формальность: без неё неверный адрес
    добрался бы до сети и выглядел бы как «Telegram недоступен».
    """
    if not proxy.configured:
        return None

    url = proxy.url
    if "@" not in url and "://" in url:
        # Адрес без логина почти всегда означает «забыли внести
        # логин»: прокси ответит 407, и причина будет неочевидна.
        raise ValueError(
            "TELEGRAM_PROXY_URL указан без логина и пароля: ожидался вид "
            "http://пользователь:пароль@хост:порт"
        )

    return url


__all__ = [
    "ProxySettings",
    "load_proxy_settings",
    "build_telegram_proxy",
]