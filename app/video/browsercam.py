"""Видео из браузера пользователя — режим «Веб-камера (браузер)».

Зачем этот режим. Сервер считает всё: кадры, YOLO, движение, простой,
ущерб. Но камера не обязана быть у сервера — она может быть у человека:
ноутбук в аудитории, планшет в цеху, камера ноутбука прямо на паре.
Прежний режим «Веб-камера» открывал устройство НА СЕРВЕРЕ, а на
хостинге там камеры нет, и режим был мёртвым: всегда «нет сигнала».

Как работает. Браузер просит доступ к камере (getUserMedia), рисует
кадр на canvas и отправляет JPEG обычным POST на
`/api/video/browser/frame`. Здесь кадр декодируется и кладётся в общий
слот, откуда его забирает конвейер как обычный источник видео.

Что остаётся на стороне сервера: YOLO, ROI, детектор движения, Event
Engine, таймер простоя, расчёт ущерба, база, API. В браузере не
считается ничего — только передача картинки.

Живучесть. Различаются два состояния: браузер ЖИВ (последний кадр
получен меньше ALIVE_SECONDS назад) и кадр СВЕЖ (меньше FRESH_SECONDS
назад). Пока браузер жив, окно показывает последнюю картинку и
конвейер продолжает считать; «нет сигнала с браузера» появляется
только при настоящем обрыве — и источник при этом НЕ закрывается:
браузер может вернуться через минуту, и конвейер продолжит с того же
места.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from app.video.source import VideoSource

#: Сколько секунд кадр считается ТОЛЬКО ЧТО ПРИШЁЛ. Это честная мера
#: «картинка свежая»: браузер шлёт кадры несколько раз в секунду.
FRESH_SECONDS = 0.7

#: Сколько секунд браузер считается ЖИВЫМ, даже если нового кадра
#: пока нет.
#:
#: Почему одного «свежего» окна не хватало. Страница с камерой занята:
#: она декодирует MJPEG-поток в окно, перерисовывает графики и сама
#: кодирует кадр в JPEG. Измерено на реальной странице: между
#: отправками выходило 1.1–1.3 секунды при времени ответа сервера
#: 60–130 мс. То есть браузер передавал исправно, но медленно.
#:
#: При окне в 0.7 секунды сервер отбрасывал практически каждый кадр:
#: конвейер получал None, объявлял «нет сигнала с браузера» и НЕ
#: публиковал новый кадр. Окно на дашборде показывало прежнюю
#: картинку — выглядело это как «камера зависла на одном кадре», хотя
#: передача шла. Заодно человек считался отсутствующим, и линия
#: переключалась в STOPPED на живой камере.
#:
#: Теперь «живой браузер» и «свежий кадр» — разные вещи. Пока браузер
#: молчит меньше ALIVE_SECONDS, картинка продолжает считаться рабочей,
#: и в окне остаётся последний кадр. Когда молчание становится
#: настоящей потерей связи, объявляется «нет сигнала».
ALIVE_SECONDS = 5.0

#: Имя старого окна оставлено для совместимости с вызовом в коде и в
#: тестах: смысл у него теперь «кадр только что пришёл».
GRACE_SECONDS = FRESH_SECONDS

#: Номинальная частота кадров для расчёта паузы конвейера. Реальная
#: задаётся браузером и YOLO (они медленнее), поэтому это верхняя
#: граница, а не обещание.
NOMINAL_FPS = 5.0


@dataclass
class _Slot:
    """Общее хранилище последнего кадра браузера."""

    frame: Optional[Any] = None
    received_at: float = 0.0
    frames: int = 0
    bytes_in: int = 0
    last_interval: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, frame, size: int) -> None:
        now = time.time()
        with self.lock:
            if self.received_at:
                gap = now - self.received_at
                # Медиана не нужна: сглаживание двумя кадрами достаточно,
                # а счётчик «пропусков» между вкладками путает.
                self.last_interval = gap if self.last_interval == 0 else (
                    0.7 * self.last_interval + 0.3 * gap
                )
            self.frame = frame
            self.received_at = now
            self.frames += 1
            self.bytes_in += size

    def take(self, max_age: float) -> Tuple[Optional[Any], float]:
        with self.lock:
            if self.frame is None or self.received_at == 0.0:
                return None, float("inf")
            age = time.time() - self.received_at
            return (self.frame, age) if age <= max_age else (None, age)

    def clear(self) -> None:
        with self.lock:
            self.frame = None
            self.received_at = 0.0


#: Единственный слот на процесс: режим «браузер» в системе один.
_SLOT = _Slot()


def submit_jpeg(payload: bytes) -> bool:
    """Принимает JPEG из браузера. True — кадр принят и декодирован."""
    import cv2

    if not payload:
        return False
    buffer = np_frombuffer(payload)
    frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        return False
    _SLOT.put(frame, len(payload))
    return True


def np_frombuffer(payload: bytes):
    """bytes → numpy без лишней копии там, где возможно."""
    import numpy as np

    return np.frombuffer(payload, dtype=np.uint8)


def latest(max_age: float = ALIVE_SECONDS) -> Tuple[Optional[Any], float]:
    """Последний кадр и его возраст в секундах."""
    return _SLOT.take(max_age)


def snapshot() -> Dict[str, Any]:
    """Состояние передачи — для /api/video/state и интерфейса.

    Два разных ответа на два разных вопроса:

    * ``connected`` — браузер на связи (молчание меньше ALIVE_SECONDS).
      Именно по нему решается, показывать ли картинку и не ли «нет
      сигнала»;
    * ``active`` — кадр только что пришёл (меньше FRESH_SECONDS). Это
      про скорость передачи, а не про её наличие.
    """
    with _SLOT.lock:
        frames = _SLOT.frames
        received_at = _SLOT.received_at
        interval = _SLOT.last_interval
        shape = None if _SLOT.frame is None else _SLOT.frame.shape[:2]

    age = time.time() - received_at if received_at else None
    return {
        "connected": age is not None and age <= ALIVE_SECONDS,
        "active": age is not None and age <= FRESH_SECONDS,
        "frames": frames,
        "age_seconds": round(age, 2) if age is not None else None,
        "fps": round(1.0 / interval, 2) if interval > 0 else 0.0,
        "size": [int(shape[1]), int(shape[0])] if shape else None,
    }


def reset() -> None:
    """Забыть кадр: режим выключен или сменился."""
    _SLOT.clear()


class BrowserSource(VideoSource):
    """Источник видео, кадры которого приходят из браузера.

    Открывается всегда: пока браузер не подключился, источник честно
    не отдаёт кадр, и конвейер показывает «нет сигнала с браузера».
    Это лучше, чем попытка открыть камеру и упасть.
    """

    source_type = "browser"
    is_demo = False

    def __init__(self) -> None:
        super().__init__()
        self._opened = False
        self._processed_token: Optional[int] = None
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def open(self) -> "BrowserSource":
        self._opened = True
        with self._state_lock:
            self._processed_token = None
        return self

    def close(self) -> None:
        self._opened = False
        with self._state_lock:
            self._processed_token = None

    def is_opened(self) -> bool:
        return self._opened

    def get_frame(self) -> Optional[Any]:
        """Последний кадр браузера.

        Кадр отдаётся, пока браузер на связи (ALIVE_SECONDS с
        последнего кадра), а не только пока он «совсем свежий».
        Иначе пауза между кадрами в полторы секунды — обычное дело для
        занятой страницы — выглядела бы как обрыв связи, и картинка в
        окне замирала на последнем кадре.
        """
        frame, _age = latest(max_age=ALIVE_SECONDS)
        return frame

    def frame_token(self) -> Optional[int]:
        """Номер последнего принятого кадра.

        Пока браузер молчит, get_frame() отдаёт прежнюю картинку.
        Номер нужен, чтобы конвейер не гонял YOLO по одному и тому же
        кадру несколько раз в секунду впустую.
        """
        with _SLOT.lock:
            return _SLOT.frames

    def already_processed(self, token: Optional[int]) -> bool:
        """Этот кадр конвейер уже видел. True — обрабатывать нечего."""
        if token is None:
            return False
        with self._state_lock:
            if token == self._processed_token:
                return True
            self._processed_token = token
            return False

    def latest_frame_snapshot(self):
        """Кадр вместе с меткой — для плавной перерисовки окна.

        Меткой служит СЧЁТЧИК кадров, а не время приёма: окно
        спрашивает кадр много раз в секунду, и по времени разметка
        перерисовывалась бы заново на каждый запрос. По счётчику — только
        когда пришёл действительно новый кадр.
        """
        frame, _age = latest(max_age=ALIVE_SECONDS)
        if frame is None:
            return None
        with _SLOT.lock:
            counter = _SLOT.frames
        return frame, counter

    # ------------------------------------------------------------------ #
    @property
    def fps(self) -> float:
        info = snapshot()
        return float(info["fps"] or NOMINAL_FPS)

    @property
    def width(self) -> int:
        size = snapshot()["size"]
        return int(size[0]) if size else 0

    @property
    def height(self) -> int:
        size = snapshot()["size"]
        return int(size[1]) if size else 0

    @property
    def problem(self) -> str:
        """Причина «нет сигнала» — показывается прямо в окне камеры.

        Пустая строка, пока браузер на связи. Раньше проверялась
        «свежесть» кадра (0.7 с), и при паузе между кадрами в полторы
        секунды — обычной для занятой страницы — на живой картинке
        появлялся баннер «НЕТ СИГНАЛА». Теперь баннер означает только
        настоящий обрыв: браузер молчит дольше ALIVE_SECONDS.
        """
        info = snapshot()
        if info["connected"]:
            return ""
        if info["frames"] == 0:
            return "Браузер не передаёт кадры: разрешите доступ к камере"
        return "Передача с браузера прервалась"

    def describe(self) -> str:
        info = snapshot()
        if info["frames"] == 0:
            return "Веб-камера браузера (ожидаем кадры)"
        size = info["size"] or [0, 0]
        if not info["connected"]:
            return f"Веб-камера браузера {size[0]}x{size[1]} (нет сигнала)"
        return (
            f"Веб-камера браузера {size[0]}x{size[1]} "
            f"({info['fps']:.1f} к/с)"
        )


__all__ = [
    "BrowserSource",
    "submit_jpeg",
    "latest",
    "snapshot",
    "reset",
    "GRACE_SECONDS",
    "FRESH_SECONDS",
    "ALIVE_SECONDS",
]