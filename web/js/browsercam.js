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
     нагрузки, чем поток камеры целиком. */
  const SEND_INTERVAL_MS = 250;

  /* Ширина кадра. Выше 1280 смысла нет: YOLO всё равно смотрит на
     кадр целиком, а трафик растёт квадратично. */
  const TARGET_WIDTH = 1280;

  /* Качество JPEG: 0.75 — картинка достаточно резкая для детекции
     человека и достаточно лёгкая для отправки 4 раза в секунду. */
  const JPEG_QUALITY = 0.75;

  const state = {
    stream: null,
    canvas: null,
    ctx: null,
    timer: null,
    sending: false,
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

  function notify() {
    if (typeof state.onStateChange === "function") {
      state.onStateChange(describe());
    }
  }

  function describe() {
    return {
      active: state.sending,
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
    if (!drawToCanvas(video)) return;
    let blob = null;
    try {
      blob = await new Promise((resolve) =>
        state.canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY)
      );
    } catch (e) {
      /* toBlob может бросить на неподдерживаемом формате — тихо
         пропускаем кадр, следующий попробует снова. */
    }
    if (!blob) return;

    try {
      const response = await fetch(apiBase() + "/api/video/browser/frame", {
        method: "POST",
        headers: { "Content-Type": "image/jpeg" },
        body: blob,
      });
      if (response.ok) {
        state.sent += 1;
        state.lastError = "";
      } else {
        state.lastError = "Сервер ответил " + response.status;
      }
    } catch (e) {
      state.lastError = "Сервер недоступен";
    }
    notify();
  }

  /* Просит камеру и начинает отправлять кадры. Возвращает текст
     ошибки (пустая строка — успех), чтобы вызывающий показал его
     оператору, а не молча проглотил. */
  async function start(videoEl) {
    if (state.sending) return "";

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
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

    const video = videoEl || document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    if (!videoEl) {
      video.srcObject = state.stream;
      try {
        await video.play();
      } catch (e) {
        /* см. выше */
      }
    }

    state.sending = true;
    state.sent = 0;
    state.lastError = "";

    const loop = async () => {
      if (!state.sending) return;
      await sendOnce(video);
      if (!state.sending) return;
      state.timer = setTimeout(loop, SEND_INTERVAL_MS);
    };
    loop();

    /* Уход со вкладки останавливает камеру: иначе в комнате горит
       зелёный индикатор, хотя страницы уже никто не смотрит. */
    document.addEventListener("visibilitychange", onVisibility);

    notify();
    return "";
  }

  function onVisibility() {
    if (document.hidden) {
      stop();
    }
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
    document.removeEventListener("visibilitychange", onVisibility);

    const wasSending = state.sending;
    state.sending = false;
    state.canvas = null;
    state.ctx = null;

    if (wasSending) {
      fetch(apiBase() + "/api/video/browser/stop", { method: "POST" }).catch(
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