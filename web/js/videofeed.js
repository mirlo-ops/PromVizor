/* ============================================================
   ПромВизор — показ видеопотока на Dashboard
   ------------------------------------------------------------
   Зачем отдельный модуль. Раньше окно камеры было тегом
   <img src="/api/video/stream">, и браузер сам разбирал поток MJPEG.

   Так работает не везде. На части машин Chrome перестаёт
   перерисовывать <img> с бесконечным multipart-потоком: поток на
   сервере продолжает идти, кадры приходят, а на экране навсегда
   остаётся первый. Ровно это и было: оператор смотрел на неподвижную
   картинку, хотя счётчик принятых кадров рос.

   Почему нельзя просто перезапрашивать картинку. Раньше поверх
   <img> был сторож: если за 8 секунд не пришёл первый кадр, адрес
   перезапрашивался. Каждая перезапроска открывает НОВОЕ соединение
   к потоку, а старое браузер закрывает не сразу. Соединения
   накапливались, и в какой-то момент новые переставали открываться
   совсем — картинка не появлялась никогда. На сервере это выглядело
   как «сервис не может остановиться 90 секунд»: клиенты висели.

   Теперь поток читает сам модуль через fetch(), кадры рисуются на
   canvas, а соединение в любой момент можно закрыть самому — одним
   AbortController. Никаких скрытых подключений не остаётся.

   Побочная польза: модуль знает, сколько кадров пришло и когда был
   последний. Это видно оператору прямо в окне, и по этому сразу
   понятно, что именно сломано, без догадок.
   ============================================================ */
