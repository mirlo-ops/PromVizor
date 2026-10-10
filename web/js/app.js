/* ============================================================
   ПромВизор — инициализация приложения
   ============================================================ */
(function (global) {
  "use strict";

  const { data, util: U } = global.PV;
  const api = global.PV.api;
  const { icon, toast } = global.PV.c;
  const sections = global.PV.sections;

  const $ = (id) => document.getElementById(id);

  /* ---------- состояние раздела/вкладки ---------- */
  let currentSection = "monitoring";
  let currentTab = "cameras";

  function setSection(section) {
    currentSection = section;
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.section === section)
    );
    $("tabs").classList.toggle("is-hidden", section !== "monitoring");
    sections.renderSection(section);
    // Активна та вкладка, чей контент реально показан
    if (section === "monitoring") currentTab = sections.state.tab;
    document.querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.tab === currentTab)
    );
  }

  /* Вкладка всегда принадлежит разделу «Мониторинг».
     Клик по вкладке из другого раздела обязан туда вернуть,
     иначе навигация остаётся скрытой, а контент — чужим. */
  function setTab(tab) {
    if (currentSection !== "monitoring") {
      // Сначала фиксируем вкладку, иначе setSection отрендерит предыдущую
      sections.state.tab = tab;
      setSection("monitoring");
      return;
    }
    currentTab = tab;
    document.querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.tab === tab)
    );
    sections.render(tab);
  }

  /* ---------- верхняя панель ---------- */
  // Число и месяц выбираются в шапке «Линий», здесь — год, счётчик и часы
  function renderTopbar() {
    const d = sections.state.day;
    $("yearLabel").textContent = d.getFullYear();
    $("employeesCount").textContent = data.employees.length;

    const menu = $("yearSelect").querySelector(".dd-menu");
    const nowY = new Date().getFullYear();
    menu.innerHTML = Array.from({ length: 6 }, (_, i) => nowY - i)
      .map(
        (y) =>
          `<button class="dd-item${y === d.getFullYear() ? " is-active" : ""}"
             data-pick="year" data-v="${y}">${y}</button>`
      )
      .join("");
  }

  /* ---- выбор года в верхней панели ---- */
  function initYearSelect() {
    const wrap = $("yearSelect");
    const btn = wrap.querySelector(".dd-btn");
    const menu = wrap.querySelector(".dd-menu");

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const wasOpen = !menu.hasAttribute("hidden");
      sections.closeDateMenus();
      if (wasOpen) return;
      renderTopbar(); // список всегда актуален
      menu.removeAttribute("hidden");
      btn.setAttribute("aria-expanded", "true");
    });

    menu.addEventListener("click", (e) => {
      const b = e.target.closest(".dd-item");
      if (!b || b.disabled) return;
      const y = +b.dataset.v;
      const d = sections.state.day;
      // год меняем так, чтобы день не вылез за пределы месяца (29 февраля → 28)
      const dim = new Date(y, d.getMonth() + 1, 0).getDate();
      sections.state.day = new Date(y, d.getMonth(), Math.min(d.getDate(), dim));
      sections.closeDateMenus();
      renderTopbar();
      sections.renderSection(currentSection);
    });
  }

  /* ---------- часы ---------- */
  function tick() {
    const d = new Date();
    $("clockTime").textContent = `${U.pad(d.getHours())}:${U.pad(d.getMinutes())}`;
    // обновление секунд в оверлее видео
    const chip = document.querySelector(".ov-time");
    if (chip) {
      chip.textContent =
        `${U.pad(d.getDate())}.${U.pad(d.getMonth() + 1)}.${d.getFullYear()} ` +
        `${U.pad(d.getHours())}:${U.pad(d.getMinutes())}:${U.pad(d.getSeconds())}`;
    }
  }

  /* ---------- логотип: возврат на главный экран (Линии) ---------- */
  function initBrand() {
    const go = () => {
      setSection("monitoring");
      setTab("lines");
      toast("Главный экран — Линии");
    };
    $("brandBtn").addEventListener("click", go);
    $("brandBtn").addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        go();
      }
    });
  }

  /* ---------- статус системы ---------- */
  /* Показывает не выдуманное «система работает», а реальное
     состояние связи с backend'ом: оператор должен видеть, что
     данные не приходят, а не получать нули как показание. */
  function updateSystemPill() {
    const pill = $("systemPill");
    /* Связь определяется по последнему запросу, а не по наличию
       данных: при обрыве на экране остаются последние показания,
       и называть их «система работает» было бы враньём. */
    const live = api.ready() && data.isLive();
    const dot = $("systemDot");
    const text = $("systemText");

    if (live) {
      pill.classList.remove("is-off");
      dot.className = "dot dot-green";
      text.textContent = "Система работает";
    } else {
      pill.classList.add("is-off");
      dot.className = "dot dot-amber";
      const reason = api.lastError();
      text.textContent = reason ? "Нет связи с сервером" : "Нет данных";
      pill.title = reason ? "Backend недоступен: " + reason : "Данные с сервера не получены";
    }
  }

  $("systemPill").addEventListener("click", async () => {
    await data.reload(sections.state.day);
    updateSystemPill();
    renderTopbar();
    sections.renderSection(currentSection);
    /* Проверяем связь, а не наличие данных: при обрыве прежние
       показания остаются на экране, и по ним обрыв не виден. */
    if (api.ready()) {
      toast("Связь с backend установлена");
    } else {
      toast("Backend недоступен: " + (api.lastError() || "нет данных"), "err");
    }
  });

  /* ---------- горячие клавиши ---------- */
  function initKeys() {
    document.addEventListener("keydown", (e) => {
      // Целью может быть сам document — у него нет matches()
      const el = e.target;
      if (el && typeof el.matches === "function" &&
          el.matches("input, select, textarea")) return;
      if (e.key === "1") setSection("monitoring"), setTab("lines");
      if (e.key === "2") setSection("monitoring"), setTab("cameras");
      if (e.key === "3") setSection("monitoring"), setTab("overview");
      if (e.key === "4") setSection("employees");
      if (e.key === "5") setSection("reports");
      if (e.key === "6") setSection("settings");
      if (e.key === "ArrowLeft" && currentSection === "monitoring" && currentTab === "lines") {
        sections.state.day = U.addDays(sections.state.day, -1);
        sections.render("lines");
      }
      if (e.key === "ArrowRight" && currentSection === "monitoring" && currentTab === "lines") {
        sections.state.day = U.addDays(sections.state.day, 1);
        sections.render("lines");
      }
    });
  }

  /* ---------- автообновление ---------- */
  let refreshTimer = null;
  function syncAutoRefresh() {
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
    if (sections.state.settings.auto_refresh) {
      refreshTimer = setInterval(async () => {
        /* Сначала обновляем данные с сервера, потом перерисовываем.
           Обратный порядок показал бы прошлые цифры ещё пять секунд,
           а на экране мониторинга это выглядит как зависание.
           reload, а не load: иначе день уже в кэше и запрос к
           серверу не уйдёт — состояние замрёт навсегда. */
        await data.reload(sections.state.day);
        updateSystemPill();
        if (currentSection === "monitoring") sections.render(sections.state.tab);
        tick();
      }, 5000);
    }
  }

  /* ---------- старт ---------- */
  function boot() {
    // закрытие выпадающих списков по клику вне них и по Escape
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".dd")) sections.closeDateMenus();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") sections.closeDateMenus();
    });

    document.querySelectorAll(".nav-item").forEach((b) =>
      b.addEventListener("click", () => setSection(b.dataset.section))
    );
    document.querySelectorAll(".tab").forEach((b) =>
      b.addEventListener("click", () => setTab(b.dataset.tab))
    );

    // Тему применяем первой — до любой отрисовки
    sections.applyTheme(sections.state.settings.theme);
    sections.syncCostPerMinute();

    renderTopbar();
    initYearSelect();
    initBrand();
    initKeys();

    sections.bind(document.getElementById("content"));
    setSection("monitoring");
    setTab(currentTab);

    /* Первая отрисовка ждёт данных с сервера.
       Иначе на экране на секунду мелькнут моковые числа, а потом
       сменятся настоящими — оператор успел бы увидеть чужие.
       Заодно ждём настройки режима камеры: иначе настройки показали
       бы «камер не найдено», а через секунду подменились бы списком. */
    Promise.all([
      data.load(sections.state.day),
      sections.loadVideoConfig(),
    ]).then(() => {
      /* Стоимость минуты приходит с backend'а: показываем ту,
         что он считает, а не значение из локальных настроек. */
      const cfg = data.config();
      if (cfg && cfg.cost_per_minute) {
        sections.state.settings.cost_per_minute = cfg.cost_per_minute;
        sections.syncCostPerMinute();
      }
      updateSystemPill();
      renderTopbar();
      sections.renderSection(currentSection);
      /* Лог печатается здесь, а не в конце boot(): к этому
         моменту источник данных уже известен. Иначе в консоли
         всегда был бы «mock» — загрузка ещё не завершилась. */
      console.log(
        "ПромВизор: данные получены. Источник:", data.source(),
        "Режим камеры:", sections.state.video.mode,
        data.isLive() ? "" : "причина: " + (api.lastError() || "нет данных")
      );
    });

    tick();
    setInterval(tick, 1000);
    syncAutoRefresh();

    // если настройки поменяли — пересоздать таймер
    document.addEventListener("pv:settings", syncAutoRefresh);
    const origRender = sections.render.bind(sections);
    sections.render = function (t) {
      origRender(t);
      syncAutoRefresh();
    };

    console.log(
      "ПромВизор инициализирован. Раздел:", currentSection, "Вкладка:", currentTab
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(window);