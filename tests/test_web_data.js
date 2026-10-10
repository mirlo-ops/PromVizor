/* ============================================================
   Тесты веб-слоя Dashboard (Этап 7)

   Запуск:
     node tests/test_web_data.js

   Проверяется логика `web/js/data.js` и `web/js/api.js` в Node:
   DOM не нужен — оба файла работают только с переданными данными
   и localStorage (в тесте подменяется заглушкой).

   Зачем эти тесты. Логика перевода ответа API в форму интерфейса
   живёт в браузере, и без проверки её ошибки видны только глазами
   на готовом экране. Здесь же ловятся сразу.

   В первую очередь проверяется запрет на пересчёт ущерба:
   frontend имеет право только показать готовое число (Handoff,
   раздел 14).
   ============================================================ */

const fs = require("fs");
const path = require("path");

const WEB = path.join(__dirname, "..", "web", "js");

/* ---------- загрузка модулей в окружении браузера ---------- */
function loadModules(fetchImpl) {
  const store = {};
  const sandbox = {
    window: {
      location: { href: "http://127.0.0.1:8000/" },
      localStorage: {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => (store[k] = String(v)),
        removeItem: (k) => delete store[k],
      },
    },
    localStorage: null,
    /* В контексте vm нет браузерных глобалов — их нужно передать
       явно, иначе api.js упадёт на URL и таймауте. */
    URL: URL,
    AbortController: AbortController,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    console: console,
    /* fetch передаётся до создания контекста: подменить его позже,
       уже после vm.createContext, нельзя — модуль его не увидит. */
    fetch: fetchImpl || (() => Promise.reject(new Error("сеть недоступна в тестах"))),
  };
  sandbox.localStorage = sandbox.window.localStorage;
  /* В браузере location и localStorage — глобалы, а не свойства
     window. Без них api.js упал бы на `location.href`. */
  sandbox.location = sandbox.window.location;

  // Файлы читаем как текст и выполняем в общем контексте: так
  // модули видят один и тот же window и localStorage, как в браузере.
  const vm = require("vm");
  const context = vm.createContext(sandbox);
  for (const file of ["api.js", "data.js"]) {
    const code = fs.readFileSync(path.join(WEB, file), "utf8");
    vm.runInContext(code, context, { filename: file });
  }
  return sandbox;
}

/* ---------- утверждения ---------- */
let passed = 0;
const failures = [];

/* Проверка может быть асинхронной: Promise дожидается выполнения.
   Иначе проверка, внутри которой идёт await, «проходила» бы,
   ничего не дождавшись. */
async function check(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failures.push(name);
    console.log(`  ✗ ${name}: ${err.message}`);
  }
}

function assert(cond, message) {
  if (!cond) throw new Error(message || "условие не выполнено");
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(
      `${message || "не совпадает"}: получено ${JSON.stringify(actual)}, ` +
        `ожидалось ${JSON.stringify(expected)}`
    );
  }
}

/* Ответ API в том виде, в котором его отдаёт backend */
function apiEvent(over) {
  return Object.assign(
    {
      camera_id: 1,
      event: "DOWNTIME_FINISHED",
      start_time: "10:00",
      end_time: "10:05",
      duration_seconds: 300,
      estimated_loss: 3750.0,
      created_at: "2026-10-09T10:05:00+00:00",
    },
    over || {}
  );
}

const CAMERAS = { items: [{ id: 1, name: "Камера №1", source_type: "demo", is_demo: true }], count: 1 };
const CONFIG = { cost_per_minute: 750, camera_id: 1, default_source: "demo", downtime_threshold_seconds: 30 };
const STATUS = {
  camera_id: 1,
  line_status: "STOPPED",
  person_present: false,
  downtime_seconds: 134,
  estimated_loss: 1675.0,
};

/* Загрузка с подставленными ответами API */
async function loadLive(overrides) {
  const responses = Object.assign(
    {
      "/api/cameras": CAMERAS,
      "/api/config": CONFIG,
      "/api/status": STATUS,
      "/api/events": { items: [], count: 0, total: 0 },
    },
    overrides || {}
  );

  const fetchImpl = (url) => {
    /* Ответы ищем по пути, а не по полному URL: в нём есть
       хост, параметры запроса и, возможно, другой порт. */
    const route = new URL(String(url)).pathname;
    const body = responses[route];
    if (!body) return Promise.reject(new Error(`нет ответа для ${route}`));
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    });
  };

  const sandbox = loadModules(fetchImpl);
  await sandbox.window.PV.data.load("2026-10-09");
  return sandbox;
}

