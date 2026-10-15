"""Тесты: прокси Telegram и несколько чатов (Этап 8).

Зачем эти тесты. Два изменения на сервере сделали работу бота
зависимой от настроек, которые раньше не существовали: прокси и
список чатов. Обе ошибки молчаливые — бот просто не присылает
уведомления, — поэтому их и проверяем отдельно.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.telegram.proxy import load_proxy_settings

#: parse_chat_ids лежит в bot.py, а сам bot.py требует python-telegram-bot
#: — библиотеку, которой в лёгком окружении нет. Поэтому функция
#: достаётся из исходника: тест проверяет её поведение, не требуя
#: установки всей библиотеки Telegram.
_SOURCE = Path(__file__).resolve().parents[1] / "app" / "telegram" / "bot.py"


def _load_parse_chat_ids():
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "parse_chat_ids":
            module = ast.Module(body=[node], type_ignores=[])
            namespace: dict = {}
            exec(compile(module, str(_SOURCE), "exec"), namespace)  # noqa: S102
            return namespace["parse_chat_ids"]
    raise AssertionError("parse_chat_ids не найдена в bot.py")


parse_chat_ids = _load_parse_chat_ids()

_tests = []


def test(name):
    def wrap(fn):
        _tests.append((name, fn))
        return fn

    return wrap


# --------------------------------------------------------------------------- #
# Прокси
# --------------------------------------------------------------------------- #
@test("пустое окружение — прокси не используется")
def _():
    proxy = load_proxy_settings({})
    assert proxy.configured is False, "без адреса прокси включать нельзя"
    assert "напрямую" in proxy.describe()


@test("адрес задан — прокси включается сам")
def _():
    # Именно это и ломало бота на сервере: оператор вписал адрес,
    # но не включил флаг, и бот молча шёл напрямую, где Telegram
    # недоступен.
    proxy = load_proxy_settings({"TELEGRAM_PROXY_URL": "http://u:p@1.2.3.4:8000"})
    assert proxy.configured is True
    assert proxy.enabled is True


@test("явное выключение побеждает заданный адрес")
def _():
    proxy = load_proxy_settings(
        {"TELEGRAM_PROXY_URL": "http://u:p@1.2.3.4:8000", "TELEGRAM_PROXY_ENABLED": "0"}
    )
    assert proxy.configured is False


@test("адрес без логина отвергается с понятным текстом")
def _():
    from app.telegram.proxy import build_telegram_proxy

    # Без этой проверки прокси тихо ушёл бы в сеть и вернул 407:
    # причина «нет логина» не читается в сообщении про таймаут.
    proxy = load_proxy_settings({"TELEGRAM_PROXY_URL": "http://1.2.3.4:8000"})
    try:
        build_telegram_proxy(proxy)
    except ValueError as exc:
        assert "логин" in str(exc), f"текст не поясняет причину: {exc}"
    else:
        raise AssertionError("адрес без логина должен отвергаться")


@test("готовый адрес возвращается строкой, а не объектом")
def _():
    from app.telegram.proxy import build_telegram_proxy

    # Билдер PTB принимает строку. Если сюда вернуть объект
    # HTTPXRequest, бот падает с «no attribute url» уже в бою.
    url = build_telegram_proxy(
        load_proxy_settings({"TELEGRAM_PROXY_URL": "http://u:p@1.2.3.4:8000"})
    )
    assert isinstance(url, str), f"ожидалась строка, получено {type(url)}"
    assert url == "http://u:p@1.2.3.4:8000"


@test("описание не светит логин и пароль")
def _():
    proxy = load_proxy_settings(
        {"TELEGRAM_PROXY_URL": "http://cpUNNF:secret@1.2.3.4:8000"}
    )
    text = proxy.describe()
    assert "secret" not in text, f"пароль попал в описание: {text}"
    assert "cpUNNF" not in text, f"логин попал в описание: {text}"
    assert "1.2.3.4:8000" in text, "адрес прокси должен быть виден для диагностики"


# --------------------------------------------------------------------------- #
# Несколько чатов
# --------------------------------------------------------------------------- #
@test("один чат")
def _():
    assert parse_chat_ids("750743167") == ["750743167"]


@test("два чата через запятую")
def _():
    # То самое значение, которое стояло в .env на сервере и из-за
    # которого уведомления не доходили: Telegram получил «750743167,
    # 8851242509» как один несуществующий чат и ответил ошибкой.
    assert parse_chat_ids("750743167,8851242509") == ["750743167", "8851242509"]


@test("пробелы и точки с запятой не мешают")
def _():
    assert parse_chat_ids(" 111 , 222 ;333 ") == ["111", "222", "333"]


@test("группа со знаком минус сохраняется")
def _():
    assert parse_chat_ids("-1001234567890") == ["-1001234567890"]


@test("пустое значение даёт пустой список")
def _():
    assert parse_chat_ids("") == []
    assert parse_chat_ids("  ,  ") == []


def main() -> int:
    failed = []
    for name, fn in _tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"  ✗ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")

    print(f"\n{len(_tests) - len(failed)}/{len(_tests)} тестов пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())