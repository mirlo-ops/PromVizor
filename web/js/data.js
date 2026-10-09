/* ============================================================
   ПромВизор — данные и мок-слой
   ============================================================ */
(function (global) {
  "use strict";

  /* ---------- утилиты ---------- */
  const pad = (n) => String(n).padStart(2, "0");
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const toMin = (hhmm) => {
    const [h, m] = hhmm.split(":").map(Number);
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

  /* ---------- детерминированный ГПСЧ ---------- */
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
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ---------- сотрудники ---------- */
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
  // Должности в мужском роде; женские формы — в POSITIONS_F
  const POSITIONS_M = [
    "Оператор линии","Оператор линии","Наладчик","Мастер смены",
    "Контролёр ОТК","Упаковщик","Водитель погрузчика","Инженер-наладчик",
  ];
  // Формы, которые меняются по роду; остальные («Оператор линии»,
  // «Мастер смены», «Контролёр ОТК» и т.д.) одинаковы для обоих полов
  const POSITIONS_F_OVERRIDE = {
    "Наладчик": "Наладчица",
    "Упаковщик": "Упаковщица",
    "Инженер-наладчик": "Инженер-наладчица",
  };
  // Фамилии по полу: женская форма образуется через -ова/-ева
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
    "Александровна","Ивановна","Николаевна","Дмитриевна","Алексеевна",
    "Павловна","Андреевна","Викторовна",
  ];

  function makeEmployees() {
    const rnd = mulberry(hashStr("promvizor-employees"));
    const list = [];
    for (let i = 0; i < 120; i++) {
      const male = rnd() > 0.42;
      // Имя, фамилия, отчество и пол — единый набор для каждого человека
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
      const absentMin = status === "absent"
        ? Math.floor(20 + rnd() * 190)
        : 0;

      list.push({
        id: i + 1,
        name: `${last} ${first} ${patron}`,
        short: `${last} ${first[0]}.`,
        male,
        position,
        line: 1 + Math.floor(rnd() * 3),
        shift: shiftStart < 12 ? "Смена 1" : "Смена 2",
        shiftStart,
        status,
        absentMin,
        lost: status === "absent" ? absentMin * 12.5 : 0,
        attendance: clamp(Math.round(88 + rnd() * 12), 0, 100),
        hired: 2018 + Math.floor(rnd() * 7),
        phone: `+7 9${Math.floor(rnd() * 10)} ${100 + Math.floor(rnd() * 800)}-${10 + Math.floor(rnd() * 89)}-${10 + Math.floor(rnd() * 89)}`,
      });
    }
    return list;
  }

  /* ---------- события ---------- */
  // event.kind: "absent" (простой/отсутствие) | "present" (на месте)
  function makeEvents(dayISO, line) {
    const rnd = mulberry(hashStr(dayISO + ":line" + line));
    const out = [];
    // Рабочий день 06:00–22:00, от 0 до 3 простоев
    const count = Math.floor(rnd() * 4);
    let cursor = 6 * 60 + Math.floor(rnd() * 60);
    const staff = API.employees;

    for (let i = 0; i < count; i++) {
      const dur = 25 + Math.floor(rnd() * 160);
      const start = cursor;
      const end = start + dur;
      if (end > 22 * 60) break;

      const emp = staff[Math.floor(rnd() * staff.length)];
      out.push({
        id: `${dayISO}-L${line}-${i}`,
        day: dayISO,
        line,
        kind: "absent",
        camera_id: line,
        employeeId: emp.id,
        employee: emp.short,
        employeeFull: emp.name,
        startMin: start,
        endMin: end,
        start: toHM(start),
        end: toHM(end),
        durationSec: dur * 60,
        loss: Math.round(dur * 12.5 * 100) / 100,
      });
      cursor = end + 30 + Math.floor(rnd() * 150);
    }

    // Пара «на месте» блоков, чтобы таймлайн не был пустым
    if (rnd() > 0.35) {
      const s = 6 * 60 + Math.floor(rnd() * 120);
      out.push({
        id: `${dayISO}-L${line}-ok`,
        day: dayISO,
        line,
        kind: "present",
        camera_id: line,
        employeeId: null,
        employee: "Персонал на месте",
        employeeFull: "Персонал на месте",
        startMin: s,
        endMin: s + 60 + Math.floor(rnd() * 200),
        start: toHM(s),
        end: "",
        durationSec: 0,
        loss: 0,
      });
    }

    out.sort((a, b) => a.startMin - b.startMin);
    return out;
  }

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

  /* ---------- публичный мок-API ---------- */
  const API = {
    LineStatus: { WORKING: "WORKING", STOPPED: "STOPPED", UNKNOWN: "UNKNOWN" },
    EventType: { DOWNTIME_STARTED: "DOWNTIME_STARTED", DOWNTIME_FINISHED: "DOWNTIME_FINISHED" },

    employees: makeEmployees(),
    lines: [1, 2, 3],
    cameras: [
      { id: 1, name: "КАМ 1", online: true },
      { id: 2, name: "КАМ 2", online: false },
      { id: 3, name: "КАМ 3", online: false },
    ],

    events(day, line) {
      return makeEvents(typeof day === "string" ? day : dayISO(day), line);
    },

    eventsAllLines(day) {
      const iso = typeof day === "string" ? day : dayISO(day);
      return this.lines.flatMap((l) => makeEvents(iso, l));
    },

    downtimeByLine(day) {
      const iso = typeof day === "string" ? day : dayISO(day);
      const out = {};
      this.lines.forEach((l) => {
        const sec = makeEvents(iso, l)
          .filter((e) => e.kind === "absent")
          .reduce((s, e) => s + e.durationSec, 0);
        out[l] = sec;
      });
      return out;
    },

    statusOf(day, line) {
      const sec = this.downtimeByLine(day)[line] || 0;
      return sec > 0 ? this.LineStatus.STOPPED : this.LineStatus.WORKING;
    },

    monthEvents(year, month) {
      const rnd = mulberry(hashStr(year + "-" + month));
      const days = new Date(year, month + 1, 0).getDate();
      const out = [];
      for (let d = 1; d <= days; d++) {
        this.lines.forEach((l) => {
          makeEvents(dayISO(new Date(year, month, d)), l).forEach((e) => out.push(e));
        });
      }
      // гарантируем непустой отчёт
      if (out.length === 0) out.push(...makeEvents(`${year}-${pad(month + 1)}-13`, 1));
      return out;
    },

    util: { pad, clamp, toMin, toHM, fmtHM, fmtMoney, dayISO, fmtDateLong,
            fmtDateFull, addDays, MONTHS_GEN, MONTHS_NOM, WEEKDAYS },
  };

  global.PV = { data: API, util: API.util };
})(window);