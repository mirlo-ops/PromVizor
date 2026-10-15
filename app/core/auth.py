"""Вход на сайт по паролю.

Зачем не HTTP Basic. Базовую аутентификацию браузер запоминает на всю
сессию: открыли сайт, ввели пароль, и больше он не спрашивает — ни на
новой вкладке, ни через неделю. Для выставочного стенда это плохо:
пароль раздаётся каждому, кто подошёл, и каждый посетитель видит
чужой доступ.

Что здесь вместо этого. Есть проверка запроса и короткоживущая
отметка «эта вкладка уже вошла»:

* сервер проверяет заголовок `X-Promvizor-Auth` на КАЖДЫЙ запрос и
  решает, пускать или нет;
* браузер хранит отметку в `sessionStorage`, а он у каждой вкладки
  свой. Открыли новую вкладку — отметки нет, пароль спрашивается
  снова. Обновили страницу (F5) в этой же вкладке — отметка на месте,
  пароль не спрашивается. Это ровно то поведение, которое нужно на
  демонстрации;
* закрыли вкладку — отметка исчезла.

Чего здесь нет. Пароля в коде: сервер хранит только его SHA-256.
Пароль не пишется в журналы, не подставляется в URL и не передаётся
куда-либо, кроме самого сервера по HTTPS.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional

from app.core.env import env_bool, env_str

#: Заголовок, в котором браузер передаёт отметку о входе.
AUTH_HEADER = "x-promvizor-auth"

#: Куда браузер кладет отметку и откуда её забирает при запросах.
SESSION_KEY = "pv_auth"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class AuthSettings:
    """Настройки входа."""

    enabled: bool = False
    login: str = ""
    password_hash: str = ""

    @property
    def active(self) -> bool:
        """Вход реально включён: и флаг, и логин, и хеш заданы."""
        return bool(self.enabled and self.login and self.password_hash)

    def check(self, login: str, password: str) -> bool:
        """Сверяет логин и пароль.

        Сравнение хешей — постоянного времени: иначе по задержке
        ответа можно подбирать пароль по одному символу.
        """
        if not self.active:
            return False
        if not hmac.compare_digest(login or "", self.login):
            return False
        return hmac.compare_digest(_sha256(password or ""), self.password_hash)


def load_auth_settings(env: Optional[dict] = None) -> AuthSettings:
    """Читает настройки входа из окружения.

    Пароль в окружении не хранится — только его хеш. Получить хеш
    можно на сервере одной командой:

        python -c "import hashlib;print(hashlib.sha256(b'пароль').hexdigest())"
    """
    # Читаем из переданного словаря, а не через env_str/env_bool:
    # те функции смотрят только в os.environ, и подмена окружения
    # в тестах (или в контейнере) молча проигнорилась бы.
    source = env if env is not None else os.environ

    def raw(key: str) -> str:
        value = source.get(key)
        return "" if value is None else str(value).strip()

    enabled = raw("AUTH_ENABLED").lower() in {"1", "true", "yes", "on"}
    return AuthSettings(
        enabled=enabled,
        login=raw("AUTH_LOGIN"),
        password_hash=raw("AUTH_PASSWORD_HASH").lower(),
    )


def password_hash(password: str) -> str:
    """Хеш пароля для .env. В открытом виде пароль нигде не лежит."""
    return _sha256(password)


def make_header(login: str, password: str) -> str:
    """Значение заголовка для браузера."""
    raw = f"{login}:{password}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def verify_header(settings: AuthSettings, header: Optional[str]) -> bool:
    """Проверяет заголовок запроса.

    Заголовок несёт base64 от «логин:пароль», поэтому проверяется
    после разбора, а не сравнением строк: base64 отличается от
    исходных данных, и «просто сравнить» не сработало бы.
    """
    if not settings.active:
        return True
    if not header:
        return False
    try:
        decoded = base64.b64decode(header, validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001 — мусор в заголовке не должен ронять запрос
        return False
    login, _, password = decoded.partition(":")
    return settings.check(login, password)


# --------------------------------------------------------------------------- #
# Страница входа
# --------------------------------------------------------------------------- #
#: Страница отдаётся сервером, а не файлом из web/: её не должно быть
#: видно в интерфейсе как «часть системы» и её нельзя случайно
#: отключить, удалив файл. Один файл, без зависимостей.
LOGIN_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>ПромВизор — вход</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #0f1216; color: #e6e9ee;
    font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  }
  form {
    width: 320px; padding: 28px 26px; border-radius: 12px;
    background: #171b21; border: 1px solid #262c34;
  }
  h1 { margin: 0 0 4px; font-size: 19px; font-weight: 600; }
  p.sub { margin: 0 0 20px; color: #8b95a3; font-size: 13px; }
  label { display: block; font-size: 12px; color: #8b95a3; margin-bottom: 5px; }
  input {
    width: 100%; padding: 9px 11px; margin-bottom: 14px; border-radius: 7px;
    border: 1px solid #313945; background: #0f1216; color: #e6e9ee;
    font-size: 14px;
  }
  input:focus { outline: none; border-color: #3d7d5f; }
  button {
    width: 100%; padding: 10px; border: 0; border-radius: 7px;
    background: #2f6f52; color: #fff; font-size: 14px; font-weight: 500;
    cursor: pointer;
  }
  button:hover { background: #3a8563; }
  .err { display: none; margin: -6px 0 14px; color: #e07a5f; font-size: 12px; }
  .err.on { display: block; }
</style>
</head>
<body>
<form id="f">
  <h1>ПромВизор</h1>
  <p class="sub">Вход для просмотра данных системы</p>

  <label for="u">Логин</label>
  <input id="u" name="u" autocomplete="username" required autofocus>

  <label for="p">Пароль</label>
  <input id="p" name="p" type="password" autocomplete="current-password" required>

  <div class="err" id="e">Неверный логин или пароль</div>
  <button type="submit">Войти</button>
</form>
<script>
(function () {
  /* Отметка хранится в sessionStorage — у каждой вкладки своё
     хранилище. Поэтому новая вкладка спрашивает пароль заново,
     а обновление страницы внутри этой же вкладки — нет. Ровно то,
     что нужно на демонстрации. Хранилище, общее на весь браузер,
     здесь не годится: пароль спросили бы один раз на всё время. */
  var KEY = "pv_auth";

  document.getElementById("f").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var login = document.getElementById("u").value;
    var pass = document.getElementById("p").value;

    fetch("/api/auth/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login: login, password: pass })
    }).then(function (r) {
      if (!r.ok) {
        document.getElementById("e").classList.add("on");
        return;
      }
      return r.json().then(function (data) {
        sessionStorage.setItem(KEY, data.token);
        /* Пароль из памяти страницы вычищаем: после входа он больше
           не нужен, а держать его в DOM неправильно. */
        document.getElementById("p").value = "";
        location.reload();
      });
    }).catch(function () {
      document.getElementById("e").classList.add("on");
    });
  });
})();
</script>
</body>
</html>
"""


__all__ = [
    "AUTH_HEADER",
    "SESSION_KEY",
    "AuthSettings",
    "load_auth_settings",
    "password_hash",
    "make_header",
    "verify_header",
    "LOGIN_PAGE",
]