(function (global) {
  "use strict";

  /* Поток молчит дольше этого — считаем его оборванным и переподключаемся.
     Секунда с запасом: между кадрами на медленной камере бывают паузы. */
  const STALL_MS = 1200;

  /* Пауза перед переподключением. Часто трогать нельзя: каждый
     переподключение — новое соединение, и при частых попытках
     браузер упирается в лимит соединений к хосту. */
  const RETRY_MS = 2000;

  const state = {
    canvas: null,
    ctx: null,
    abort: null,
    timer: null,
    /* Счётчик для проверки, что рисуем именно то, что пришло:
       пересоздавать картинку на каждый кадр незачем */
    image: null,
    frames: 0,
    lastFrameAt: 0,
    startedAt: 0,
    stopped: true,
    onStatus: null,
  };

  /* Создаёт объект-картинку один раз и переиспользует его: иначе на
     каждом кадре создаётся новый Image и сборщик мусора со временем
     подъедает память вкладки. */
  function image() {
    if (!state.image) state.image = new Image();
    return state.image;
  }

  function notify() {
    if (typeof state.onStatus !== "function") return;
    state.onStatus({
      frames: state.frames,
      lastFrameAgo: state.lastFrameAt
        ? Math.round((Date.now() - state.lastFrameAt) / 100) / 10
        : null,
      alive: !state.stopped && state.frames > 0,
    });
  }

  /* Рисует пришедший кадр. Ошибка декодирования игнорируется: битый
     кадр не должен рвать поток, следующий придёт нормальным. */
  function paint(bytes) {
    const img = image();
    /* Blob собирается из массива байт, а не из строки. Строка в JS —
       это текст в кодировке UTF-16: прошедшие через неё байты JPEG
       портятся, и картинка не декодировалась НИКОГДА. На это ушло
       больше времени, чем на саму поломку. */
    const blob = new Blob([bytes], { type: "image/jpeg" });
    const url = URL.createObjectURL(blob);
    img.onload = function () {
      URL.revokeObjectURL(url);
      if (!state.canvas || !state.ctx) return;
      if (state.canvas.width !== img.width || state.canvas.height !== img.height) {
        state.canvas.width = img.width;
        state.canvas.height = img.height;
      }
      state.ctx.drawImage(img, 0, 0);
      state.frames += 1;
      state.lastFrameAt = Date.now();
      notify();
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
    };
    img.src = url;
  }

  /* Конец блока заголовков: \r\n\r\n. Ищем по байтам — текстовые
     функции здесь неприменимы, см. paint(). */
  function findHeaderEnd(bytes, from) {
    for (let i = from; i <= bytes.length - 4; i++) {
      if (
        bytes[i] === 13 &&
        bytes[i + 1] === 10 &&
        bytes[i + 2] === 13 &&
        bytes[i + 3] === 10
      ) {
        return i;
      }
    }
    return -1;
  }

  /* Разбирает поток частями multipart и отдаёт каждый кадр.
     Всё делается на байтах: заголовок читаем как текст (он ASCII),
     тело кадра — как есть. */
  function consume(stream, onFrame) {
    const reader = stream.getReader();
    const headerDecoder = new TextDecoder();
    let buffer = new Uint8Array(0);

    function append(chunk) {
      const merged = new Uint8Array(buffer.length + chunk.length);
      merged.set(buffer, 0);
      merged.set(chunk, buffer.length);
      buffer = merged;
    }

    function takeFrom(start, length) {
      /* Копия обязательна: buffer переиспользуется следующими кадрами */
      return buffer.slice(start, start + length);
    }

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) return null;
        append(result.value);

        let guard = 0;
        while (guard++ < 64) {
          const headerEnd = findHeaderEnd(buffer, 0);
          if (headerEnd === -1) break;

          const headerText = headerDecoder.decode(buffer.subarray(0, headerEnd));
          const match = /content-length:\s*(\d+)/i.exec(headerText);
          if (!match) {
            /* Не часть с кадром (например, разделитель): пропускаем
               блок заголовков и идём дальше с тела. */
            buffer = buffer.subarray(headerEnd + 4);
            continue;
          }

          const length = parseInt(match[1], 10);
          const bodyStart = headerEnd + 4;
          const bodyEnd = bodyStart + length;
          if (buffer.length < bodyEnd) break;

          onFrame(takeFrom(bodyStart, length));
          /* Пропускаем закрывающий \r\n части */
          buffer = buffer.subarray(bodyEnd + 2);
        }
        return pump();
      });
    }

    return pump();
  }

  /* Следит за потоком: если кадры перестали приходить — соединение
     закрываем и открываем заново. Раньше обрыв был молчаливым: поток
     висел, картинка стояла, и оператор не знал, что происходит. */
  function watchStall() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(function () {
      if (state.stopped) return;
      const silence = state.lastFrameAt
        ? Date.now() - state.lastFrameAt
        : Date.now() - state.startedAt;

      /* Больше STALL_MS тишины — поток надо переоткрыть.
         Раньше здесь стояло просто notify(), и поток, который тихо
         закончился, больше никогда не открывался: экран замирал
         навсегда, притом что камера продолжала слать кадры на сервер.

         Тишины на «мёртвой» камере не бывает: сервер в этом случае
         присылает заглушку раз в секунду. Значит молчание означает
         именно обрыв, и ждать тут нечего. */
      if (silence > STALL_MS) {
        open();
        return;
      }
      watchStall();
    }, STALL_MS);
  }

  function streamUrl() {
    const api = global.PV && global.PV.api;
    const base = api && typeof api.baseUrl === "function" ? api.baseUrl() : "";
    let url = base + "/api/video/stream";
    if (api && typeof api.authToken === "function") {
      const token = api.authToken();
      if (token) url += "?pv_auth=" + encodeURIComponent(token);
    }
    return url;
  }

  async function open() {
    /* От старого соединения ВСЕГДА остаёмся чисты. Без этого каждая
       перерисовка панели оставляла после себя висящую связь, и через
       несколько минут браузер переставал открывать новые. */
    close();
    state.stopped = false;
    state.startedAt = Date.now();
    state.canvas = document.getElementById("videoFeed");
    if (!state.canvas || state.canvas.tagName !== "CANVAS") return;
    if (!state.canvas.getContext) return;
    state.ctx = state.canvas.getContext("2d");

    const controller = new AbortController();
    state.abort = controller;

    /* Оба исхода — и ошибка, и «поток закончился штатно» — означают
       одно: соединения больше нет. Раньше обрабатывалась только
       ошибка, а спокойное завершение потока проходило мимо, и окно
       замирало навсегда. */
    const reconnect = function () {
      if (state.stopped) return;
      state.abort = null;
      setTimeout(open, RETRY_MS);
    };

    try {
      const response = await fetch(streamUrl(), {
        signal: controller.signal,
        cache: "no-store",
      });
      if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
      consume(response.body, paint)
        .then(reconnect)
        .catch(reconnect);
    } catch (e) {
      reconnect();
    }
    watchStall();
  }

  /* Закрывает соединение начисто. Именно этого не хватало раньше. */
  function close() {
    state.stopped = true;
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
    if (state.abort) {
      try {
        state.abort.abort();
      } catch (e) {
        /* уже закрыт */
      }
      state.abort = null;
    }
  }

  /* Подключает canvas в только что отрисованную панель. Элемент
     сохраняется между перерисовками, поэтому и поток не рвётся. */
  function attach(canvasEl) {
    if (state.canvas !== canvasEl) {
      close();
      state.canvas = canvasEl || null;
      state.ctx = state.canvas ? state.canvas.getContext("2d") : null;
      state.frames = 0;
      state.lastFrameAt = 0;
      if (state.canvas) open();
    }
  }

  global.PV = global.PV || {};
  global.PV.videofeed = {
    open: open,
    close: close,
    attach: attach,
    onStatus: function (fn) {
      state.onStatus = fn;
    },
    stats: function () {
      return {
        frames: state.frames,
        lastFrameAgo: state.lastFrameAt
          ? Math.round((Date.now() - state.lastFrameAt) / 100) / 10
          : null,
      };
    },
  };
})(window);