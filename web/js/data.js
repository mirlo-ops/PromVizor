/* ============================================================
   ПромВизор — слой данных
   ------------------------------------------------------------
   Задача файла: отдать интерфейсу данные в том же виде, в каком он
   их ожидает, но из реального backend'а (Этап 6), а не из выдумки.

   Порядок работы:
     1. load(day) асинхронно забирает данные у API и складывает их
        в кэш;
     2. синхронные методы events(), downtimeByLine(), statusOf()
        читают кэш.

   Так сделано потому, что разделы интерфейса (sections.js)
   перерисовываются синхронно. Переписывать их на асинхронные ради
   одного слоя данных — лишний риск: правки в отрисовке не нужны,
   меняется только источник.

   Правило, которое этот файл обязан соблюдать (Handoff, раздел 14):
   ФРОНТЕНД НЕ ПЕРЕСЧИТЫВАЕТ УЩЕРБ. Значение estimated_loss
   приходит с backend'а готовым и используется как есть.

   Про mock. Если backend недоступен, интерфейс показывает данные
   mock'а — но честно помечает их как демонстрационные. Молча
   подсовывать выдумку нельзя: на Dashboard смотрел бы человек,
   решивший бы, что линия стоит.
   ============================================================ */
(function (global) {
  "use strict";

  const api = global.PV.api;

  /* Линии — конфигурация цеха, а не ответ сервера.
     Backend может знать пока об одной камере, но интерфейс
     рассчитан на три линии: сжимать наряд до одного столбца
     нельзя, иначе ломаются таймлайн, сводка и отчёты. */
  const DEFAULT_LINES = [1, 2, 3];

  /* ---------- утилиты ---------- */
  const pad = (n) => String(n).padStart(2, "0");
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const toMin = (hhmm) => {
    const [h, m] = String(hhmm).split(":").map(Number);
    return h * 60 + m;
  };
  const toHM = (min) => `${pad(Math.floor(min / 60) % 24)}:${pad(min % 60)}`;
  const fmtHM = (sec) => {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return h > 0 ? `${h}:${pad(m)}` : `${m} мин`;
  };
  const fmtMoney = (v) =>
    Math.round(v).toLocaleString("ru-RU") + " ₽";

  const MONTHS_GEN = [
    "января","февраля","марта","апреля","мая","июня",
    "июля","августа","сентября","октября","ноября","декабря",
  ];
  const MONTHS_NOM = [
    "Январь","Февраль","Март","Апрель","Май","Июнь",
    "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь",
  ];
  const WEEKDAYS = [
    "воскресенье","понедельник","вторник","среда",
    "четверг","пятница","суббота",
  ];

  /* ---------- даты ---------- */
  function dayISO(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function fmtDateLong(d) {
    return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
  }
  function fmtDateFull(d) {
    return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]} ${d.getFullYear()}`;
  }
  function addDays(d, n) {
    const x = new Date(d);
    x.setDate(x.getDate() + n);
    return x;
  }
  function parseISO(iso) {
    const [y, m, d] = String(iso).split("-").map(Number);
    return new Date(y, (m || 1) - 1, d || 1);
  }

  /* Сутки по дате записи события (UTC). Метка приходит с backend'а
     в UTC, поэтому разбираем её без поправки на местное время:
     иначе событие «23:30» уехало бы на следующие сутки у того,
     кто смотрит из другого часового пояса. */
  function dayOfStamp(stamp) {
    return String(stamp || "").slice(0, 10);
  }

  /* ============================================================
     КЭШ ДАННЫХ
     ============================================================ */
  const cache = {
    /* status — последний SystemStatus */
    status: null,
    /* cameras — список камер */
    cameras: [],
    /* config — настройки backend'а */
    config: null,
    /* events — Map: "дата" → массив событий в форме интерфейса */
    events: new Map(),
    /* stats — Map: "дата" → Statistics за сутки */
    stats: new Map(),
    /* lines — линии (равны id камер) */
    lines: [1],
    /* source: "live" | "mock" */
    source: "mock",
    loaded: false,
    /* Диапазон загруженного: чтобы понимать, попадает ли
       запрошенный день в кэш или его надо догрузить. */
    from: null,
    to: null,
  };

  /* ---------- преобразование ответа API в форму интерфейса ---------- */
  /* Событие с backend'а приходит «сырым»: даты записи в нём нет,
     длительность и ущерб названы своими именами. Здесь оно
     превращается в то, чем рисует интерфейс. Ничего не
     вычисляется — только переименование полей. */
  function toViewEvent(raw) {
    const startMin = toMin(raw.start_time);
    const isOpen = raw.event === "DOWNTIME_STARTED";

    /* Незавершённый простой идёт «до сих пор»: показываем его до
       текущего времени. Иначе карточка получилась бы нулевой ширины
       и простой, который прямо сейчас идёт, был бы не виден.

       Длительность берётся из реального прошедшего времени между
       меткой записи и «сейчас». Сравнивать с местными часами нельзя:
       start_time приходит в UTC, а на часах оператора время другое —
       наивный подсчёт давал бы простой в несколько раз длиннее
       настоящего и завышал итоги в отчёте. */
    const elapsed = isOpen
      ? Math.max(0, Math.round((Date.now() - Date.parse(raw.created_at)) / 1000))
      : 0;
    const endMin = isOpen ? startMin + Math.round(elapsed / 60) : toMin(raw.end_time);

    return {
      id: raw.id != null ? raw.id : raw.start_time + raw.event,
      day: dayOfStamp(raw.created_at),
      line: raw.camera_id,
      camera_id: raw.camera_id,
      /* Открытый простой отмечен отдельно: на таймлайне он
         рисуется, но в итоги за сутки и месяц не входит — так же,
         как его считает backend, где открытое событие ущерба не
         имеет. */
      kind: isOpen ? "open" : "absent",
      open: isOpen,
      event: raw.event,
      employee: "Персонал не на месте",
      employeeFull: "Персонал не на месте",
      startMin: startMin,
      endMin: endMin,
      start: raw.start_time,
      /* У открытого простоя конца ещё нет — вместо него показываем
         время, до которого он длится, чтобы не выглядело, что он
         закончился в 00:00. */
      end: isOpen ? toHM(endMin) : raw.end_time || "",
      /* Готовые значения backend'а. Никакого пересчёта.
         У открытого простоя ущерб нулевой: он считается при
         завершении, и backend отдаёт 0 — так же, как здесь. */
      durationSec: isOpen ? elapsed : raw.duration_seconds || 0,
      loss: raw.estimated_loss || 0,
    };
  }

  /* ---------- загрузка ---------- */
  /* Загружаем окно вокруг выбранного дня: этого хватает и для
     таймлайна за сутки, и для отчёта за месяц. */
  const WINDOW_DAYS = 40;

  function inRange(iso) {
    return cache.from && cache.to && iso >= cache.from && iso <= cache.to;
  }

  async function load(day, force) {
    const target = typeof day === "string" ? parseISO(day) : day || new Date();
    const iso = dayISO(target);

    /* День уже в загруженном окне — на сервер не идём.
       Исключение — force: его ставит автообновление. Без этой
       развилки «текущее состояние» замерло бы на первых же
       показаниях и больше никогда не менялось бы. */
    if (!force && cache.loaded && inRange(iso)) return cache.source;

    const from = dayISO(addDays(target, -WINDOW_DAYS));
    const to = dayISO(addDays(target, WINDOW_DAYS));

    const [cameras, config, events] = await Promise.all([
      api.cameras({ fresh: force }),
      api.config({ fresh: force }),
      api.events({ since: from, until: to, limit: 1000, fresh: force }),
    ]);

    /* Клиент API возвращает null вместо исключения, поэтому
       «ответ пришёл» и «ответ есть» — не одно и то же. Проверяем
       явно: иначе один упавший запрос оставил бы интерфейс в
       режиме live с пустым экраном и подписью «данные с сервера»,
       то есть молча выдавал бы демо за показания. */
    if (!cameras || !events) {
      const missing = [];
      if (!cameras) missing.push("камеры");
      if (!config) missing.push("настройки");
      if (!events) missing.push("события");
      throw new Error(
        "Backend не вернул: " + missing.join(", ") +
          (api.lastError() ? " (" + api.lastError() + ")" : "")
      );
    }

    /* Список камер — источник правды о том, какие камеры есть.
       Но НЕ о числе линий: линии — это конфигурация интерфейса,
       их наряд всегда три. Если брать линии из ответа сервера, то
       при одной зарегистрированной камере весь интерфейс
       сжимался до одного столбца, и половина разделов переставала
       работать. */
    const camerasList = (cameras && cameras.items) || [];
    const known = new Map();
    camerasList.forEach((c) => {
      known.set(c.id, {
        id: c.id,
        name: c.name || `Камера №${c.id}`,
        online: true,
        source_type: c.source_type,
        is_demo: c.is_demo,
      });
    });

    /* Три линии по настройке плюс все линии, о которых сообщил
       сервер: если камер больше трёх, они тоже должны появиться. */
    const lineIds = new Set(DEFAULT_LINES);
    known.forEach((_, id) => lineIds.add(id));
    cache.lines = Array.from(lineIds).sort((a, b) => a - b);

    /* Камера показывается для каждой линии. Если сервер о ней не
       знает, она честно помечается офлайн: иначе при переключении
       на «Линию 2» заголовок продолжал бы называть «Камера №1»,
       то есть показывал бы чужую камеру. */
    cache.cameras = cache.lines.map(
      (id) =>
        known.get(id) || {
          id: id,
          name: `Камера №${id}`,
          online: false,
          source_type: "",
          is_demo: false,
        }
    );
    cache.config = config;

    /* Раскладываем события по суткам */
    cache.events = new Map();
    const items = (events && events.items) || [];
    items.forEach((raw) => {
      const view = toViewEvent(raw);
      if (!cache.events.has(view.day)) cache.events.set(view.day, []);
      cache.events.get(view.day).push(view);
    });
    cache.events.forEach((list) => list.sort((a, b) => a.startMin - b.startMin));

    cache.from = from;
    cache.to = to;
    cache.loaded = true;
    cache.source = "live";

    /* Текущий статус забираем отдельно: он меняется постоянно,
       и кэш минуты тут не нужен — он и так сбрасывается. */
    const status = await api.status({ fresh: force });
    if (status) cache.status = status;

    return cache.source;
  }

  /* Однократная попытка загрузки. Ошибка не бросается наружу:
     интерфейс должен показать «нет связи», а не пустой экран. */
  async function tryLoad(day, force) {
    try {
      return await load(day, force);
    } catch (err) {
      cache.source = "mock";
      cache.loaded = false;
      return "mock";
    }
  }

  /* ---------- выбор источника ---------- */
  /* Функции ниже отвечают на вопрос «что показать»: реальные данные
     или демонстрационные. Решение принимается один раз и явно. */
  function isLive() {
    return cache.source === "live";
  }

  function eventsFor(iso, line) {
    if (!isLive()) return mockEvents(iso, line);
    const list = cache.events.get(iso) || [];
    if (line === undefined || line === null) return list.slice();
    return list.filter((e) => e.line === line);
  }

  function downtimeFor(iso) {
    if (!isLive()) return mockDowntimeByLine(iso);
    const out = {};
    cache.lines.forEach((l) => {
      out[l] = (cache.events.get(iso) || [])
        .filter((e) => e.line === l && !e.open)
        .reduce((sum, e) => sum + e.durationSec, 0);
    });
    return out;
  }

  /* Завершённые простои за сутки — то, что backend считает итогом.
     Открытый простой (идущий прямо сейчас) сюда не входит: он не
     завершён, длительность и ущерб у него ещё не посчитаны. */
  function finishedFor(iso) {
    if (!isLive()) {
      return [1, 2, 3].flatMap((l) => mockEvents(iso, l)).filter((e) => !e.open);
    }
    return (cache.events.get(iso) || []).filter((e) => !e.open);
  }

  /* ============================================================
     MOCK-СЛОЙ (демонстрационный режим)
     ------------------------------------------------------------
     Используется только когда backend недоступен. Весь этот
     блок помечен как демонстрационный, чтобы его числа никто
     не принял за показания завода.
     ============================================================ */

  function hashStr(s) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  }
  function mulberry(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  let COST_PER_MINUTE = 750;

  function setCostPerMinute(value) {
    const v = Number(value);
    if (Number.isFinite(v) && v > 0) COST_PER_MINUTE = v;
  }

  /* Ущерб в mock'е — ЗАГЛУШКА, повторяющая calculate_loss()
     из app/core/types.py. В рабочем режиме эта функция не
     используется: реальные суммы приходят готовыми с backend'а. */
  function backendLoss(downtimeSeconds) {
    const minutes = Math.max(0, downtimeSeconds) / 60;
    return Math.round(minutes * COST_PER_MINUTE * 100) / 100;
  }

  function mockEvents(day, line) {
    const rnd = mulberry(hashStr(day + ":line" + line));
    const out = [];
    const count = Math.floor(rnd() * 4);
    let cursor = 6 * 60 + Math.floor(rnd() * 60);

    for (let i = 0; i < count; i++) {
      const dur = 25 + Math.floor(rnd() * 160);
      const start = cursor;
      const end = start + dur;
      if (end > 22 * 60) break;
      const durationSec = dur * 60;
      out.push({
        id: day + "-L" + line + "-" + i,
        day: day,
        line: line,
        camera_id: line,
        kind: "absent",
        open: false,
        employee: "Персонал не на месте",
        employeeFull: "Персонал не на месте",
        startMin: start,
        endMin: end,
        start: toHM(start),
        end: toHM(end),
        durationSec: durationSec,
        loss: backendLoss(durationSec),
      });
      cursor = end + 30 + Math.floor(rnd() * 150);
    }
    return out;
  }

  /* ---------- сотрудники (только демо-режим) ---------- */
  /* Сотрудников в контракте нет и backend их не ведёт, поэтому
     раздел «Сотрудники» остаётся демонстрационным. Он помечен
     в интерфейсе явно (DATA.employeesAreMock), чтобы его числа
     не приняли за данные завода. */
  const STATUSES = ["work", "work", "work", "work", "absent", "vacation"];
  const FIRST_M = [
    "Александр","Дмитрий","Максим","Сергей","Алексей","Артём","Никита","Иван",
    "Михаил","Андрей","Павел","Роман","Евгений","Денис","Илья","Кирилл",
  ];
  const FIRST_F = [
    "Анна","Бэлла","Виктория","Галина","Дарья","Елена","Жанна","Зинаида",
    "Ирина","Ксения","Людмила","Маргарита","Нина","Ольга","Полина","Регина",
    "Светлана","Татьяна","Ульяна","Фаина","Юлия",
  ];
  /* Должности в мужском роде; женские формы — в POSITIONS_F */
  const POSITIONS_M = [
    "Оператор линии","Оператор линии","Наладчик","Мастер смены",
    "Контролёр ОТК","Упаковщик","Водитель погрузчика","Инженер-наладчик",
  ];
  /* Формы, которые меняются по роду; остальные одинаковы для обоих */
  const POSITIONS_F_OVERRIDE = {
    "Наладчик": "Наладчица",
    "Упаковщик": "Упаковщица",
    "Инженер-наладчик": "Инженер-наладчица",
  };
  const LAST_M = [
    "Петров","Иванов","Смирнов","Кузнецов","Соколов","Попов","Лебедев",
    "Козлов","Новиков","Морозов","Волков","Зайцев","Павлов","Семёнов",
  ];
  const LAST_F = [
    "Петрова","Иванова","Смирнова","Кузнецова","Соколова","Попова","Лебедева",
    "Козлова","Новикова","Морозова","Волкова","Зайцева","Павлова","Семёнова",
  ];
  const PATRON_M = [
    "Сергеевич","Иванович","Николаевич","Дмитриевич","Алексеевич",
    "Павлович","Андреевич","Викторович",
  ];
  const PATRON_F = [
    "Александровна","Иванова","Николаевна","Дмитриевна","Алексеевна",
    "Павловна","Андреевна","Викторовна",
  ];

  function makeEmployees() {
    const rnd = mulberry(hashStr("promvizor-employees"));
    const list = [];
    for (let i = 0; i < 120; i++) {
      const male = rnd() > 0.42;
      const first = male
        ? FIRST_M[Math.floor(rnd() * FIRST_M.length)]
        : FIRST_F[Math.floor(rnd() * FIRST_F.length)];
      const last = male
        ? LAST_M[Math.floor(rnd() * LAST_M.length)]
        : LAST_F[Math.floor(rnd() * LAST_F.length)];
      const patron = (male ? PATRON_M : PATRON_F)[Math.floor(rnd() * 8)];
      const posM = POSITIONS_M[Math.floor(rnd() * POSITIONS_M.length)];
      const position = male ? posM : (POSITIONS_F_OVERRIDE[posM] || posM);

      const status = STATUSES[Math.floor(rnd() * STATUSES.length)];
      const shiftStart = [6, 6, 7, 14, 14, 22][Math.floor(rnd() * 6)];
      const absentMin = status === "absent" ? Math.floor(20 + rnd() * 190) : 0;

      list.push({
        id: i + 1,
        name: `${last} ${first} ${patron}`,
        short: `${last} ${first[0]}.`,
        male: male,
        position: position,
        line: 1 + Math.floor(rnd() * 3),
        shift: shiftStart < 12 ? "Смена 1" : "Смена 2",
        shiftStart: shiftStart,
        status: status,
        absentMin: absentMin,
        lost: status === "absent" ? backendLoss(absentMin * 60) : 0,
        attendance: clamp(Math.round(88 + rnd() * 12), 0, 100),
        hired: 2018 + Math.floor(rnd() * 7),
        phone:
          `+7 9${Math.floor(rnd() * 10)} ${100 + Math.floor(rnd() * 800)}-` +
          `${10 + Math.floor(rnd() * 89)}-${10 + Math.floor(rnd() * 89)}`,
      });
    }
    return list;
  }

  function mockDowntimeByLine(iso) {
    const out = {};
    [1, 2, 3].forEach((l) => {
      out[l] = mockEvents(iso, l).reduce((s, e) => s + e.durationSec, 0);
    });
    return out;
  }

  function mockMonthEvents(y, m) {
    const rnd = mulberry(hashStr(y + "-" + m));
    const days = new Date(y, m + 1, 0).getDate();
    const out = [];
    for (let d = 1; d <= days; d++) {
      [1, 2, 3].forEach((l) => {
        mockEvents(dayISO(new Date(y, m, d)), l).forEach((e) => out.push(e));
      });
    }
    if (out.length === 0) out.push(...mockEvents(y + "-" + pad(m + 1) + "-13", 1));
    return out;
  }

  /* ============================================================
     ПУБЛИЧНЫЙ ИНТЕРФЕЙС ДАННЫХ
     Форма совпадает с тем, что ждёт sections.js
     ============================================================ */
  const DATA = {
    LineStatus: api.LineStatus,
    EventType: api.EventType,

    setCostPerMinute,

    load: tryLoad,

    /* Полное обновление с сервера, минуя кэш. Так работает
       автообновление и кнопка «проверить связь». */
    reload(day) {
      return tryLoad(day, true);
    },

    isLive,
    source: () => cache.source,
    status: () => cache.status,
    config: () => cache.config,

    get lines() {
      return cache.lines;
    },

    get cameras() {
      return isLive() ? cache.cameras : mockCameras();
    },

    /* Сотрудников в контракте нет и backend их не ведёт, поэтому
       раздел «Сотрудники» остаётся демонстрационным. Флаг
       employeesAreMock интерфейс показывает явно, чтобы эти
       числа не приняли за данные завода. */
    employees: makeEmployees(),
    employeesAreMock: true,

    events(day, line) {
      const iso = typeof day === "string" ? day : dayISO(day);
      return eventsFor(iso, line);
    },

    eventsAllLines(day) {
      const iso = typeof day === "string" ? day : dayISO(day);
      if (!isLive()) {
        return [1, 2, 3].flatMap((l) => mockEvents(iso, l));
      }
      return (cache.events.get(iso) || []).slice();
    },

    /* Завершённые простои за сутки — для сводок и отчётов.
       Открытый простой сюда не входит, чтобы итоги совпадали
       с тем, что backend считает по /api/statistics. */
    finishedForDay(day) {
      const iso = typeof day === "string" ? day : dayISO(day);
      return finishedFor(iso);
    },

    /* Завершённые простои за месяц — там же логика. */
    finishedForMonth(year, month) {
      if (!isLive()) {
        return mockMonthEvents(year, month).filter((e) => !e.open);
      }
      return DATA.monthEvents(year, month).filter((e) => !e.open);
    },

    downtimeByLine(day) {
      const iso = typeof day === "string" ? day : dayISO(day);
      return downtimeFor(iso);
    },

    statusOf(day, line) {
      const iso = typeof day === "string" ? day : dayISO(day);
      const sec = downtimeFor(iso)[line] || 0;
      if (sec > 0) return DATA.LineStatus.STOPPED;
      if (!isLive()) return DATA.LineStatus.WORKING;
      /* В реальном режиме для сегодняшнего дня отвечает
         /api/status: он учитывает простой, который идёт сейчас
         и в базу ещё не записан как завершённый. */
      const today = dayISO(new Date());
      const status = cache.status;
      if (iso === today && status && status.camera_id === line) {
        return status.line_status;
      }
      return DATA.LineStatus.WORKING;
    },

    monthEvents(year, month) {
      if (!isLive()) return mockMonthEvents(year, month);
      const out = [];
      cache.events.forEach((list, day) => {
        const d = parseISO(day);
        if (d.getFullYear() === year && d.getMonth() === month) out.push(...list);
      });
      return out.sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : a.startMin - b.startMin));
    },

    util: {
      pad, clamp, toMin, toHM, fmtHM, fmtMoney, dayISO,
      fmtDateLong, fmtDateFull, addDays, parseISO,
      MONTHS_GEN, MONTHS_NOM, WEEKDAYS,
    },
  };

  /* Камеры для демо-режима */
  function mockCameras() {
    return [
      { id: 1, name: "Камера №1", online: true, source_type: "demo", is_demo: true },
      { id: 2, name: "Камера №2", online: false, source_type: "demo", is_demo: true },
      { id: 3, name: "Камера №3", online: false, source_type: "demo", is_demo: true },
    ];
  }

  global.PV = global.PV || {};
  global.PV.data = DATA;
  global.PV.util = DATA.util;
})(window);