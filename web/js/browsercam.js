/* ============================================================
   ПромВизор — камера из браузера
   ------------------------------------------------------------
   РЕЖИМ «ВЕБ-КАМЕРА (ИЗ БРАУЗЕРА)».

   Как это устроено. Камера может быть не у сервера, а у человека:
   ноутбук в аудитории, планшет в цеху. Браузер просит доступ к камере
   (getUserMedia), рисует кадр на canvas и отправляет JPEG на сервер.
   ВСЮ обработку делает сервер: YOLO, ROI, детектор движения, простой,
   ущерб, база. Здесь нет ни одной формулы и ни одного вычисления про
   простой — только передача картинки.

   Что важно знать:
   * getUserMedia работает только по HTTPS (кроме localhost). Поэтому
     сервер обязательно должен быть за сертификатом — иначе браузер
     откажет в доступе к камере даже с разрешения пользователя.
   * Кадры шлются не чаще, чем успевает обработать сервер. Слишком
     часто — значит тратить трафик и задерживать очередь.
   * Ничего не считается в браузере: даже «сколько кадров отправлено»
     показывается для информации, но ни на что не влияет.
   ============================================================ */
(function (global) {
  "use strict";

  /* Частота отправки, мс. 250 мс ≈ 4 к/с — этого достаточно, чтобы
     конвейер на сервере видел живую картинку, и заметно меньше
     нагрузки, чем поток камеры целиком.

     Пауза отсчитывается от КОНЦА отправки, а не добавляется к ней.
     Раньше цикл ждал ещё 250 мс после каждого запроса, поэтому при
     времени ответа 120 мс реальный интервал выходил 370 мс, а под
     нагрузкой страницы — больше секунды. */
  const SEND_INTERVAL_MS = 250;

  /* Предел ожидания одного запроса, мс.

     Без него зависший POST останавливал передачу навсегда: цикл ждал
     ответа, ответа не было, и картинка в окне замирала на одном кадре
     без всякой ошибки. Обрыв по таймауту виден и поправляется сам. */
  const SEND_TIMEOUT_MS = 4000;

  /* Ниже этого интервала кадры не шлём: если страница не успевает,
     лучше реже, чем копить запросы в очередь. */
  const MIN_INTERVAL_MS = 120;

  /* Ширина кадра. Выше 1280 смысла нет: YOLO всё равно смотрит на
     кадр целиком, а трафик растёт квадратично. */
  const TARGET_WIDTH = 1280;

  /* Качество JPEG: 0.75 — картинка достаточно резкая для детекции
     человека и достаточно лёгкая для отправки 4 раза в секунду. */
  const JPEG_QUALITY = 0.75;

  const state = {
    stream: null,
    video: null,
    canvas: null,
    ctx: null,
    timer: null,
    preview: null,
    sending: false,
    /* Флаг «запуск уже идёт». Без него два быстрых нажатия «Включить»
       запускали две передачи одновременно, и они дрались за камеру. */
    starting: false,
    /* Передача приостановлена, потому что вкладку скрыли */
    paused: false,
    sent: 0,
    lastError: "",
    onStateChange: null,
  };

  function apiBase() {
    if (global.PV && global.PV.api && global.PV.api.baseUrl) {
      return global.PV.api.baseUrl();
    }
    return "";
  }

  /* Отметка о входе в эту вкладку (sessionStorage — он общий на
     вкладку, а не на весь браузер). Берём через api.js, если он
     загружен, иначе читаем хранилище сами. */
  function headersWithAuth(extra) {
    const headers = extra || {};
    let token = "";
    if (global.PV && global.PV.api && global.PV.api.authToken) {
      token = global.PV.api.authToken();
    } else {
      try {
        token = sessionStorage.getItem("pv_auth") || "";
      } catch (e) {
        token = "";
      }
    }
    if (token) headers["X-Promvizor-Auth"] = token;
    return headers;
  }

  function notify() {
    if (typeof state.onStateChange === "function") {
      state.onStateChange(describe());
    }
  }

  function describe() {
    return {
      active: state.sending,
      /* Передача на паузе, но камера включена: так это и должно
         выглядеть в интерфейсе, иначе получится «камера выключена»
         в момент, когда она просто ждёт возврата на вкладку. */
      paused: state.paused,
      sent: state.sent,
      error: state.lastError,
    };
  }

  /* Кадр сжимается по максимальной стороне: ноутбук отдаёт 1920,
     телефон — 4032, а серверу достаточно 1280. */
  function drawToCanvas(video) {
    const vw = video.videoWidth || video.width || 0;
    const vh = video.videoHeight || video.height || 0;
    if (!vw || !vh) return false;

    const scale = Math.min(1, TARGET_WIDTH / Math.max(vw, vh));
    const width = Math.round(vw * scale);
    const height = Math.round(vh * scale);

    if (!state.canvas) {
      state.canvas = document.createElement("canvas");
      state.ctx = state.canvas.getContext("2d");
    }
    if (state.canvas.width !== width || state.canvas.height !== height) {
      state.canvas.width = width;
      state.canvas.height = height;
    }
    state.ctx.drawImage(video, 0, 0, width, height);
    return true;
  }

  async function sendOnce(video) {
    /* Возвращает, сколько миллисекунд заняла отправка: цикл использует
       это, чтобы держать интервал, а не добавлять паузу к времени
       ответа. */
    if (!drawToCanvas(video)) return 0;
    let blob = null;
    try {
      blob = await new Promise((resolve) =>
        state.canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY)
      );
    } catch (e) {
      /* toBlob может бросить на неподдерживаемом формате — тихо
         пропускаем кадр, следующий попробует снова. */
    }
    if (!blob) return 0;

    /* Отменяем запрос, если он завис: иначе передача встаёт навсегда,
       и оператор видит неподвижную картинку без объяснений. */
    const controller =
      typeof AbortController === "function" ? new AbortController() : null;
    let timedOut = false;
    if (controller) {
      setTimeout(function () {
        timedOut = true;
        controller.abort();
      }, SEND_TIMEOUT_MS);
    }

    try {
      const options = {
        method: "POST",
        /* Отметка о входе: без неё кадр не примет ни одна защита,
           и камера «перестала работать» после входа. */
        headers: headersWithAuth({ "Content-Type": "image/jpeg" }),
        body: blob,
      };
      if (controller) options.signal = controller.signal;
      const response = await fetch(
        apiBase() + "/api/video/browser/frame",
        options
      );
      if (response.ok) {
        state.sent += 1;
        state.lastError = "";
      } else if (response.status === 401) {
        /* Молчание здесь выглядело бы как «камера не работает».
           Причина простая: пароль в этой вкладке снялся. */
        state.lastError = "Сеанс истёк: обновите страницу и войдите снова";
      } else {
        state.lastError = "Сервер ответил " + response.status;
      }
    } catch (e) {
      state.lastError = timedOut
        ? "Сервер не отвечает, передача ждёт"
        : "Сервер недоступен";
    }
    notify();
    return timedOut ? SEND_TIMEOUT_MS : 0;
  }

  /* Просит камеру и начинает отправлять кадры. Возвращает текст
     ошибки (пустая строка — успех), чтобы вызывающий показал его
     оператору, а не молча проглотил. */
  async function start(videoEl) {
    if (state.sending || state.starting) return "";
    state.starting = true;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      state.starting = false;
      return (
        "Браузер не умеет отдавать камеру на страницу. " +
        "Нужен HTTPS: по http камера недоступна из принципа."
      );
    }

    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          facingMode: "user",
        },
        audio: false,
      });
    } catch (e) {
      const name = (e && e.name) || "";
      state.starting = false;
      if (name === "NotAllowedError" || name === "SecurityError") {
        return "Доступ к камере не разрешён. Разрешите его в браузере и повторите.";
      }
      if (name === "NotFoundError") {
        return "Камера не найдена: в этом компьютере её нет.";
      }
      if (name === "NotReadableError") {
        return "Камера занята другой программой (браузер, Zoom, видеоредактор).";
      }
      return "Камеру открыть не удалось: " + name;
    }

    if (videoEl) {
      videoEl.srcObject = state.stream;
      /* muted обязателен: иначе браузер требует жеста пользователя
         перед показом картинки. */
      videoEl.muted = true;
      videoEl.playsInline = true;
      try {
        await videoEl.play();
      } catch (e) {
        /* автозапуск мог быть запрещён — на показ влияет только картинка
           в окне камеры с сервера, поэтому это не ошибка */
      }
    }

    /* Не привязываем цикл передачи к элементу превью настроек: после
       применения режима интерфейс перерисовывается и удаляет этот
       элемент из документа. На некоторых браузерах отсоединённое видео
       перестаёт декодировать кадры, поэтому отправляется только первый.
       У захвата свой постоянно живущий video-элемент; превью остаётся
       отдельным и может безопасно пересоздаваться интерфейсом. */
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.autoplay = true;
    video.setAttribute("aria-hidden", "true");
    video.tabIndex = -1;
    /* Элемент должен оставаться в документе: некоторые браузеры
       замедляют/останавливают декодирование у отсоединённого video. */
    video.style.position = "fixed";
    video.style.left = "-2px";
    video.style.top = "0";
    video.style.width = "1px";
    video.style.height = "1px";
    video.style.opacity = "0";
    video.style.pointerEvents = "none";
    video.srcObject = state.stream;
    document.body.appendChild(video);
    try {
      await video.play();
    } catch (e) {
      /* см. выше: если автозапуск запрещён, цикл повторит попытку */
    }

    state.sending = true;
    state.starting = false;
    state.paused = false;
    state.sent = 0;
    state.lastError = "";
    state.video = video;
    state.preview = videoEl || null;

    document.addEventListener("visibilitychange", onVisibility);

    kick();
    notify();
    return "";
  }

  /* Один шаг передачи: ждём нового кадра камеры (если браузер умеет
     сообщать о нём), отправляем его и планируем следующий шаг.

     Отправки идут одна за другой, пауза считается от КОНЦА
     предыдущей, поэтому интервал держится около SEND_INTERVAL_MS
     независимо от времени ответа. Обрыв по таймауту цикл не
     завершает: следующий шаг попробует снова. */
  function loopOnce() {
    if (!state.sending || state.paused) return;
    const video = state.video;
    if (!video) return;

    const send = function () {
      sendOnce(video)
        .then(function (took) {
          if (!state.sending || state.paused) return;
          state.timer = setTimeout(
            loopOnce,
            Math.max(MIN_INTERVAL_MS, SEND_INTERVAL_MS - took)
          );
        })
        .catch(function () {
          /* Странная ошибка не должна рвать цикл: иначе передача
             прекратится молча и картинка в окне замрёт. */
          if (state.sending && !state.paused) {
            state.timer = setTimeout(loopOnce, SEND_INTERVAL_MS);
          }
        });
    };

    /* Браузер умеет сообщать «в камере пришёл новый кадр». Если он это
       умеет, отправляем только такие кадры: в неподвижной сцене это
       экономит кодирование и трафик, а движение попадает в кадр сразу,
       не дожидаясь таймера. */
    if (typeof video.requestVideoFrameCallback === "function") {
      video.requestVideoFrameCallback(send);
      return;
    }

    state.timer = setTimeout(send, 0);
  }

  function onVisibility() {
    /* Вкладку скрыли — не выключаем камеру, а приостанавливаем
       передачу. Раньше здесь был полный stop(), и это ломало
       демонстрацию: чтобы показать бота в Telegram, оператор
       переключался на другую вкладку, после чего камера гасла
       насмерть и «зависала на одном кадре». Теперь камера остаётся
       включённой, а цикл возобновляется при возврате на вкладку. */
    if (document.hidden) {
      pause();
    } else {
      resume();
    }
  }

  /* Приостанавливает передачу, не трогая камеру. */
  function pause() {
    if (!state.sending || state.paused) return;
    state.paused = true;
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
  }

  function resume() {
    if (!state.sending || !state.paused) return;
    state.paused = false;
    kick();
  }

  /* Запускает цикл передачи. Вынесено отдельно, чтобы перезапуск после
     паузы и первый запуск делали одно и то же. */
  function kick() {
    if (!state.sending || state.paused) return;
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
    state.timer = setTimeout(loopOnce, 0);
  }

  /* Останавливает передачу и выключает камеру. Сообщает серверу,
     чтобы окно показало «нет сигнала» сразу, а не через полсекунды. */
  function stop() {
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    if (state.video) {
      state.video.srcObject = null;
      state.video.remove();
    }
    if (state.preview) state.preview.srcObject = null;
    if (state.preview) state.preview = null;
    document.removeEventListener("visibilitychange", onVisibility);

    const wasSending = state.sending;
    state.sending = false;
    state.paused = false;
    state.starting = false;
    state.video = null;
    state.preview = null;
    state.canvas = null;
    state.ctx = null;

    if (wasSending) {
      fetch(apiBase() + "/api/video/browser/stop", {
        method: "POST",
        headers: headersWithAuth(),
      }).catch(
        function () {
          /* сервер мог уже не отвечать — молча, камеру мы уже выключили */
        }
      );
    }
    notify();
  }

  function isActive() {
    return state.sending;
  }

  global.PV = global.PV || {};
  global.PV.browsercam = {
    start: start,
    stop: stop,
    isActive: isActive,
    describe: describe,
    onStateChange: function (fn) {
      state.onStateChange = fn;
    },
  };
})(window);
