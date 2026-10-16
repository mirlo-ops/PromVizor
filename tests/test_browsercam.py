"""Камера из браузера: «живой браузер» и «свежий кадр» — разные вещи.

Тесты закрывают правку от 16.10.2026: окно камеры «зависало на одном
кадре», хотя браузер исправно передавал картинку.

Что было. Сервер считал кадр годным только 0.7 секунды после приёма.
Измерено на реальной странице: между отправками выходило 1.1–1.3 секунды
при времени ответа сервера 60–130 мс — страница занята декодированием
потока и перерисовкой графиков. Каждый кадр приходил «протухшим»,
конвейер получал None, объявлял «нет сигнала с браузера» и не публиковал
новый кадр. Окно показывало прежнюю картинку, а линия ещё и
переключалась в STOPPED.

Что стало. Пока браузер на связи (ALIVE_SECONDS), картинка остаётся
рабочей; «нет сигнала» означает только настоящий обрыв.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from app.video import browsercam

_tests = []


def test(name):
    def wrap(fn):
        _tests.append((name, fn))
        return fn

    return wrap


def _frame(color=90):
    return np.full((180, 320, 3), color, dtype=np.uint8)


def _put(frame=None, size=1000):
    browsercam._SLOT.put(_frame() if frame is None else frame, size)


def _age_to(seconds: float) -> None:
    """«Состарить» последний кадр: подменить время приёма в прошлом."""
    browsercam._SLOT.received_at = time.time() - seconds


def _clean():
    browsercam.reset()
    browsercam._SLOT.frames = 0
    browsercam._SLOT.bytes_in = 0
    browsercam._SLOT.last_interval = 0.0


# --------------------------------------------------------------------------- #
# Пауза между кадрами не должна выглядеть как обрыв
# --------------------------------------------------------------------------- #
@test("кадр в 1.3 с — как на реальной странице — остаётся годным")
def _():
    _clean()
    source = browsercam.BrowserSource().open()
    _put()
    _age_to(1.3)

    # Именно этот случай и выглядел как «зависание на одном кадре»
    assert source.get_frame() is not None, (
        "кадр в 1.3 с отброшен: страница передаёт медленно, это норма"
    )


@test("пауза до 5 с не считается обрывом")
def _():
    _clean()
    source = browsercam.BrowserSource().open()
    _put()
    _age_to(browsercam.ALIVE_SECONDS - 0.2)
    assert source.get_frame() is not None


@test("обрыв объявляется только после ALIVE_SECONDS")
def _():
    _clean()
    source = browsercam.BrowserSource().open()
    _put()
    _age_to(browsercam.ALIVE_SECONDS + 0.5)

    assert source.get_frame() is None, "после обрыва кадра быть не должно"
    assert source.problem, "при обрыве нужна причина, а не пустая строка"


@test("на живой картинке нет надписи «нет сигнала»")
def _():
    _clean()
    source = browsercam.BrowserSource().open()
    _put()
    _age_to(1.3)

    # Баннер поверх живой картинки выглядит как поломка, хотя камера
    # работает. Именно это и было видно на демонстрации.
    assert source.problem == "", f"на живом кадре причина: {source.problem!r}"


@test("подпись источника не обещает кадров при обрыве")
def _():
    _clean()
    source = browsercam.BrowserSource().open()
    _put()
    _age_to(browsercam.ALIVE_SECONDS + 0.5)
    assert "нет сигнала" in source.describe().lower()


# --------------------------------------------------------------------------- #
# Состояние для интерфейса
# --------------------------------------------------------------------------- #
@test("connected и active — разные ответы")
def _():
    _clean()
    _put()
    _age_to(1.3)
    info = browsercam.snapshot()

    assert info["connected"] is True, "браузер на связи — это connected"
    assert info["active"] is False, "кадр старше 0.7 с — это не active"


@test("connected становится False при обрыве")
def _():
    _clean()
    _put()
    _age_to(browsercam.ALIVE_SECONDS + 1.0)
    assert browsercam.snapshot()["connected"] is False


@test("до первого кадра ничего не подключено")
def _():
    _clean()
    info = browsercam.snapshot()
    assert info["connected"] is False and info["active"] is False
    assert info["frames"] == 0


@test("окно живучести больше окна свежести")
def _():
    # Иначе разделение бессмысленно: это единственная причина, по
    # которой медленная передача перестала считаться обрывом.
    assert browsercam.ALIVE_SECONDS > browsercam.FRESH_SECONDS


# --------------------------------------------------------------------------- #
# Повторная обработка одного и того же кадра
# --------------------------------------------------------------------------- #
@test("повторно тот же кадр помечается как уже обработанный")
def _():
    # Пока браузер молчит, get_frame() отдаёт прежнюю картинку. Без
    # этой отметки конвейер гонял бы YOLO по ней впустую.
    _clean()
    source = browsercam.BrowserSource().open()
    _put()

    token = source.frame_token()
    assert source.already_processed(token) is False, "первый кадр новый"
    assert source.already_processed(token) is True, "повтор должен пропускаться"


@test("новый кадр снова считается новым")
def _():
    _clean()
    source = browsercam.BrowserSource().open()
    _put()
    source.already_processed(source.frame_token())

    _put()  # пришёл следующий кадр
    assert source.already_processed(source.frame_token()) is False


@test("после переподключения прошлый кадр не помечается обработанным")
def _():
    # Иначе после паузы конвейер пропустил бы первый кадр и показал
    # пустое или старое окно.
    _clean()
    _put()
    source = browsercam.BrowserSource()
    source.open()
    token = source.frame_token()
    source.already_processed(token)

    source.open()  # переподключение
    assert source.already_processed(token) is False, (
        "после переподключения кадр надо обработать заново"
    )


@test("без метки токена ничего не пропускается")
def _():
    # Обычные источники (камера, ролики) метки не имеют — конвейер
    # обязан обрабатывать каждый кадр.
    _clean()
    source = browsercam.BrowserSource().open()
    assert source.already_processed(None) is False


# --------------------------------------------------------------------------- #
# Приём кадра
# --------------------------------------------------------------------------- #
@test("принимается JPEG и виден источнику")
def _():
    import cv2

    _clean()
    ok, buf = cv2.imencode(".jpg", _frame(color=200))
    assert browsercam.submit_jpeg(buf.tobytes()) is True
    assert browsercam.BrowserSource().open().get_frame() is not None


@test("мусор не принимается")
def _():
    _clean()
    # Не картинка: сервер должен отвергнуть, а не положить в слот
    assert browsercam.submit_jpeg(b"not an image at all") is False


@test("пустой кадр не принимается")
def _():
    _clean()
    assert browsercam.submit_jpeg(b"") is False


@test("reset забывает кадр — окно гаснет сразу")
def _():
    # Выключение передачи должно быть видно немедленно, а не через
    # ALIVE_SECONDS: иначе камера «продолжала бы работать» после
    # выключения.
    _clean()
    _put()
    browsercam.reset()
    source = browsercam.BrowserSource().open()
    assert source.get_frame() is None
    assert source.problem


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

    _clean()
    print(f"\n{len(_tests) - len(failed)}/{len(_tests)} тестов пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())