/* ============================================================ */
console.log("\nweb/js — слой данных Dashboard\n");

(async () => {
  /* ---------- работа с данными сервера ---------- */
  const live = await loadLive({
    "/api/events": {
      items: [
        apiEvent({}),
        apiEvent({
          start_time: "12:00",
          end_time: "12:02",
          duration_seconds: 120,
          estimated_loss: 1500.0,
          created_at: "2026-10-09T12:02:00+00:00",
        }),
      ],
      count: 2,
      total: 2,
    },
  });
  const data = live.window.PV.data;

  await check("данные с сервера помечаются как live", () => {
    assert(data.isLive(), "ожидался режим live");
    assertEqual(data.source(), "live", "источник");
  });

  await check("ущерб берётся готовым, а не считается заново", () => {
    /* Ключевое требование Handoff (раздел 14). Меняем стоимость
       минуты — сумма в отображении обязана остаться прежней. */
    data.setCostPerMinute(5000);
    const events = data.events("2026-10-09", 1);
    assertEqual(events[0].loss, 3750.0, "ущерб первого события");
    assertEqual(events[1].loss, 1500.0, "ущерб второго события");
    data.setCostPerMinute(750);
  });

  await check("длительность берётся готовой", () => {
    const events = data.events("2026-10-09", 1);
    assertEqual(events[0].durationSec, 300, "длительность");
  });

  await check("события группируются по суткам", () => {
    const events = data.events("2026-10-09", 1);
    assertEqual(events.length, 2, "число событий за день");
    assertEqual(events[0].day, "2026-10-09", "дата события");
    assertEqual(events[0].start, "10:00", "время начала");
    assertEqual(events[0].end, "10:05", "время окончания");
  });

  await check("события сортируются по времени начала", () => {
    const events = data.events("2026-10-09", 1);
    assert(events[0].startMin <= events[1].startMin, "порядок нарушен");
  });

  await check("время переводится в минуты от полуночи", () => {
    const events = data.events("2026-10-09", 1);
    assertEqual(events[0].startMin, 10 * 60, "начало");
    assertEqual(events[0].endMin, 10 * 60 + 5, "конец");
  });

  await check("за день без простоев приходит пустой список", () => {
    assertEqual(data.events("2026-10-01", 1).length, 0, "события за пустой день");
  });

  await check("простой за сутки суммируется по завершённым событиям", () => {
    const byLine = data.downtimeByLine("2026-10-09");
    assertEqual(byLine[1], 420, "суммарный простой");
  });

  await check("линии — три, даже если сервер знает об одной камере", () => {
    /* Раньше линии брались из ответа /api/cameras, и при одной
       зарегистрированной камере весь интерфейс сжимался до одного
       столбца: ломались таймлайн, сводка и отчёты. */
    assertEqual(JSON.stringify(data.lines), JSON.stringify([1, 2, 3]), "линии");
  });

  await check("камера есть для каждой линии", () => {
    assertEqual(data.cameras.length, 3, "число камер");
    const byId = {};
    data.cameras.forEach((c) => (byId[c.id] = c));
    assertEqual(byId[1].online, true, "камера 1 зарегистрирована на сервере");
    assertEqual(byId[2].online, false, "камера 2 серверу неизвестна");
    assertEqual(byId[3].online, false, "камера 3 серверу неизвестна");
  });

  await check("текущий статус приходит с сервера", () => {
    const st = data.status();
    assertEqual(st.line_status, "STOPPED", "состояние линии");
    assertEqual(st.person_present, false, "рабочий");
    assertEqual(st.downtime_seconds, 134, "простой");
    assertEqual(st.estimated_loss, 1675.0, "ущерб");
  });

  await check("настройки приходят с сервера", () => {
    assertEqual(data.config().cost_per_minute, 750, "стоимость минуты");
  });

  /* ---------- незавершённый простой ---------- */
  const withOpen = await loadLive({
    "/api/events": {
      items: [
        apiEvent({
          event: "DOWNTIME_STARTED",
          end_time: "",
          duration_seconds: 0,
          estimated_loss: 0.0,
          created_at: "2026-10-09T10:00:00+00:00",
        }),
      ],
      count: 1,
      total: 1,
    },
  });

  await check("незавершённый простой виден на таймлайне до текущего времени", () => {
    const events = withOpen.window.PV.data.events("2026-10-09", 1);
    assertEqual(events.length, 1, "число событий");
    assertEqual(events[0].open, true, "признак открытого простоя");
    /* До текущего времени — иначе карточка была бы нулевой ширины,
       и идущий прямо сейчас простой был бы не виден. */
    assert(events[0].endMin > events[0].startMin, "должен тянуться до «сейчас»");
    assert(/^\d{2}:\d{2}$/.test(events[0].end), "время окончания в формате ЧЧ:ММ");
  });

  await check("незавершённый простой не попадает в итоги за сутки", () => {
    /* У него нет ни длительности, ни ущерба: включать его в суммы
       значило бы завысить простой и ущерб за день. */
    const byLine = withOpen.window.PV.data.downtimeByLine("2026-10-09");
    assertEqual(byLine[1], 0, "простой за день");
  });

  await check("открытый простой отделён от завершённых", () => {
    const d = withOpen.window.PV.data;
    assertEqual(d.finishedForDay("2026-10-09").length, 0, "завершённых событий");
    assertEqual(d.events("2026-10-09", 1).length, 1, "событий на таймлайне");
    assertEqual(d.events("2026-10-09", 1)[0].kind, "open", "тип события");
  });

  await check("длительность открытого простоя считается по прошедшему времени", () => {
    /* Раньше здесь сравнивалось время backend'а (UTC) с местными
       часами: выходили простои по 6–10 часов вместо нескольких
       минут, и итоги в отчёте завышались в разы. */
    const startedAt = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    const box = loadModules(
      (url) => {
        const route = new URL(String(url)).pathname;
        const body =
          route === "/api/status"
            ? STATUS
            : route === "/api/cameras"
            ? CAMERAS
            : route === "/api/events"
            ? {
                items: [
                  apiEvent({
                    event: "DOWNTIME_STARTED",
                    end_time: "",
                    duration_seconds: 0,
                    estimated_loss: 0,
                    created_at: startedAt,
                  }),
                ],
                count: 1,
                total: 1,
              }
            : CONFIG;
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      }
    );
    /* Событие началось пять минут назад, то есть сегодня — за день
       создания оно и не лежит, поэтому берём именно сегодняшний. */
    const today = new Date();
    const iso =
      today.getFullYear() +
      "-" +
      String(today.getMonth() + 1).padStart(2, "0") +
      "-" +
      String(today.getDate()).padStart(2, "0");

    return box.window.PV.data.load(iso).then(() => {
      const event = box.window.PV.data.events(iso, 1)[0];
      assert(event, "событие за сегодня не найдено");
      assertEqual(event.durationSec, 300, "длительность идущего простоя, сек");
      assertEqual(event.loss, 0, "ущерб открытого простоя");
    });
  });

  /* ---------- сервер недоступен ---------- */
  const offline = loadModules(() => Promise.reject(new Error("Failed to fetch")));
const offlineData = offline.window.PV.data;
await offlineData.load("2026-10-09");

  await check("при недоступном сервере показывается демо-режим", () => {
    assertEqual(offlineData.source(), "mock", "источник");
    assertEqual(offlineData.isLive(), false, "режим live");
  });

  await check("причина отказа сохраняется для показа оператору", () => {
    const err = offline.window.PV.api.lastError();
    assert(err !== null && err.length > 0, "причина не записана");
  });

  await check("в демо-режиме простой всё равно рисуется", () => {
    /* Пустой экран хуже демонстрационных данных: оператор должен
       видеть интерфейс, зная, что он помечен как пример. */
    const events = offlineData.events("2026-10-09", 1);
    assert(Array.isArray(events), "события не массив");
  });

  /* ---------- месяц ---------- */
  const monthly = await loadLive({
    "/api/events": {
      items: [
        apiEvent({ created_at: "2026-10-01T10:05:00+00:00" }),
        apiEvent({ created_at: "2026-10-09T10:05:00+00:00" }),
        apiEvent({ created_at: "2026-09-09T10:05:00+00:00" }),
      ],
      count: 3,
      total: 3,
    },
  });

  await check("отчёт за месяц собирает только события этого месяца", () => {
    const events = monthly.window.PV.data.monthEvents(2026, 9); // 9 = октябрь
    assertEqual(events.length, 2, "событий за октябрь");
    assert(events.every((e) => e.day.startsWith("2026-10")), "чужой месяц просочился");
  });

  await check("в отчёте за месяц нет простоя из прошлого месяца", () => {
    const events = monthly.window.PV.data.monthEvents(2026, 8); // сентябрь
    assertEqual(events.length, 1, "событий за сентябрь");
    assertEqual(events[0].day, "2026-09-09", "дата события");
  });

  /* ---------- повторное обновление ---------- */
  const countingBox = (() => {
    const state = { calls: 0 };
    const impl = () => {
      state.calls += 1;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ items: [], count: 0, total: 0 }),
      });
    };
    return { impl, state };
  })();

  const repeated = loadModules(countingBox.impl);
  await repeated.window.PV.data.load("2026-10-09");
  const firstCalls = countingBox.state.calls;

  await repeated.window.PV.data.load("2026-10-09");
  await check("повтор того же дня берётся из кэша, а не из сети", () => {
    assertEqual(countingBox.state.calls, firstCalls, "число запросов");
  });

  await repeated.window.PV.data.reload("2026-10-09");
  await check("reload обращается к серверу даже при загруженном дне", () => {
    /* Иначе «текущее состояние» замирает навсегда: день уже
       в кэше, запрос не уходит, цифры не меняются. */
    assert(
      countingBox.state.calls > firstCalls,
      "reload обязан обновить данные с сервера"
    );
  });

  /* ---------- обрыв связи при уже загруженных данных ---------- */
  const flakyBox = (() => {
    const state = { online: true };
    const impl = (url) => {
      if (!state.online) return Promise.reject(new Error("сеть недоступна"));
      const route = new URL(String(url)).pathname;
      const body =
        route === "/api/status"
          ? STATUS
          : route === "/api/cameras"
          ? CAMERAS
          : route === "/api/events"
          ? { items: [apiEvent({})], count: 1, total: 1 }
          : CONFIG;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    return { impl, state };
  })();

  const flaky = loadModules(flakyBox.impl);
  await flaky.window.PV.data.load("2026-10-09");

  await check("до обрыва связь считается установленной", () => {
    assertEqual(flaky.window.PV.api.ready(), true, "связь");
    assertEqual(flaky.window.PV.data.isLive(), true, "режим");
  });

  flakyBox.state.online = false;
  await flaky.window.PV.data.reload("2026-10-09");

  await check("обрыв связи обнаруживается и сохраняется причина", () => {
    assertEqual(flaky.window.PV.api.ready(), false, "связь должна оборваться");
    assert(flaky.window.PV.api.lastError(), "причина обрыва не сохранена");
  });

  await check("при обрыве прежние данные не стираются", () => {
    /* Пустой экран хуже последних показаний: оператор должен
       видеть данные с пометкой обрыва, а не ничто. */
    assertEqual(flaky.window.PV.data.isLive(), true, "данные должны остаться");
    assertEqual(flaky.window.PV.data.events("2026-10-09", 1).length, 1, "события");
  });

  /* ---------- раздел «Сотрудники» ---------- */
  await check("раздел сотрудников помечен как демонстрационный", () => {
    /* Сотрудников нет в контракте и backend их не ведёт. Флаг
       нужен, чтобы интерфейс показал предупреждение. */
    assertEqual(data.employeesAreMock, true, "признак демо-данных");
    assert(Array.isArray(data.employees) && data.employees.length > 0, "список пуст");
  });

  /* ---------- итог ---------- */
  console.log(`\n${passed}/${passed + failures.length} тестов пройдено`);
  process.exit(failures.length ? 1 : 0);
})();