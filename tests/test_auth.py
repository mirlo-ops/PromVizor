"""Тесты входа на сайт (Этап: развёртывание).

Проверяется поведение, ради которого всё затевалось: пароль спрашивается
при открытии новой вкладки, но не при обновлении страницы внутри этой
же вкладки. Технически это означает «отметка живёт в sessionStorage»,
а сервер проверяет её на каждом запросе.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import auth

_tests = []

PASSWORD = "24201182"
LOGIN = "akylov"
HASH = auth.password_hash(PASSWORD)


def test(name):
    def wrap(fn):
        _tests.append((name, fn))
        return fn

    return wrap


def _enabled_env() -> dict:
    return {
        "AUTH_ENABLED": "1",
        "AUTH_LOGIN": LOGIN,
        "AUTH_PASSWORD_HASH": HASH,
    }


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #
@test("выключенный вход никого не спрашивает")
def _():
    settings = auth.load_auth_settings({})
    assert settings.active is False
    assert auth.verify_header(settings, None) is True


@test("включённый вход пускает с верным заголовком")
def _():
    settings = auth.load_auth_settings(_enabled_env())
    assert settings.active is True
    header = auth.make_header(LOGIN, PASSWORD)
    assert auth.verify_header(settings, header) is True


@test("включённый вход не пускает без заголовка")
def _():
    settings = auth.load_auth_settings(_enabled_env())
    assert auth.verify_header(settings, None) is False
    assert auth.verify_header(settings, "") is False


@test("неверный пароль не пускает")
def _():
    settings = auth.load_auth_settings(_enabled_env())
    wrong = auth.make_header(LOGIN, "неправильный")
    assert auth.verify_header(settings, wrong) is False


@test("чужой логин не пускает")
def _():
    settings = auth.load_auth_settings(_enabled_env())
    other = auth.make_header("someone", PASSWORD)
    assert auth.verify_header(settings, other) is False


@test("мусор в заголовке не роняет проверку")
def _():
    # Заголовок приходит из браузера, то есть из сети: значение может
    # оказаться чем угодно. Ошибка разбора обязана означать «не пускаем»,
    # а не исключение на каждый запрос.
    settings = auth.load_auth_settings(_enabled_env())
    assert auth.verify_header(settings, "не-base64-вовсе!!") is False
    assert auth.verify_header(settings, base64.b64encode(b"\xff\xfe").decode()) is False


@test("пароль не хранится в открытом виде")
def _():
    # В .env попадает только хеш. Проверяем, что исходного пароля там
    # нет и что из настроек его не вытащить.
    settings = auth.load_auth_settings(_enabled_env())
    assert PASSWORD not in settings.password_hash
    assert PASSWORD not in repr(settings)


@test("пароль сравнивается как есть, регистр значим")
def _():
    # Пароль из цифр: PASSWORD.upper() равно самому паролю, и проверка
    # «регистр значим» на нём бессмысленна. Поэтому здесь — пароль с
    # буквами, а настоящий проверяется отдельно.
    mixed = "Pass2420"
    settings = auth.AuthSettings(
        enabled=True, login=LOGIN, password_hash=auth.password_hash(mixed)
    )
    assert settings.check(LOGIN, mixed) is True, "верный пароль должен проходить"
    assert settings.check(LOGIN, mixed.upper()) is False, "регистр пароля значим"
    assert settings.check(LOGIN, mixed + " ") is False, "пробел в конце — другой пароль"

    real = auth.load_auth_settings(_enabled_env())
    assert real.check(LOGIN, PASSWORD) is True, "рабочий пароль должен проходить"


@test("корень открыт, но данные закрыты")
def _():
    # Здесь была ошибка: префикс "/" в списке открытых путей совпадал
    # с ЛЮБЫМ адресом, и защита отключалась целиком — /api/status
    # отдавался без пароля. Поэтому корень проверяется сравнением
    # целиком, а не как префикс.
    from app.api import auth as api_auth

    assert api_auth.is_public("/") is True
    assert api_auth.is_public("/js/auth.js") is True
    assert api_auth.is_public("/css/styles.css") is True
    # Логотип и фон окна входа лежат в assets/. Без пароля они должны
    # отдаваться: форма входа их рисует, и без них она осталась бы
    # без логотипа — ровно то, что было видно на демонстрации.
    assert api_auth.is_public("/assets/logo.png") is True
    assert api_auth.is_public("/assets/login-bg.svg") is True

    for closed in ("/api/status", "/api/events", "/api/video/stream"):
        assert api_auth.is_public(closed) is False, (
            f"{closed} не должен быть открыт без пароля"
        )


@test("страница входа есть в web/js/auth.js, а не только на сервере")
def _():
    # Проверка идёт на КАЖДЫЙ запрос, и браузер навигацию без заголовка
    # не отправит. Значит форму входа должен уметь показать сам
    # интерфейс — иначе после входа reload снова получил бы её.
    src = (Path(__file__).resolve().parents[1] / "web" / "js" / "auth.js").read_text(
        encoding="utf-8"
    )
    assert "sessionStorage" in src
    assert "pv_auth" in src


@test("защита проверяет каждый запрос, а не только страницу")
def _():
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "auth.py").read_text(
        encoding="utf-8"
    )
    # 401 на данных — то, что действительно закрывает простой.
    assert "401" in src, "данные без входа должны отдавать 401"
    # Видеопоток открывается тегом <img>, а на такой запрос браузер
    # не вешает заголовки. Значит отметка обязана приниматься и из
    # адреса — иначе после входа камера «перестала бы работать».
    assert "SESSION_KEY" in src, "отметка из адреса (видеопоток) не читается"
    assert "query_params" in src


@test("пароль не напечатан на странице входа")
def _():
    # На демонстрации пароль не должен быть виден на экране.
    src = (Path(__file__).resolve().parents[1] / "web" / "js" / "auth.js").read_text(
        encoding="utf-8"
    )
    assert "24201182" not in src, "пароль попал в исходник интерфейса"
    assert "24201182" not in auth.LOGIN_PAGE


@test("интерфейс не рисуется до входа")
def _():
    src = (Path(__file__).resolve().parents[1] / "web" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "auth.ensure" in src, "вход должен проверяться до отрисовки"


# --------------------------------------------------------------------------- #
# Поведение вкладок
# --------------------------------------------------------------------------- #
@test("отметка вкладки — это base64 от логина и пароля")
def _():
    header = auth.make_header(LOGIN, PASSWORD)
    decoded = base64.b64decode(header).decode("utf-8")
    assert decoded == f"{LOGIN}:{PASSWORD}"


@test("ключ отметки — sessionStorage, не localStorage")
def _():
    # Прямое требование: localStorage общий для всего браузера, и новая
    # вкладка не спросила бы пароль. sessionStorage у каждой вкладки
    # свой — именно поэтому выбран он.
    page = auth.LOGIN_PAGE
    assert "sessionStorage" in page, "вход обязан использовать sessionStorage"
    # localStorage не упоминается в коде страницы входа. Проверяем
    # именно это: если бы отметка легла в localStorage, она пережила
    # бы закрытие вкладки и новую вкладку пароль бы не спросил.
    assert "localStorage" not in page, (
        "localStorage пережил бы закрытие вкладки — пароль спросят один раз"
    )


@test("страница входа не отдаёт пароль назад в форму")
def _():
    # Проверяем, что после успешного входа поле пароля очищается:
    # держать его в DOM страницы незачем.
    assert 'document.getElementById("p").value = ""' in auth.LOGIN_PAGE


@test("форма входа — без внешних файлов")
def _():
    # Страница входа отдаётся сервером. Если бы она тянула css/js из
    # web/, то эти файлы закрыты защитой и вход не отрисовался бы.
    assert "<script src=" not in auth.LOGIN_PAGE
    assert "<link" not in auth.LOGIN_PAGE


@test("заголовок доступа называется одинаково в проекте")
def _():
    # Значение заголовка используется и в Python, и в JS. Расхождение
    # означало бы «камера перестала работать после входа».
    assert auth.AUTH_HEADER == "x-promvizor-auth"
    js = (Path(__file__).resolve().parents[1] / "web" / "js" / "api.js").read_text(
        encoding="utf-8"
    )
    assert "X-Promvizor-Auth" in js


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