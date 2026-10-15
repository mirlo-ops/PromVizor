"""Тест фонового опроса бота (Этап 8).

На сервере бот выглядел живым: он опрашивал backend и печатал
ошибку на каждом событии, потому что обработчик искал у объекта
`Application` атрибут `.application`, которого нет. Уведомления при
этом не уходили никуда, а курсор стоял на месте — то есть система
молча не работала.

Тест ловит именно эту ошибку по форме: подпись `_watch` должна
принимать объект приложения, и обработчик должен получать именно
его, а не что-то другое.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SOURCE = Path(__file__).resolve().parents[1] / "app" / "telegram" / "bot.py"

_tests = []


def test(name):
    def wrap(fn):
        _tests.append((name, fn))
        return fn

    return wrap


def _functions() -> dict:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_name(func):
    """Имя вызываемой функции: и для obj.method(), и для method()."""
    return getattr(func, "attr", None) or getattr(func, "id", None) or ""


def _nested(node):
    """Вложенные функции (async def) внутри указанной функции.

    `_post_init` определена внутри `main()`, поэтому общий обход дерева
    её не находит: ast.walk ищет по всем потомкам, но имя узла у
    вложенной функции — `_post_init`, а не FuncDef верхнего уровня.
    Здесь берём именно прямых потомков.
    """
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


@test("_watch принимает объект приложения")
def _():
    # Без параметра фон не запускался: Application ждёт вызов с
    # аргументом, и задача молча не создавалась.
    watch = _functions()["_watch"]
    args = [a.arg for a in watch.args.args]
    assert "app" in args, f"_watch должен принимать приложение, а не {args}"


@test("_post_init передаёт приложение в _watch")
def _():
    main = _functions()["main"]
    nested = {n.name: n for n in _nested(main)}
    assert "_post_init" in nested, "_post_init не найдена внутри main()"

    calls = [
        node
        for node in ast.walk(nested["_post_init"])
        if isinstance(node, ast.Call) and _called_name(node.func) == "_watch"
    ]
    assert calls, "_post_init не вызывает _watch"
    assert calls[0].args, "_watch вызывается без аргументов — фон не запустится"


@test("обработчик получает приложение, а не сам объект")
def _():
    # Ключевая ошибка: обработчик брал объект Application вместо
    # контекста и искал у него .application.
    watch = _functions()["_watch"]
    lambdas = [n for n in ast.walk(watch) if isinstance(n, ast.Lambda)]
    assert lambdas, "в _watch нет обработчика события"

    lambda_node = lambdas[0]
    assert len(lambda_node.args.args) == 1, "обработчик должен принимать одно событие"

    passed = lambda_node.body
    assert isinstance(passed, ast.Call), "обработчик должен звать handle_event"

    func = passed.func
    name = getattr(func, "attr", "") or getattr(func, "id", "")
    assert name == "handle_event", f"неожиданный вызов: {name}"

    # Аргумент handle_event должен быть переменная-параметр, а не
    # сам application: тот объект не является контекстом PTB.
    arg = passed.args[0]
    assert isinstance(arg, ast.Name), "передаётся не переменная"
    assert arg.id != "application", (
        "в обработчик передаётся сам application — у него нет .application"
    )
    assert arg.id == lambda_node.args.args[0].arg, (
        "в обработчик должно идти событие, а не объект приложения"
    )


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