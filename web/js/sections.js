/* ============================================================
   ПромВизор — разделы интерфейса
   ============================================================ */
(function (global) {
  "use strict";

  const { data, util: U } = global.PV;
  const { icon, esc, initials, toast, on, statusTag } = global.PV.c;

  /* ============================================================
     Внутреннее состояние
     ============================================================ */
  const state = {
    // Вкладка мониторинга (Линии / Камеры / Сводная).
    // Держим её отдельно от раздела, иначе переход в «Отчёты»
    // затирал вкладку и возврат в «Мониторинг» показывал чужой раздел.
    tab: "cameras",
    section: "monitoring",
    day: new Date(),                  // актуальная дата при запуске
    camLine: 1,
    playing: true,
    employeesPage: 1,
    employeesQuery: "",
    employeesLine: "all",
    reportPeriod: "month",
    settings: loadSettings(),
    /* Режим камеры и выбор веб-камеры приходят с сервера, а не из
       localStorage: переключать режим нужно на работающей системе,
       без перезапуска backend'а. */
    video: {
      mode: "synthetic",
      modeLabel: "",
      webcamIndex: 0,
      webcams: [],
      applied: false,
      /* Демо-сценарии: список роликов с сервера. Порядок задан
         здесь, а не берётся из ответа: подписи должны идти в
         задуманном порядке (норма → остановка → ушёл → простой),
         а сервер перечисляет их как удобно ему. */
      scenarios: [],
      scenarioLabels: {},
      scenarioOrder: ["normal", "stopped", "person_left", "stopped_no_person"],
      demoScenario: "",
    },
  };

  /* Адрес потока видео. Берётся у API-клиента, а не пишется
     относительным путём: см. api.baseUrl().

     Здесь отметка о входе попадает В АДРЕС, потому что поток
     открывается тегом <img> — а на такой запрос браузер не даёт
     вешать заголовки. Адрес не попадает в историю браузера, но
     останется в журнале сервера, поэтому на сервере такие адреса
     вырезаются из access-log. */
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

  function loadSettings() {
    const def = {
      cost_per_minute: 750,
      downtime_threshold_seconds: 30,
      default_source: "demo",
      rtsp_url: "",
      notify_telegram: true,
      notify_email: false,
      auto_refresh: true,
      show_loss: true,
      retention_days: 90,
      threshold_minutes: 5,
      theme: "dark",
    };
    try {
      const saved = JSON.parse(localStorage.getItem("pv_settings") || "{}");
      // Сохранённое значение темы не влияет на оформление: выбор темы
      // отключён, интерфейс всегда тёмный. Иначе старая «light» из
      // localStorage возвращала бы прежнее оформление, хотя
      // переключателя в настройках уже нет.
      // Прочие сохранённые настройки оставляем без изменений.
      saved.theme = "dark";
      return Object.assign(def, saved);
    } catch (e) {
      return def;
    }
  }

  /* Стоимость минуты — серверное значение. Передаём её моку,
     чтобы интерфейс показывал столько же, сколько вернул бы backend. */
  function syncCostPerMinute() {
    data.setCostPerMinute(state.settings.cost_per_minute);
  }

  function saveSettings() {
    try {
      syncCostPerMinute();
      localStorage.setItem("pv_settings", JSON.stringify(state.settings));
      return true;
    } catch (e) {
      return false;
    }
  }

  /* Применяет тему к документу.
     Атрибут ставится на <html>, чтобы тема действовала до отрисовки
     и не мигала при загрузке страницы.

     Сейчас интерфейс всегда тёмный и выбора темы нет, поэтому
     applyTheme() зовут с фиксированным значением. Функция оставлена
     как есть: обе палитры в styles.css живы, и вернуть переключатель
     — починка одной строки. */
  function applyTheme(theme) {
    const t = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
    state.settings.theme = t;
    const meta = document.querySelector('meta[name="theme-color"]');
    // Цвет панели браузера: тёмно-зелёный из презентации, а не
    // серо-синий #0f1519, оставшийся от прошлой палитры.
    if (meta) meta.setAttribute("content", t === "dark" ? "#081c15" : "#f7f9f9");
  }

  /* ============================================================
     SVG-заглушка «видео» (складской цех, различается по линиям)
     ============================================================ */
  function factorySVG(line) {
    const tint = ["#4d6472", "#54604f", "#5a4f63"][line - 1] || "#4d6472";
    const boxes = [];
    const rnd = (i) => ((Math.sin(line * 17 + i * 3.7) + 1) / 2);
    for (let i = 0; i < 7; i++) {
      const x = 90 + i * 150 + rnd(i) * 22;
      const y = 330 + rnd(i + 9) * 26;
      const s = 26 + rnd(i + 3) * 16;
      boxes.push(
        `<rect x="${x.toFixed(0)}" y="${y.toFixed(0)}" width="${s.toFixed(0)}" height="${(s * 0.72).toFixed(0)}" rx="2" fill="#b58a52" opacity=".85"/>` +
        `<rect x="${x.toFixed(0)}" y="${(y + s * 0.30).toFixed(0)}" width="${s.toFixed(0)}" height="2" fill="#8d6a3c" opacity=".7"/>`
      );
    }
    const rollers = [];
    for (let i = 0; i < 26; i++) {
      rollers.push(`<rect x="${150 + i * 26}" y="${372 + rnd(i + 21) * 5}" width="7" height="16" rx="3" fill="#9aa8b1"/>`);
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 810" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id="ceil${line}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#cfd8dd"/><stop offset="100%" stop-color="#eef2f4"/>
        </linearGradient>
        <linearGradient id="flr${line}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#9aa5ad"/><stop offset="100%" stop-color="#7d8891"/>
        </linearGradient>
      </defs>
      <rect width="1440" height="470" fill="url(#ceil${line})"/>
      <rect y="470" width="1440" height="340" fill="url(#flr${line})"/>
      <g opacity=".5" stroke="#8b969e" stroke-width="2">
        ${[0, 1, 2, 3, 4, 5].map((i) => `<path d="M${60 + i * 260} 0 V470"/>`).join("")}
        <path d="M0 470 H1440"/>
      </g>
      <g fill="#eef3f5" opacity=".9">
        ${[0, 1, 2, 3].map((i) => `<rect x="${180 + i * 330}" y="40" width="150" height="12" rx="6"/>`).join("")}
      </g>
      <g opacity=".95">
        <rect x="120" y="250" width="180" height="200" rx="6" fill="${tint}"/>
        <rect x="300" y="290" width="240" height="160" rx="6" fill="#3f4d58"/>
        <rect x="560" y="230" width="200" height="220" rx="6" fill="${tint}" opacity=".92"/>
        <rect x="780" y="270" width="260" height="180" rx="6" fill="#48555f"/>
        <rect x="1060" y="240" width="210" height="210" rx="6" fill="${tint}" opacity=".9"/>
      </g>
      <g stroke="#e5c542" stroke-width="7" fill="none" opacity=".9">
        <path d="M120 250 V450 M300 290 V450 M560 230 V450 M780 270 V450 M1060 240 V450"/>
        <path d="M120 250 H300 M560 230 H780 M1060 240 H1270"/>
      </g>
      <g>${rollers.join("")}</g>
      <rect x="110" y="360" width="1080" height="18" rx="6" fill="#8d99a2"/>
      <rect x="110" y="378" width="1080" height="8" rx="4" fill="#6f7a83"/>
      <g>${boxes.join("")}</g>
      <rect x="1250" y="300" width="150" height="170" rx="6" fill="#c9d3d9" opacity=".7"/>
    </svg>`;
  }

  function videoDataUri(line) {
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(factorySVG(line));
  }

  /* ============================================================
     1. КАМЕРЫ
     ============================================================ */
  function renderCameras() {
    /* Выбранная линия всегда должна существовать: если её убрали
       из настроек, показываем первую доступную, иначе экран
       останется с пустым таймлайном без объяснения. */
    if (data.lines.indexOf(state.camLine) === -1) {
      state.camLine = data.lines[0] || 1;
    }
    const line = state.camLine;
    /* Таймлайн показывает и открытый простой (идущий прямо сейчас):
       он ещё не завершён, но оператор должен видеть, что линия стоит. */
    const evs = data.events(state.day, line);
    /* Камера выбирается по выбранной линии, а не всегда первая:
       иначе при переключении на «Линию 2» заголовок продолжал
       называть «Камера №1». */
    const cams = data.cameras;
    const cam = cams.find((c) => c.id === line) || cams[0];
    const v = state.video;
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();

    // сегменты активности за сутки
    const segs = evs
      .map((e) => {
        const left = (e.startMin / 1440) * 100;
        const w = ((e.endMin - e.startMin) / 1440) * 100;
        return `<div class="activity-seg absent" style="left:${left}%;width:${w}%"
                   data-start="${e.start}" data-end="${e.end}"
                   data-who="${esc(e.employeeFull)}"
                   data-min="${e.durationSec / 60}"></div>`;
      })
      .join("");

    // Сегменты «на месте» до первого простоя и после последнего.
    // Хвост считаем как 100% - left, иначе полоса вылезает за трек.
    const firstPct = evs.length ? (evs[0].startMin / 1440) * 100 : 0;
    const lastEndPct = evs.length ? (evs[evs.length - 1].endMin / 1440) * 100 : 0;
    const tailPct = Math.max(0, 100 - lastEndPct);

    const presentSeg = evs.length
      ? `<div class="activity-seg present" style="left:0%;width:${firstPct}%"></div>
         <div class="activity-seg present" style="left:${lastEndPct}%;width:${tailPct}%"></div>`
      : `<div class="activity-seg present" style="left:0%;width:100%"></div>`;

    const tip = evs.length
      ? `<div class="activity-tip" id="actTip">${icon("user")}<span>${evs[0].start} – ${evs[0].end}</span></div>`
      : "";

    const ts = `${U.pad(now.getDate())}.${U.pad(now.getMonth() + 1)}.${now.getFullYear()} ${U.pad(now.getHours())}:${U.pad(now.getMinutes())}:${U.pad(now.getSeconds())}`;

    return `
    ${renderNowPanel()}

    <div class="cam-layout">
      <div class="card cam-card">
        <div class="cam-head">
          <h2>${esc(cam.name)}</h2>
          <span class="online-pill ${cam.online ? "" : "is-off"}">
            <span class="dot ${cam.online ? "dot-green" : "dot-red"}"></span>${cam.online ? "Онлайн" : "Офлайн"}
          </span>
          <div class="line-switch" id="lineSwitch">
            ${data.lines.map((l) => `<button data-line="${l}" class="${l === line ? "is-active" : ""}">Линия ${l}</button>`).join("")}
          </div>
        </div>

        <div class="video-stage">
          <!-- Раньше здесь стоял <img src="/api/video/stream">: браузер сам
               разбирал поток MJPEG. На части машин Chrome переставал
               перерисовывать такой <img>, хотя поток продолжал идти, —
               оператор видел первый кадр навсегда. Теперь поток читает
               videofeed.js и рисует кадры на canvas; соединение он же
               и закрывает, поэтому висящих подключений не остаётся. -->
          <canvas id="videoFeed" class="video-canvas" width="960" height="540" role="img" aria-label="Видео с камеры ${esc(cam.name)}"></canvas>
          <div class="video-overlay">
            <div class="ov-top">
              <span class="ov-chip">${esc(v.modeLabel || v.mode)} · Линия ${line}</span>
            </div>
            <div class="ov-bottom">
              <span class="ov-chip rec"><span class="dot dot-red"></span>LIVE</span>
              <span class="ov-chip ov-time">${ts}</span>
            </div>
          </div>
          <div class="video-empty" id="videoEmpty" hidden></div>
        </div>

        <div class="player-bar">
          <span class="feed-legend"><i class="dot dot-green"></i>Рабочий в кадре</span>
          <span class="feed-legend"><i class="dot dot-red"></i>Линия стоит</span>
          <span class="grow"></span>
          <span>${esc(cam.online ? "поток активен" : "нет сигнала")}</span>
        </div>
      </div>

      <div class="card activity-card">
        <div class="activity-head">${icon("clock")}<h3>Активность</h3></div>
        <div class="activity-track" id="actTrack">
          ${presentSeg}${segs}
          <div class="activity-now" style="left:${(nowMin / 1440) * 100}%"></div>
          ${tip}
        </div>
        <div class="activity-axis"><span>06:00</span><span>12:00</span><span>18:00</span><span>00:00</span></div>
        ${renderScenarioPicker(v)}
      </div>
    </div>`;
  }

  /* Переключатель демо-сценариев под таймлайном активности.

     Показывается ТОЛЬКО в режиме «Демо — ролики сценариев».
     Раньше блок был виден всегда, и это вводило в заблуждение:
     сценарии относятся к роликам, а в синтетике или в режиме камеры
     из браузера переключение ничего не меняло — оператор щёлкал по
     отрезку и видел, что картинка не изменилась.

     Каждый отрезок — один ролик. Сценарий переключается щелчком по
     отрезку, без отдельного списка и без перезагрузки страницы: в
     демо это самое частое действие, и лишний шаг между «увидел
     простой» и «показал его на видео» стирает смысл демонстрации.

     Порядок и подписи берём у backend'а: он же сообщает, какие
     ролики на месте. Без этого интерфейс предлагал бы выбрать
     сценарий, который нечем показать. */
  function renderScenarioPicker(v) {
    const items = v.scenarios || [];
    if (!items.length) return "";
    /* Сценарии — это ролики из demo/videos. В других режимах их нет,
       поэтому и показывать нечего: клик по отрезку не изменил бы
       ничего. */
    if (v.mode !== "demo") return "";
    const current = v.demoScenario || "";

    const segs = items
      .map((item) => {
        const key = item.scenario;
        const label = (v.scenarioLabels && v.scenarioLabels[key]) || key;
        const cls = [
          "scenario-seg",
          key === current ? "is-active" : "",
          item.available ? "" : "is-missing",
        ]
          .filter(Boolean)
          .join(" ");
        return `<button class="${cls}" data-scenario="${key}" type="button"
                  title="${esc(label)}${item.available ? " — " + esc(item.file) : " — ролик не найден"}">
                  <span class="scenario-num">${esc(String(v.scenarioOrder.indexOf(key) + 1))}</span>
                  <span class="scenario-label">${esc(label)}</span>
                </button>`;
      })
      .join("");

    return `
      <div class="scenario-picker">
        <div class="scenario-head">
          ${icon("film")}<h4>Демо-сценарии</h4>
          <span class="scenario-hint">отрезок — один ролик, щёлкните чтобы посмотреть</span>
        </div>
        <div class="scenario-track" id="scenarioTrack">${segs}</div>
        <div class="scenario-note" id="scenarioNote"></div>
      </div>`;
  }

  /* ============================================================
     2. ЛИНИИ
     ============================================================ */
  /* Раскладка пересекающихся событий по дорожкам (columns).
     События, перекрывающиеся по времени, раскладываются в соседние колонки
     внутри одной линии, поэтому карточки физически не могут наложиться. */
  function packLanes(events) {
    const sorted = events
      .slice()
      .sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
    const lanes = []; // lanes[i] = время освобождения дорожки

    sorted.forEach((e) => {
      let lane = lanes.findIndex((freeAt) => freeAt <= e.startMin);
      if (lane === -1) {
        lanes.push(e.endMin);
        lane = lanes.length - 1;
      } else {
        lanes[lane] = e.endMin;
      }
      e._lane = lane;
    });

    // Плотность колонок: чем больше дорожек, тем уже карточка
    const total = Math.max(1, lanes.length);
    sorted.forEach((e) => {
      e._lanes = total;
      e._span = 1;
    });

    // Одновременные события (короткое общее окно) объединяем в один блок,
    // чтобы не раздувать число дорожек из-за почти касающихся интервалов
    return sorted;
  }

  const TL_HEIGHT = 520;

  /* Число дней в месяце (по году и месяцу) */
  function daysInMonth(y, m) {
    return new Date(y, m + 1, 0).getDate();
  }

  /* Закрыть все выпадающие списки выбора даты */
  function closeDateMenus() {
    document.querySelectorAll(".dd-menu").forEach((m) => m.setAttribute("hidden", ""));
    document.querySelectorAll(".dd-btn").forEach((b) => b.setAttribute("aria-expanded", "false"));
  }

  /* ============================================================
     ТЕКУЩЕЕ СОСТОЯНИЕ (Handoff, раздел 17)
     ------------------------------------------------------------
     Четыре поля строго по контракту SystemStatus:
       LINE     — состояние линии
       PERSON   — есть ли рабочий
       DOWNTIME — текущий простой
       LOSS     — рассчитанный ущерб

     Значения приходят из /api/status готовыми. Ущерб здесь НЕ
     пересчитывается: по Handoff (раздел 14) право считать его
     есть только у backend'а.
     ============================================================ */
  function renderNowPanel() {
    const st = data.status();
    const showLoss = state.settings.show_loss;
    const cost = data.config() ? data.config().cost_per_minute : state.settings.cost_per_minute;

    /* Данных ещё нет: сервер не отвечает или конвейер запускается.
       Причины разные и требуют разных слов: «запустите сервер» при
       живом, но ещё пустом backend'е отправляет оператора искать
       несуществующую проблему. */
    if (!st || !st.line_status) {
      const online = global.PV.api.ready();
      const text = online
        ? "Сервер отвечает, конвейер запускается — данных пока нет."
        : "Нет связи с backend'а. Запустите сервер и обновите страницу.";
      return `<div class="card now-panel is-idle">
        <div class="now-head">${icon("bars")}<h2>Текущее состояние</h2></div>
        <div class="now-empty">
          ${icon("alert")}
          <span>${esc(text)}</span>
        </div>
      </div>`;
    }

    const lineMap = {
      WORKING: { label: "WORKING", cls: "is-working", dot: "dot-green" },
      STOPPED: { label: "STOPPED", cls: "is-stopped", dot: "dot-red" },
      UNKNOWN: { label: "UNKNOWN", cls: "is-unknown", dot: "dot-gray" },
    };
    const line = lineMap[st.line_status] || lineMap.UNKNOWN;
    const person = st.person_present;

    const cells = [
      `<div class="now-cell">
         <div class="now-k">LINE</div>
         <div class="now-v ${line.cls}"><span class="dot ${line.dot}"></span>${line.label}</div>
       </div>`,
      `<div class="now-cell">
         <div class="now-k">PERSON</div>
         <div class="now-v ${person ? "is-ok" : "is-bad"}">
           ${person ? icon("check") : icon("close")} ${person ? "DETECTED" : "NOT DETECTED"}
         </div>
       </div>`,
      `<div class="now-cell">
         <div class="now-k">DOWNTIME</div>
         <div class="now-v ${st.downtime_seconds > 0 ? "is-bad" : ""}">${U.fmtHM(st.downtime_seconds)}</div>
       </div>`,
      `<div class="now-cell">
         <div class="now-k">LOSS</div>
         <div class="now-v ${st.estimated_loss > 0 ? "is-bad" : ""}">
           ${showLoss ? U.fmtMoney(st.estimated_loss) : "—"}
         </div>
       </div>`,
    ].join("");

    const sync = !data.isLive()
      ? "Нет связи с сервером — показаны демонстрационные значения"
      : global.PV.api.ready()
      ? "Данные с сервера"
      : "Нет связи с сервером — показаны последние полученные данные";

    return `<div class="card now-panel">
      <div class="now-head">
        ${icon("bars")}
        <h2>Текущее состояние</h2>
        <span class="now-sync ${data.isLive() ? "is-live" : "is-mock"}">${sync}</span>
      </div>
      <div class="now-grid">${cells}</div>
      <div class="now-foot">Стоимость минуты простоя — ${U.fmtMoney(cost)}</div>
    </div>`;
  }

  function renderLines() {
    const day = state.day;
    const iso = U.dayISO(day);
    const perLine = {};
    data.lines.forEach((l) => (perLine[l] = packLanes(data.events(iso, l))));
    const dt = data.downtimeByLine(iso);

    const HOURS = [0, 6, 12, 18, 24];

    const cols = data.lines
      .map((l) => {
        const evs = perLine[l];
        const lanes = Math.max(1, ...evs.map((e) => e._lanes || 1));

        return `
        <div class="tl-col">
          ${evs
            .map((e) => {
              const hPct = ((e.endMin - e.startMin) / 1440) * 100;
              const hPx = (hPct / 100) * TL_HEIGHT;
              const top = (e.startMin / 1440) * 100;
              const h = Math.max(hPct, (22 / TL_HEIGHT) * 100); // минимум 22px

              const lane = e._lane || 0;
              const n = e._lanes || 1;
              // Горизонтальное позиционирование по дорожкам + зазоры 6px
              const gap = 6;
              const usable = 100 - (gap * (n + 1));
              const w = usable / n;
              const left = gap + lane * (w + gap);

              const cls =
                (hPx < 44 ? " is-short is-tiny" : hPx < 84 ? " is-short" : "") +
                // при нескольких дорожках карточка узкая — время не помещается
                (n > 1 ? " is-narrow" : "");
              const style = `top:${top}%;height:${h}%;left:${left}%;width:${w}%;` +
                (n > 1 ? "--lanes:" + n + ";" : "");

              if (e.kind === "absent" || e.kind === "open") {
                /* Открытый простой подписан отдельно: он идёт
                   прямо сейчас, у него нет ни конца, ни ущерба. */
                const desc = e.open
                  ? "Простой продолжается"
                  : "Отсутствовал(а) на рабочем месте";
                const time = e.open
                  ? `${e.start} – ${e.end} <span>идёт</span>`
                  : `${e.start} – ${e.end}<span>${U.fmtHM(e.durationSec)}</span>`;
                return `<div class="ev absent${e.open ? " is-open" : ""}${cls}" style="${style}"
                             data-line="${l}" data-start="${e.start}" data-end="${e.end}"
                             data-who="${esc(e.employeeFull)}" data-min="${Math.round(e.durationSec / 60)}"
                             title="${esc(e.employeeFull)} · ${e.start}–${e.end} · ${U.fmtHM(e.durationSec)}">
                            <span class="ev-icon">${icon("user")}</span>
                            <span class="ev-info">
                              <span class="ev-name">${esc(e.employee)}</span>
                              <span class="ev-desc">${desc}</span>
                            </span>
                            <span class="ev-time"><b>${time}</b></span>
                          </div>`;
              }
              return `<div class="ev present${cls}" style="${style}"
                             data-line="${l}" data-start="${e.start}" data-end="${e.end || ""}"
                             data-who="Персонал на месте" data-min="0"
                             title="Персонал на месте · ${e.start}–${e.end || "…"}">
                      <span class="ev-icon">${icon("user")}</span>
                      <span class="ev-info">
                        <span class="ev-name">${esc(e.employee)}</span>
                        <span class="ev-desc">Смена идёт штатно</span>
                      </span>
                      <span class="ev-time"><b>${e.start} – ${e.end || "…"}</b><span>на месте</span></span>
                    </div>`;
            })
            .join("")}
        </div>`;
      })
      .join("");

    const dtCells = data.lines
      .map(
        (l) => `<div class="dt-cell">
                  <div class="dt-val ${dt[l] > 0 ? "is-bad" : ""}">${U.fmtHM(dt[l])}</div>
                  <div class="dt-label">Линия ${l}</div>
                </div>`
      )
      .join("");

    /* Один элемент управления на «число + месяц».
       Внутри списка — две независимые секции: месяц и число.
       Месяц выбирается здесь, год — в верхней панели. */
    const today = new Date();
    const chev = icon("right", "chev-down");
    const dim = daysInMonth(day.getFullYear(), day.getMonth());
    const isToday =
      day.getDate() === today.getDate() &&
      day.getMonth() === today.getMonth() &&
      day.getFullYear() === today.getFullYear();

    const monthOptions = U.MONTHS_NOM.map((m, i) => {
      const future =
        day.getFullYear() > today.getFullYear() ||
        (day.getFullYear() === today.getFullYear() && i > today.getMonth());
      return `<button class="dd-item dd-month${i === day.getMonth() ? " is-active" : ""}"
                data-pick="month" data-v="${i}" ${future ? "disabled" : ""}
                title="${m}">${m.slice(0, 3)}</button>`;
    }).join("");

    /* Сетка чисел: пустые ячейки до 1-го числа + заголовки дней недели,
       иначе числа читаются как повторяющиеся по столбцам. */
    const DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
    const lead = (new Date(day.getFullYear(), day.getMonth(), 1).getDay() + 6) % 7;

    const dayHead = DOW
      .map((d, i) => `<span class="dd-dow${i >= 5 ? " is-weekend" : ""}">${d}</span>`)
      .join("");
    const dayLead = Array.from({ length: lead }, () => "<span></span>").join("");

    const dayOptions = Array.from({ length: dim }, (_, i) => {
      const n = i + 1;
      const future = new Date(day.getFullYear(), day.getMonth(), n) > today;
      const dow = new Date(day.getFullYear(), day.getMonth(), n).getDay();
      const weekend = dow === 0 || dow === 6;
      return `<button class="dd-day${n === day.getDate() ? " is-active" : ""}${weekend ? " is-weekend" : ""}"
                data-pick="day" data-v="${n}" ${future ? "disabled" : ""}
                title="${n} ${U.MONTHS_GEN[day.getMonth()]}">${n}</button>`;
    }).join("");

    return `
    <div class="date-nav">
      ${icon("calendar", "big")}
      <div class="dd">
        <button class="dd-btn dd-date" data-dd="date" aria-haspopup="listbox" aria-expanded="false">
          <span>${day.getDate()} ${U.MONTHS_GEN[day.getMonth()]}</span>${chev}
        </button>
        <div class="dd-menu dd-menu-date" data-menu="date" hidden>
          <div class="dd-sec">
            <div class="dd-sec-title">Месяц</div>
            <div class="dd-months">${monthOptions}</div>
          </div>
          <div class="dd-sec">
            <div class="dd-sec-title">Число</div>
            <div class="dd-days">
              <div class="dd-days-head">${dayHead}</div>
              <div class="dd-days-grid">${dayLead}${dayOptions}</div>
            </div>
          </div>
        </div>
      </div>
      ${isToday ? '<span class="tag">Сегодня</span>' : '<button class="btn-today" data-day="today">Сегодня</button>'}
    </div>

    <div class="card timeline-wrap">
      <div class="tl-axis">
        ${HOURS.map((h) => `<div>${U.pad(h % 24)}:00</div>`).join("")}
      </div>
      <div class="tl-body">
        <div class="tl-head" style="--cols:${data.lines.length}">
          ${data.lines
        .map((l) => `<div>${icon("bars")}<span>Линия ${l}</span></div>`)
        .join("")}
        </div>
        <div class="tl-row" style="--cols:${data.lines.length};--row-h:104px">${cols}</div>
      </div>
    </div>

    <div class="card downtime-card">
      <div class="dt-title">${icon("clock")}<h3>Простои</h3></div>
      <div class="dt-grid" style="--cols:${data.lines.length}">${dtCells}</div>
    </div>`;
  }

  /* ============================================================
     3. СВОДНАЯ
     ============================================================ */
  function renderOverview() {
    const iso = U.dayISO(state.day);
    const evs = data.finishedForDay(iso);
    const dt = data.downtimeByLine(iso);
    const totalSec = Object.values(dt).reduce((a, b) => a + b, 0);
    const totalLoss = evs.reduce((a, b) => a + b.loss, 0);
    const avg = evs.length ? totalSec / evs.length : 0;
    const maxSec = Math.max(1, ...Object.values(dt));

    const kpi = (label, val, sub, ico, cls, icoCls) => `
      <div class="card kpi">
        <div class="kpi-head"><span class="kpi-label">${label}</span><span class="kpi-ico ${icoCls || ""}">${icon(ico)}</span></div>
        <div class="kpi-val">${val}</div>
        <div class="kpi-sub">${sub}</div>
      </div>`;

    const bars = data.lines
      .map(
        (l) => `<div class="bar-row">
          <span class="bar-name">Линия ${l}</span>
          <span class="bar-track"><i class="${dt[l] > 0 ? "bad" : ""}" style="width:${(dt[l] / maxSec) * 100}%"></i></span>
          <span class="bar-val">${U.fmtHM(dt[l])}</span>
        </div>`
      )
      .join("");

    const lineRows = data.lines
      .map((l) => {
        const le = evs.filter((e) => e.line === l);
        const st = data.statusOf(iso, l);
        const tag =
          st === data.LineStatus.STOPPED
            ? '<span class="tag bad">Простой</span>'
            : '<span class="tag">В работе</span>';
        return `<div class="line-row">
          <div>Линия ${l} &nbsp; ${tag}</div>
          <div class="num">${le.length}</div>
          <div class="num">${U.fmtHM(dt[l])}</div>
          <div class="num">${state.settings.show_loss ? U.fmtMoney(le.reduce((a, b) => a + b.loss, 0)) : "—"}</div>
        </div>`;
      })
      .join("");

    const evRows = evs
      .slice()
      .sort((a, b) => b.startMin - a.startMin)
      .slice(0, 8)
      .map(
        (e) => {
          const camera = data.cameras.find((item) => item.id === e.camera_id);
          const eventLabel = e.event === "DOWNTIME_STARTED"
            ? "Начало простоя"
            : e.event === "DOWNTIME_FINISHED"
              ? "Простой завершён"
              : "Простой";
          return `<tr>
            <td>${esc(e.start)}–${e.open ? "сейчас" : esc(e.end)}</td>
            <td>${esc(camera ? camera.name : `Камера №${e.camera_id}`)}</td>
            <td>${eventLabel}</td>
            <td class="num">${U.fmtHM(e.durationSec)}</td>
            <td class="num">${state.settings.show_loss ? U.fmtMoney(e.loss) : "—"}</td>
          </tr>`;
        }
      )
      .join("");

    return `
    <div class="page-title">${icon("chart")}<h1>Сводная за ${U.fmtDateLong(state.day)}</h1></div>

    <div class="kpi-grid">
      ${kpi("Всего простоев", U.fmtHM(totalSec), `событий: ${evs.length}`, "clock", "", "amber")}
      ${kpi("Средний простой", U.fmtHM(avg), "на одно событие", "bars", "", "")}
      ${kpi("Ущерб", state.settings.show_loss ? U.fmtMoney(totalLoss) : "—", `${state.settings.cost_per_minute} ₽/мин`, "loss", "", "red")}
      ${kpi("Линий в работе", `${data.lines.filter((l) => dt[l] === 0).length} / ${data.lines.length}`, "остальные — простой", "cam", "", "")}
    </div>

    <div class="grid-2">
      <div class="card panel">
        <div class="panel-title">Простои по линиям</div>
        <div class="panel-sub">За ${U.fmtDateLong(state.day)}</div>
        <div class="bars">${bars}</div>

        <div class="panel-title" style="margin-top:26px">Сводка по линиям</div>
        <div class="panel-sub">События, длительность, ущерб</div>
        <div class="line-rows">
          <div class="line-row head"><div>Линия</div><div class="num">Событий</div><div class="num">Простой</div><div class="num">Ущерб</div></div>
          ${lineRows}
        </div>
      </div>

      <div class="card panel">
        <div class="panel-title">Последние события</div>
        <div class="panel-sub">Хронологически</div>
        ${evRows
        ? `<table class="ev-table">
              <thead><tr><th>Время</th><th>Камера</th><th>Событие</th><th class="num">Длит.</th><th class="num">Ущерб</th></tr></thead>
              <tbody>${evRows}</tbody>
            </table>`
        : '<div class="empty">Простоев не зафиксировано</div>'}
      </div>
    </div>`;
  }

  /* ============================================================
     4. СОТРУДНИКИ
     ============================================================ */
  function renderEmployees() {
    const q = state.employeesQuery.trim().toLowerCase();
    const lineF = state.employeesLine;

    let rows = data.employees.filter((e) => {
      if (lineF !== "all" && String(e.line) !== lineF) return false;
      if (!q) return true;
      return (e.name + " " + e.position).toLowerCase().includes(q);
    });

    const perPage = 12;
    const pages = Math.max(1, Math.ceil(rows.length / perPage));
    const page = U.clamp(state.employeesPage, 1, pages);
    state.employeesPage = page;
    const slice = rows.slice((page - 1) * perPage, page * perPage);

    const absentCount = data.employees.filter((e) => e.status === "absent").length;

    const body = slice
      .map(
        (e) => `<tr>
          <td>
            <div class="who">
              <div class="avatar ${e.status === "absent" ? "absent" : ""}">${esc(initials(e.name))}</div>
              <div><div class="who-name">${esc(e.name)}</div><div class="who-role">${esc(e.position)}</div></div>
            </div>
          </td>
          <td>Линия ${e.line}</td>
          <td>${esc(e.shift)} <span class="who-role">с ${U.pad(e.shiftStart)}:00</span></td>
          <td>${statusTag(e.status)}</td>
          <td class="num">${e.status === "absent" ? U.fmtHM(e.absentMin * 60) : "—"}</td>
          <td>${global.PV.c.progressBar(e.attendance)}</td>
          <td class="num">${e.status === "absent" && state.settings.show_loss ? U.fmtMoney(e.lost) : "—"}</td>
        </tr>`
      )
      .join("");

    return `
    <div class="page-title">${icon("user")}<h1>Сотрудники</h1>
      <span class="tag idle">${absentCount} отсутствуют сейчас</span>
    </div>

    ${data.employeesAreMock
        ? `<div class="notice">${icon("alert")}<span>Раздел демонстрационный: backend не ведёт сотрудников, а в контракте их нет. Список и суммы здесь — пример, а не данные завода.</span></div>`
        : ""}

    <div class="toolbar">
      <label class="search">${icon("search")}
        <input id="empSearch" type="search" placeholder="Поиск по имени или должности" value="${esc(state.employeesQuery)}">
      </label>
      <select class="select" id="empLine">
        <option value="all">Все линии</option>
        ${data.lines.map((l) => `<option value="${l}" ${String(l) === lineF ? "selected" : ""}>Линия ${l}</option>`).join("")}
      </select>
      <button class="btn-sec" id="empExport">${icon("download")}Экспорт CSV</button>
    </div>

    <div class="card table-card">
      ${slice.length
        ? `<table class="tbl">
            <thead><tr>
              <th>Сотрудник</th><th>Линия</th><th>Смена</th><th>Статус</th>
              <th class="num">Отсутствие</th><th>Посещаемость</th><th class="num">Ущерб</th>
            </tr></thead>
            <tbody>${body}</tbody>
          </table>`
        : '<div class="empty">Ничего не найдено</div>'}
      <div class="pager">
        <span>Показано ${slice.length} из ${rows.length} · стр. ${page} из ${pages}</span>
        <div class="pager-btns">
          <button data-page="-1" ${page === 1 ? "disabled" : ""}>${icon("left")}</button>
          <button data-page="1" ${page === pages ? "disabled" : ""}>${icon("right")}</button>
        </div>
      </div>
    </div>`;
  }

  /* ============================================================
     5. ОТЧЁТЫ
     ============================================================ */
  function renderReports() {
    const y = state.day.getFullYear();
    const m = state.day.getMonth();
    const per = state.reportPeriod;
    let evs = [];
    let label = "";

    if (per === "day") {
      evs = data.finishedForDay(state.day);
      label = U.fmtDateFull(state.day);
    } else if (per === "week") {
      const start = U.addDays(state.day, -state.day.getDay());
      for (let i = 0; i < 7; i++) {
        evs = evs.concat(data.finishedForDay(U.addDays(start, i)));
      }
      label = `${U.fmtDateLong(start)} — ${U.fmtDateLong(U.addDays(start, 6))}`;
    } else {
      evs = data.finishedForMonth(y, m);
      label = `${U.MONTHS_NOM[m]} ${y}`;
    }

    const totalSec = evs.reduce((a, b) => a + b.durationSec, 0);
    const totalLoss = evs.reduce((a, b) => a + b.loss, 0);

    // Топ сотрудников
    const byEmp = {};
    evs.forEach((e) => {
      const k = e.employeeFull;
      byEmp[k] = byEmp[k] || { name: k, sec: 0, loss: 0, n: 0 };
      byEmp[k].sec += e.durationSec;
      byEmp[k].loss += e.loss;
      byEmp[k].n++;
    });
    const top = Object.values(byEmp).sort((a, b) => b.sec - a.sec).slice(0, 8);

    // По линиям
    const byLine = data.lines.map((l) => {
      const le = evs.filter((e) => e.line === l);
      return {
        line: l, n: le.length, sec: le.reduce((a, b) => a + b.durationSec, 0),
        loss: le.reduce((a, b) => a + b.loss, 0)
      };
    });
    const maxLineSec = Math.max(1, ...byLine.map((r) => r.sec));

    // По дням месяца (для графика)
    const byDay = {};
    evs.forEach((e) => {
      const d = parseInt(e.day.slice(8), 10);
      byDay[d] = (byDay[d] || 0) + e.durationSec;
    });
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const maxDaySec = Math.max(1, ...Object.values(byDay));
    const chart = Array.from({ length: daysInMonth }, (_, i) => {
      const d = i + 1;
      const v = byDay[d] || 0;
      const h = Math.max(3, (v / maxDaySec) * 100);
      return `<div class="bar-row" style="grid-template-columns:30px 1fr 52px;gap:8px">
          <span class="bar-name" style="font-size:12px">${d}</span>
          <span class="bar-track" style="height:8px"><i class="${v > 0 ? "bad" : ""}" style="width:${v > 0 ? h : 0}%"></i></span>
          <span class="bar-val" style="font-size:12px">${v ? Math.round(v / 60) + "м" : "—"}</span>
        </div>`;
    }).join("");

    const lineRows = byLine
      .map(
        (r) => `<div class="rank" style="grid-template-columns:34px 1fr 1fr 110px">
          <span class="rank-num">${r.line}</span>
          <span class="rank-name">Линия ${r.line}</span>
          <span class="bar-track" style="height:9px"><i class="${r.sec > 0 ? "bad" : ""}" style="width:${(r.sec / maxLineSec) * 100}%"></i></span>
          <span class="rank-time">${U.fmtHM(r.sec)} · ${r.n} соб.</span>
        </div>`
      )
      .join("");

    const rankRows = top
      .map(
        (t, i) => `<div class="rank">
          <span class="rank-num">${i + 1}</span>
          <span class="rank-name">${esc(t.name)}<small>${t.n} ${plural(t.n, "событие", "события", "событий")}</small></span>
          <span class="bar-track" style="height:9px"><i class="bad" style="width:${(t.sec / Math.max(1, top[0].sec)) * 100}%"></i></span>
          <span class="rank-time">${U.fmtHM(t.sec)}</span>
        </div>`
      )
      .join("");

    return `
    <div class="page-title">${icon("doc")}<h1>Отчёты</h1></div>

    <div class="report-filters">
      <div class="chips" id="repPeriod">
        <button data-p="day" class="${per === "day" ? "is-active" : ""}">День</button>
        <button data-p="week" class="${per === "week" ? "is-active" : ""}">Неделя</button>
        <button data-p="month" class="${per === "month" ? "is-active" : ""}">Месяц</button>
      </div>
      <button class="btn-sec" id="repPrint">${icon("printer")}Печать</button>
      <button class="btn-primary" id="repExport">${icon("download")}Скачать CSV</button>
    </div>

    <div class="card">
      <div class="rep-head">
        <div>
          <h2>Отчёт по простоям</h2>
          <p>${esc(label)} · ${evs.length} ${plural(evs.length, "событие", "события", "событий")}</p>
        </div>
        <div class="rep-total">
          <div class="v">${U.fmtHM(totalSec)}</div>
          <div class="l">суммарный простой</div>
          <div class="v" style="font-size:20px;color:var(--red);margin-top:8px">${state.settings.show_loss ? U.fmtMoney(totalLoss) : "—"}</div>
          <div class="l">расчётный ущерб</div>
        </div>
      </div>

      <div class="rep-sec">
        <h3>По линиям</h3>
        <div class="sub">Суммарная длительность и число событий</div>
        <div class="ranking">${lineRows}</div>
      </div>

      <div class="rep-sec">
        <h3>Топ сотрудников по простоям</h3>
        <div class="sub">Кто чаще всего покидает рабочее место</div>
        <div class="ranking">${rankRows || '<div class="empty">Нет данных за период</div>'}</div>
      </div>

      <div class="rep-sec">
        <h3>Распределение по дням</h3>
        <div class="sub">Минут простоя в день</div>
        <div class="bars">${chart}</div>
      </div>
    </div>`;
  }

  function plural(n, one, few, many) {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }

  /* ============================================================
     6. НАСТРОЙКИ
     ============================================================ */
  /* ============================================================
     РЕЖИМ КАМЕРЫ: подсказки и выбор камеры
     ============================================================ */
  /* Кадры с камеры браузера: идёт передача или нет. Здесь только
     состояние для подписи — сами кадры идёт web/js/browsercam.js,
     а считает всё сервер. */
  function isBrowserCamActive() {
    return !!(global.PV.browsercam && global.PV.browsercam.isActive());
  }

  function browserCamHint() {
    if (!global.PV.browsercam) return "Модуль передачи не загружен";
    const info = global.PV.browsercam.describe();
    if (info.error) return info.error;
    if (info.paused) {
      return (
        "Передача приостановлена: вкладка не на экране. " +
        "Вернитесь на вкладку — передача продолжится сама."
      );
    }
    if (info.active) return "Идёт передача: отправлено кадров — " + info.sent;
    return (
      "Кадры с камеры отправляются на сервер, обрабатывает их сервер. " +
      "Нужен HTTPS: по http браузер не отдаст камеру."
    );
  }

  async function toggleBrowserCam() {
    const button = document.getElementById("browserCamToggle");
    const note = document.getElementById("modeNote");
    if (!button) return;
    button.disabled = true;
    try {
      if (isBrowserCamActive()) {
        global.PV.browsercam.stop();
        if (note) note.textContent = "Передача остановлена";
      } else {
        const preview = document.getElementById("browserPreview");
        const error = await global.PV.browsercam.start(preview);
        if (error) {
          if (note) note.textContent = error;
          toast(error, "err");
        } else {
          if (note) note.textContent = "Передача идёт, считает сервер";
          toast("Кадры с камеры передаются на сервер");
        }
      }
      this.render("settings");
    } finally {
      button.disabled = false;
    }
  }

  function modeHint(mode) {
    if (mode === "browser")
      return "Камера этого компьютера, но обрабатывает сервер. Нажмите «Применить» и разрешите доступ: браузер будет передавать кадры, а считать простой и ущерб будет сервер.";
    if (mode === "webcam")
      return "Камера КОМПЬЮТЕРА СЕРВЕРА. В режиме выставочного сервера такой камеры нет — выберите «из браузера», чтобы отдать кадры с вашего ноутбука.";
    if (mode === "rtsp")
      return "Сетевая камера. В Alpha недоступна: в окне камер будет «нет сигнала».";
    if (mode === "demo")
      return "Готовые ролики сценариев из папки demo/. Пока их нет — используйте синтетику.";
    return "Сцена рисуется программно: видно всё — рамку человека, его путь и простой. Это не данные завода.";
  }

  function webcamOptions(v) {
    if (!v.webcams.length) {
      return '<option value="0">Камеры не найдены</option>';
    }
    return v.webcams
      .map(
        (c) =>
          `<option value="${c.index}" ${c.index === v.webcamIndex ? "selected" : ""}>` +
          `${esc(c.name)}${c.width ? " — " + c.width + "×" + c.height : ""}` +
          `${c.works ? "" : " (не отдаёт кадр)"}</option>`
      )
      .join("");
  }

  function webcamHint(v) {
    if (!v.webcams.length) {
      return "Камеры не найдены. Проверьте, что камера подключена и не занята другой программой.";
    }
    const chosen = v.webcams.find((c) => c.index === v.webcamIndex);
    if (chosen && !chosen.works) {
      return "Выбранная камера открывается, но не отдаёт кадр — в окне будет «нет сигнала».";
    }
    return "Список получен перебором устройств.";
  }

  /* Загружает режим камеры и список веб-камер с сервера. */
  async function loadVideoConfig() {
    /* Адреса берём у API-клиента, а не пишем относительными путями:
       при открытии Dashboard не с адреса backend'а (отдельный
       статический сервер или другая машина) запросы настроек уходили
       не туда, режим камеры оставался «синтетика», и оператор вообще
       не понимал, в каком режиме система работает. */
    const base = global.PV.api.baseUrl();
    /* Запросы идут через api.get(), а не через голый fetch: почти все
       /api/* закрыты паролем. Без заголовка X-Promvizor-Auth сервер
       отвечает 401, и — что хуже всего — 401 НЕ считается ошибкой:
       поле оставалось со значением по умолчанию, и после перезагрузки
       страницы список режимов показывал «синтетику» независимо от
       того, что сервер на самом деле работает в другом режиме. */
    try {
      const mode = await global.PV.api.get("/api/video/mode");
      if (mode && mode.mode) {
        state.video.mode = mode.mode;
        state.video.modeLabel = mode.mode_label;
        state.video.webcamIndex = mode.webcam_index;
        state.video.threshold = mode.downtime_threshold_webcam_seconds;
        state.video.applied = true;
      }
    } catch (e) {
      /* сервер не ответил — настройки останутся такими, какие есть */
    }

    try {
      const list = await global.PV.api.get("/api/video/cameras");
      state.video.webcams = (list && list.items) || [];
    } catch (e) {
      state.video.webcams = [];
    }

    await loadScenarios(base);
  }

  /* Список демо-сценариев с сервера: какие ролики на месте и как
     сценарий называется по-русски. Через api.get() — эндпоинт тоже
     закрыт паролем. */
  async function loadScenarios(base) {
    try {
      const payload = await global.PV.api.get("/api/video/scenarios");
      if (!payload) return;
      state.video.scenarios = payload.items || [];
      state.video.scenarioLabels = payload.labels || {};
      state.video.demoScenario = payload.current || "";
    } catch (e) {
      state.video.scenarios = [];
    }
  }

  /* Переключение демо-сценария щелчком по отрезку. */
  async function selectScenario(key, button) {
    const base = global.PV.api.baseUrl();
    const note = document.getElementById("scenarioNote");
    const buttons = Array.from(
      document.querySelectorAll("#scenarioTrack .scenario-seg")
    );
    buttons.forEach((b) => b.classList.remove("is-pending"));
    if (button) button.classList.add("is-pending");
    if (note) note.textContent = "";

    try {
      const response = await fetch(
        base + "/api/video/scenario?scenario=" + encodeURIComponent(key),
        { method: "POST", headers: global.PV.api.authHeaders() }
      );
      if (!response.ok) {
        let detail = "HTTP " + response.status;
        try {
          const body = await response.json();
          if (body && body.detail) detail = String(body.detail);
        } catch (e) {
          /* тело не JSON — оставляем код ответа */
        }
        throw new Error(detail);
      }

      state.video.demoScenario = key;
      // Ролик другой — поток надо перезапустить, иначе в окне камеры
      // остался бы предыдущий кадр и выглядело бы, что ничего не сменилось.
      restartVideoFeed();
      setVideoError("");
      global.PV.sections.render("cameras");
      toast("Сценарий: " + (state.video.scenarioLabels[key] || key));
    } catch (err) {
      if (note) note.textContent = "Не удалось переключить сценарий: " + err.message;
      toast("Не удалось переключить сценарий: " + err.message, "err");
      buttons.forEach((b) => b.classList.remove("is-pending"));
    }
  }

  /* Применяет выбранный режим на сервере. */
  async function applyVideoMode() {
    const note = document.getElementById("modeNote");
    const button = document.getElementById("applyMode");
    if (!note || !button) return;

    const mode = document.getElementById("s_mode").value;
    const cameraSelect = document.getElementById("s_webcam");
    const webcamIndex = cameraSelect ? +cameraSelect.value || 0 : 0;

    button.disabled = true;
    note.textContent = "Переключаем источник видео…";
    try {
      const response = await fetch(
        global.PV.api.baseUrl() +
          `/api/video/mode?mode=${encodeURIComponent(mode)}&webcam_index=${webcamIndex}`,
        { method: "POST", headers: global.PV.api.authHeaders() }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "HTTP " + response.status);
      }
      const applied = await response.json();
      state.video.mode = applied.mode;
      state.video.webcamIndex = applied.webcam_index;
      /* Название режима обновляем тоже: в оверлее видео иначе
         осталась бы подпись от прошлого режима («Демо (синтетика)»
         при включённой веб-камере) — оператор смотрел бы не туда. */
      state.video.modeLabel = applied.mode_label || "";
      const error = applied.state && applied.state.error;
      const label = applied.mode_label || applied.mode;

      /* Подтверждаем переклющение ВСЕГДА, а не только когда ошибок нет.
         Раньше единственный тост показывал ошибку источника, из-за чего
         успешное переключение выглядело как сбой: выбираешь веб-камеру —
         а в тосте «Источник не открыт: DEMO-видео ещё не добавлены».
         Это была ошибка ПРОШЛОГО режима: конвейер переоткрывает источник
         не мгновенно, и в ответе ещё лежит прежний текст. */
      if (applied.mode === mode) {
        toast("Режим переключён: " + label);
        note.textContent = "";
      } else {
        // Сервер не принял выбор — например, режим запрещён окружением.
        toast("Сервер оставил режим: " + label, "err");
        note.textContent = "Выбор не применён";
      }

      /* Камера из браузера: включаем передачу только тогда, когда сервер
         действительно переключился на этот режим. Иначе оператор смотрел
         бы на пустое окно и думал, что камера сломалась. Уход с режима
         камеру выключивает: в комнате не должен гореть зелёный
         индикатор, когда сервер уже смотрит в другой источник. */
      if (applied.mode === "browser" && mode === "browser") {
        await global.PV.browsercam.start(
          document.getElementById("browserPreview")
        );
      } else if (applied.mode !== "browser" && isBrowserCamActive()) {
        global.PV.browsercam.stop();
      }

      // Проблема источника — отдельное сообщение, не вместо подтверждения.
      if (error) toast(error, "err");
      // Кадр изменился — картинка на вкладке «Камеры» должна смениться
      restartVideoFeed();
      /* Через global.PV.sections, а не просто render("settings"):
         функция вызывается из стрелочной обработчика, где this не
         определён. Раньше здесь был ReferenceError — режим на сервере
         переключался, а интерфейс сообщал об ошибке и оставался старым. */
      global.PV.sections.render("settings");
    } catch (err) {
      toast("Не удалось переключить режим: " + err.message, "err");
      note.textContent = "Не удалось применить";
    } finally {
      button.disabled = false;
    }
  }

  /* Переподключает поток после смены источника: картинка осталась бы
     от прежней камеры.
     Раньше здесь перезапрашивался <img>, и каждая перезапроска
     оставляла после себя ещё одно незакрытое соединение с потоком.
     Теперь соединение закрывает сам videofeed — одним AbortController,
     и старых подключений не остаётся. */
  function restartVideoFeed() {
    if (global.PV.videofeed) {
      const feed = document.getElementById("videoFeed");
      global.PV.videofeed.open();
      if (feed) global.PV.videofeed.attach(feed);
    } else {
      const feed = document.getElementById("videoFeed");
      if (feed && feed.tagName === "IMG") {
        const base = (feed.dataset.streamSrc || streamUrl()).split("?")[0];
        const token = global.PV.api.authToken();
        feed.src =
          base +
          "?t=" +
          Date.now() +
          (token ? "&pv_auth=" + encodeURIComponent(token) : "");
      }
    }
    startFeedWatchdog();
  }

  /* ------------------------------------------------------------
     Контроль потока.

     MJPEG не сообщает браузеру ни об ошибке, ни о том, что поток
     перестал идти: оборванный поток молча оставляет <img> с последним
     кадром, а если кадр не пришёл ни разу — с чёрным прямоугольником.
     Для оператора это неотличимо от «камера молчит».

     Поэтому отдельно следим за признаками жизни: поток не начался
     вовремя или оборвался — показываем причину прямо на видео.
     ------------------------------------------------------------ */
  let feedWatchdog = null;

  const FEED_GRACE_MS = 8000;   // сколько ждём первый кадр
  const FEED_RETRY_MS = 5000;   // пауза перед повторной попыткой

  function feedMessage() {
    const v = state.video;
    if (v.error) return v.error;
    if (!global.PV.api.ready()) {
      return "Нет связи с сервером — видео недоступно.";
    }
    return "Видеопоток не отвечает. Камера не отдаёт кадры.";
  }

  function showFeedMessage(text) {
    const box = document.getElementById("videoEmpty");
    if (!box) return;
    box.hidden = false;
    box.textContent = text;
  }

  function hideFeedMessage() {
    const box = document.getElementById("videoEmpty");
    if (box) box.hidden = true;
  }

  /* Быстрое обновление «Текущего состояния».

   Проблема, которую это решает. Панель перерисовывалась раз в 5 секунд
   вместе со всем остальным экраном. Человек уходит из зоны на 4–8
   секунд — за такой промежуток панель успевала обновиться один раз, и
   чаще всего не в тот момент: оператор видел «рабочий на месте», хотя
   человек уже ушёл, и считал, что состояние не меняется.

   Здесь значения в четырёх ячейках обновляются на месте, раз в
   полторы секунды, отдельным лёгким запросом /api/video/state. Он не
   ходит в базу и отдаёт то, что конвейер знает прямо сейчас. */
  const NOW_TICK_MS = 1500;
  let nowTimer = null;
  let nowTickBusy = false;

  function stopNowTicker() {
    if (nowTimer) {
      clearInterval(nowTimer);
      nowTimer = null;
    }
  }

  function startNowTicker() {
    stopNowTicker();
    nowTimer = setInterval(tickNowPanel, NOW_TICK_MS);
  }

  async function tickNowPanel() {
    if (nowTickBusy) return;
    /* Панели нет — мы на другой вкладке. Обновлять нечего, а запросы
       лишние. */
    const panel = document.querySelector(".now-panel");
    if (!panel || panel.classList.contains("is-idle")) return;

    nowTickBusy = true;
    try {
      const s = await global.PV.api.get("/api/video/state");
      if (!s) return;
      setCell(0,
        '<span class="dot dot-' + (s.line_status === "WORKING" ? "green"
          : s.line_status === "STOPPED" ? "red" : "gray") + '"></span>'
        + (s.line_status || "UNKNOWN"),
        s.line_status === "WORKING" ? "is-working"
          : s.line_status === "STOPPED" ? "is-stopped" : "is-unknown");
      setCell(1,
        (s.person_present ? icon("check") : icon("close")) + " "
        + (s.person_present ? "DETECTED" : "NOT DETECTED"),
        s.person_present ? "is-ok" : "is-bad");
      if (typeof s.downtime_seconds === "number") {
        setCell(2, U.fmtHM(s.downtime_seconds),
                s.downtime_seconds > 0 ? "is-bad" : "");
      }
    } catch (e) {
      /* нет связи — панель обновится при следующем автообновлении */
    } finally {
      nowTickBusy = false;
    }
  }

  /* Обновляет ячейку на месте. Разметка та же, что при полной
     отрисовке, иначе значки и точки выглядели бы иначе в зависимости
     от того, как обновилась панель. */
  function setCell(index, html, cls) {
    const cells = document.querySelectorAll(".now-cell");
    const cell = cells[index];
    if (!cell) return;
    const value = cell.querySelector(".now-v");
    if (!value) return;
    const key = cls + "|" + html;
    if (value.dataset.tick === key) return;
    value.dataset.tick = key;
    value.className = "now-v" + (cls ? " " + cls : "");
    value.innerHTML = html;
  }

  function startFeedWatchdog() {
    if (feedWatchdog) clearTimeout(feedWatchdog);
    feedWatchdog = setTimeout(function () {
      /* Кадры пришли — поток живой, вмешиваться нечего.
         Считает их videofeed: у <img> больше нет naturalWidth,
         да и сам <img> больше не используется. */
      const stats =
        global.PV.videofeed && global.PV.videofeed.stats
          ? global.PV.videofeed.stats()
          : null;
      if (stats && stats.frames > 0) {
        hideFeedMessage();
        return;
      }
      showFeedMessage(feedMessage());
      restartVideoFeed();
    }, FEED_GRACE_MS);
  }

  /* Вызывается из onerror у <img>: адрес потока недоступен целиком. */
  function markFeedBroken() {
    showFeedMessage(
      "Видеопоток недоступен по адресу " +
        streamUrl() +
        ". Проверьте, что backend запущен."
    );
  }

  async function refreshVideoState() {
    /* Состояние конвейера — источник ПРАВДЫ о том, что с камерой.
       Запрашиваем не каждый кадр, а раз в цикл автообновления.

       Через api.get(), а не голый fetch: /api/video/state закрыт
       паролем, и без заголовка приходил 401, который молча
       проглатывался. Из-за этого надпись «нет сигнала» на экране
       была старой: интерфейс так и не узнавал, что камера ожила. */
    try {
      const s = await global.PV.api.get("/api/video/state");
      if (!s) return;
      setVideoError(s.error || "");
      state.video.lastState = s;
    } catch (e) {
      /* Сервер не ответил — про это уже сообщает data.reload() */
    }
  }

  /* Причина, по которой видео может не идти, хранится здесь: она
     приходит с backend'а вместе с состоянием конвейера и объясняет
     не только «камера молчит», но и «кадры идут, но чёрные» —
     на macOS это запрет доступа к камере. */
  function setVideoError(text) {
    state.video.error = text || "";
    if (text) showFeedMessage(text);
    else hideFeedMessage();
  }

  function renderSettings() {
    const s = state.settings;
    const v = state.video;
    const sw = (key, title, desc) => `
      <div class="switch-row">
        <div class="txt">${title}<small>${desc}</small></div>
        <button class="switch ${s[key] ? "is-on" : ""}" data-toggle="${key}" aria-pressed="${!!s[key]}"></button>
      </div>`;

    return `
    <div class="page-title">${icon("bars")}<h1>Настройки</h1></div>

    <div class="set-grid">
      <div class="card set-card">
        <div class="set-title">Расчёт ущерба</div>
        <div class="set-desc">Влияет на все суммы в отчётах и на карточках</div>
        <div class="field">
          <label for="s_cost">Стоимость минуты простоя, ₽</label>
          <input class="input" id="s_cost" type="number" min="0" step="10" value="${s.cost_per_minute}">
          <div class="hint">Формула: длительность простоя (мин) × стоимость минуты</div>
        </div>
        <div class="field">
          <label for="s_thr">Порог фиксации простоя, сек</label>
          <input class="input" id="s_thr" type="number" min="1" step="1" value="${s.downtime_threshold_seconds}">
          <div class="hint">Короче этого интервала отсутствие не попадёт в отчёт</div>
        </div>
      </div>

      <div class="card set-card">
        <div class="set-title">Режим камеры</div>
        <div class="set-desc">Откуда система берёт видео и как настраивается простой</div>

        <div class="field">
          <label for="s_mode">Режим</label>
          <select class="input" id="s_mode">
            <option value="synthetic" ${v.mode === "synthetic" ? "selected" : ""}>Демо — синтетическая сцена</option>
            <option value="demo" ${v.mode === "demo" ? "selected" : ""}>Демо — ролики сценариев</option>
            <option value="browser" ${v.mode === "browser" ? "selected" : ""}>Веб-камера — из браузера (считает сервер)</option>
            <option value="rtsp" ${v.mode === "rtsp" ? "selected" : ""}>Сетевая камера</option>
          </select>
          <div class="hint" id="modeHint">${esc(modeHint(v.mode))}</div>
        </div>

        <div class="field ${v.mode === "browser" ? "" : "is-hidden"}" id="browserCamField">
          <label>Передача с вашей камеры</label>
          <video id="browserPreview" class="browser-preview" autoplay muted playsinline></video>
          <div class="hint" id="browserCamHint">${esc(browserCamHint())}</div>
          <div class="set-actions" style="padding-top:10px">
            <button class="btn-primary" id="browserCamToggle">${
              isBrowserCamActive() ? "Остановить передачу" : "Передавать мою камеру"
            }</button>
          </div>
        </div>

        <div class="field ${v.mode === "webcam" ? "" : "is-hidden"}" id="webcamField">
          <label for="s_webcam">Камера</label>
          <select class="input" id="s_webcam" ${v.webcams.length ? "" : "disabled"}>
            ${webcamOptions(v)}
          </select>
          <div class="hint" id="webcamHint">${esc(webcamHint(v))}</div>
        </div>

        <div class="field">
          <label>Порог фиксации простоя</label>
          <div class="hint" style="margin:0">
            В режиме веб-камеры — ${esc(v.threshold)} сек: если рабочий исчез из кадра
            дольше этого времени, простой попадает в отчёт.
            В демо-режиме порог другой: ролик короткий.
          </div>
        </div>

        <div class="set-actions" style="padding:14px 0 0">
          <button class="btn-primary" id="applyMode">${icon("check")}Применить</button>
          <span class="note" id="modeNote"></span>
        </div>
      </div>

      <div class="card set-card">
        <div class="set-title">Уведомления</div>
        <div class="set-desc">Куда сообщать о простоях</div>
        ${sw("notify_telegram", "Telegram", "Сообщение при каждом зафиксированном простое")}
        ${sw("notify_email", "Email", "Сводка в конце смены")}
        ${sw("auto_refresh", "Автообновление", "Обновлять данные каждые 5 секунд")}
        ${sw("show_loss", "Показывать ущерб", "Скрыть все денежные суммы в интерфейсе")}
      </div>

      <div class="card set-card">
        <div class="set-title">Хранение данных</div>
        <div class="set-desc">Сколько хранить историю событий</div>
        <div class="field">
          <label for="s_ret">Срок хранения, дней</label>
          <input class="input" id="s_ret" type="number" min="1" max="3650" step="1" value="${s.retention_days}">
        </div>
        <div class="field">
          <label for="s_tmin">Минимальный простой для отчёта, мин</label>
          <input class="input" id="s_tmin" type="number" min="0" step="1" value="${s.threshold_minutes}">
        </div>
      </div>

      <div class="card set-card full">
        <div class="set-title">Система</div>
        <div class="set-desc">Состояние компонентов</div>
        <div class="line-rows">
          <div class="line-row"><div>Видеопоток</div><div class="num"><span class="tag">Подключён</span></div></div>
          <div class="line-row"><div>Детектор персонала</div><div class="num"><span class="tag">Активен</span></div></div>
          <div class="line-row"><div>Хранилище событий</div><div class="num"><span class="tag">Доступно</span></div></div>
        </div>
      </div>

      <div class="card set-card full" style="padding:0">
        <div class="set-actions">
          <span class="note" id="setNote">Изменения применяются сразу после сохранения</span>
          <button class="btn-sec" id="setReset">Сбросить</button>
          <button class="btn-primary" id="setSave">${icon("save")}Сохранить</button>
        </div>
      </div>
    </div>`;
  }

  /* ============================================================
     Экспорт CSV
     ============================================================ */
  function downloadCSV(name, rows) {
    const csv = rows
      .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(";"))
      .join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function reportRows() {
    const y = state.day.getFullYear(), m = state.day.getMonth();
    let evs = [];
    if (state.reportPeriod === "day") {
      evs = data.finishedForDay(state.day);
    } else if (state.reportPeriod === "week") {
      const start = U.addDays(state.day, -state.day.getDay());
      for (let i = 0; i < 7; i++) evs = evs.concat(data.finishedForDay(U.addDays(start, i)));
    } else {
      evs = data.finishedForMonth(y, m);
    }
    return [["Дата", "Линия", "Сотрудник", "Начало", "Конец", "Длительность, мин", "Ущерб, ₽"]].concat(
      evs.map((e) => [e.day, e.line, e.employeeFull, e.start, e.end,
      Math.round(e.durationSec / 60), Math.round(e.loss)])
    );
  }

  /* ============================================================
     Экспорт раздела
     ============================================================ */
  global.PV.sections = {
    state,
    saveSettings,
    syncCostPerMinute,
    applyTheme,
    closeDateMenus,
    /* Настройки режима камеры нужны app.js при старте:
       до загрузки они с сервера неизвестны. */
    loadVideoConfig,
    refreshVideoState,
    loadScenarios,
    selectScenario,
    restartVideoFeed,
    markFeedBroken,
    setVideoError,

    /* Без аргумента перерисовываем текущий раздел.
       Раньше view становился undefined, state.section обнулялся,
       и срабатывал default — экран уезжал на «Камеры». */
    render(view) {
      const el = document.getElementById("content");
      const target = view || state.section || state.tab;
      // Автообновление Dashboard меняет разметку, но существующий MJPEG-
      // img переносим в новый DOM, чтобы не обрывать видеосоединение.
      const liveFeed = target === "cameras" ? el.querySelector("#videoFeed") : null;
      state.section = target;
      // Вкладку мониторинга запоминаем отдельно
      if (target === "lines" || target === "cameras" || target === "overview") {
        state.tab = target;
      }
      switch (target) {
        case "cameras": el.innerHTML = renderCameras(); break;
        case "lines": el.innerHTML = renderLines(); break;
        case "overview": el.innerHTML = renderOverview(); break;
        case "employees": el.innerHTML = renderEmployees(); break;
        case "reports": el.innerHTML = renderReports(); break;
        case "settings": el.innerHTML = renderSettings(); break;
        default: el.innerHTML = renderCameras();
      }
      if (target === "cameras") {
        const feed = el.querySelector("#videoFeed");
        if (liveFeed && feed) {
          /* Старый canvas переносим в новую разметку — так поток не
             рвётся на каждом автообновлении. */
          feed.replaceWith(liveFeed);
          if (global.PV.videofeed) global.PV.videofeed.attach(liveFeed);
        } else if (feed) {
          /* Canvas только что создан заново: подключаем к нему поток.
             attach() сам различает «тот же элемент» и «новый», поэтому
             лишних переподключений при автообновлении не выходит. */
          if (global.PV.videofeed) global.PV.videofeed.attach(feed);
        }
        startFeedWatchdog();
      }
      // Не сбрасываем прокрутку при перерисовке того же раздела (смена даты)
      if (view) el.scrollTop = 0;

      /* Панель «Текущее состояние» есть только на вкладке «Камеры».
         На ней включаем быстрое обновление ячеек, на остальных —
         выключаем, чтобы не слать запросы в пустоту. */
      if (target === "cameras") startNowTicker();
      else stopNowTicker();
    },

    /* Возврат в «Мониторинг» показывает последнюю вкладку мониторинга,
       а не раздел, из которого ушли. */
    renderSection(section) {
      return this.render(section === "monitoring" ? state.tab : section);
    },

    // обработчики, навешиваемые один раз (делегирование)
    bind(root) {
      /* --- переключение линии в камере --- */
      on(root, "click", "#lineSwitch button", (e, b) => {
        state.camLine = +b.dataset.line;
        this.render("cameras");
      });

      /* --- выбор демо-сценария отрезком на таймлайне --- */
      on(root, "click", "#scenarioTrack .scenario-seg", (e, b) => {
        selectScenario(b.dataset.scenario, b);
      });

      /* --- play/pause --- */
      on(root, "click", "#playBtn", () => {
        /* Кнопка плеера больше не нужна: картинка идёт живьём с
           сервера, и «пауза» означала бы просто остановить
           обновление — это делается переключением режима. */
        state.playing = !state.playing;
        this.render("cameras");
        toast(state.playing ? "Воспроизведение запущено" : "Воспроизведение приостановлено");
      });

      /* --- тултип активности --- */
      const track = document.getElementById("actTrack");
      if (track) {
        const tip = document.getElementById("actTip");
        const show = (seg) => {
          if (!tip) return;
          if (seg.classList.contains("present")) {
            tip.classList.add("is-muted");
            tip.innerHTML = icon("check") + "<span>Персонал на месте</span>";
          } else {
            tip.classList.remove("is-muted");
            tip.innerHTML =
              icon("user") +
              `<span>${seg.dataset.start} – ${seg.dataset.end} · ${seg.dataset.who}</span>`;
          }
          const r = seg.getBoundingClientRect();
          const tr = track.getBoundingClientRect();
          tip.style.left = U.clamp(r.left - tr.left + r.width / 2, 70, tr.width - 70) + "px";
        };
        track.addEventListener("mousemove", (e) => {
          const seg = e.target.closest(".activity-seg.absent, .activity-seg.present");
          if (seg && tip) { tip.style.display = "flex"; show(seg); }
        });
        track.addEventListener("mouseleave", () => { if (tip) tip.style.display = "none"; });
      }

      /* --- кнопка «Сегодня» --- */
      on(root, "click", "[data-day='today']", () => {
        state.day = new Date();
        this.render();
      });

      /* --- выбор даты: день / месяц / год --- */
      // Открытие списка
      on(root, "click", ".dd-btn", (e, b) => {
        e.stopPropagation();
        const menu = b.parentElement.querySelector(".dd-menu");
        const wasOpen = !menu.hasAttribute("hidden");
        closeDateMenus();
        if (wasOpen) return; // повторный клик закрывает
        menu.removeAttribute("hidden");
        b.setAttribute("aria-expanded", "true");
      });

      // Выбор числа или месяца (год живёт в верхней панели, вне #content).
      // Селектор по data-pick, а не по классу: числа (.dd-day) и
      // месяцы (.dd-item) — разные элементы, но выбираются одинаково.
      on(root, "click", "[data-pick]", (e, b) => {
        if (b.disabled) return;
        const kind = b.dataset.pick;
        const v = +b.dataset.v;
        const d = state.day;
        const y = d.getFullYear();
        const m = d.getMonth();

        // День не может выйти за пределы месяца (31 → 30)
        const clampDay = (yy, mm, dd) => Math.min(dd, daysInMonth(yy, mm));

        if (kind === "day") {
          state.day = new Date(y, m, clampDay(y, m, v));
        } else if (kind === "month") {
          state.day = new Date(y, v, clampDay(y, v, d.getDate()));
        } else {
          return;
        }
        closeDateMenus();
        this.render();
      });

      /* --- событие на таймлайне --- */
      on(root, "click", ".tl-col .ev", (e, b) => {
        toast(`${b.dataset.who} · линия ${b.dataset.line} · ${b.dataset.start}–${b.dataset.end || "…"}`);
      });

      /* --- сотрудники: поиск/фильтр/страницы --- */
      on(root, "input", "#empSearch", (e, i) => {
        state.employeesQuery = i.value;
        state.employeesPage = 1;
        const pos = i.selectionStart;
        this.render("employees");
        const ni = document.getElementById("empSearch");
        if (ni) { ni.focus(); ni.setSelectionRange(pos, pos); }
      });
      on(root, "change", "#empLine", (e, s) => {
        state.employeesLine = s.value;
        state.employeesPage = 1;
        this.render("employees");
      });
      on(root, "click", "[data-page]", (e, b) => {
        state.employeesPage += +b.dataset.page;
        this.render("employees");
      });
      on(root, "click", "#empExport", () => {
        const rows = [["ID", "Сотрудник", "Должность", "Линия", "Смена", "Статус", "Отсутствие, мин", "Ущерб, ₽"]].concat(
          data.employees.map((e) => [e.id, e.name, e.position, e.line, e.shift,
          e.status === "work" ? "На месте" : e.status === "absent" ? "Отсутствует" : "Отпуск",
          e.absentMin, Math.round(e.lost)])
        );
        downloadCSV("promvizor-employees.csv", rows);
        toast("CSV выгружен");
      });

      /* --- отчёты --- */
      on(root, "click", "#repPeriod button", (e, b) => {
        state.reportPeriod = b.dataset.p;
        this.render("reports");
      });
      on(root, "click", "#repExport", () => {
        downloadCSV(`promvizor-report-${U.dayISO(state.day)}.csv`, reportRows());
        toast("Отчёт выгружен в CSV");
      });
      on(root, "click", "#repPrint", () => window.print());

      /* --- настройки --- */
      on(root, "click", "[data-toggle]", (e, b) => {
        const k = b.dataset.toggle;
        state.settings[k] = !state.settings[k];
        saveSettings();
        this.render("settings");
      });

      /* --- режим камеры --- */
      on(root, "click", "#applyMode", () => applyVideoMode());
      on(root, "click", "#browserCamToggle", () => toggleBrowserCam());

      /* Смена режима перерисовывает блок: поле выбора камеры нужно
         только в режиме веб-камеры, а подсказка зависит от режима. */
      on(root, "change", "#s_mode", (e) => {
        state.video.mode = e.target.value;
        this.render("settings");
      });

      on(root, "click", "#setSave", () => {
        const g = (id) => document.getElementById(id);
        const s = state.settings;
        if (g("s_cost")) s.cost_per_minute = +g("s_cost").value || 0;
        if (g("s_thr")) s.downtime_threshold_seconds = +g("s_thr").value || 1;
        if (g("s_src")) s.default_source = g("s_src").value;
        if (g("s_rtsp")) s.rtsp_url = g("s_rtsp").value.trim();
        if (g("s_ret")) s.retention_days = +g("s_ret").value || 1;
        if (g("s_tmin")) s.threshold_minutes = +g("s_tmin").value || 0;
        const ok = saveSettings();
        toast(ok ? "Настройки сохранены" : "Не удалось сохранить в браузере", ok ? "ok" : "err");
        this.render();
      });
      on(root, "click", "#setReset", () => {
        localStorage.removeItem("pv_settings");
        state.settings = loadSettings();
        // Сброс всегда возвращает ту же светлую тему, что и загрузка.
        applyTheme("light");
        syncCostPerMinute();
        toast("Настройки сброшены к значениям по умолчанию");
        this.render();
      });
    },
  };
})(window);
