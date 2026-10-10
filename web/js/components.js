/* ============================================================
   ПромВизор — компоненты и общие рендер-функции
   ============================================================ */
(function (global) {
  "use strict";

  const U = global.PV.util;

  /* ---------- иконки (inline SVG) ---------- */
  const ICONS = {
    bars: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
    calendar: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>',
    user: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3.2 2"/></svg>',
    chart: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
    loss: '<svg viewBox="0 0 24 24"><path d="M12 3v18M7 7h7a3.5 3.5 0 0 1 0 7H9.5a3.5 3.5 0 0 0 0 7H17"/></svg>',
    videoOff: '<svg viewBox="0 0 24 24"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h8A2.5 2.5 0 0 1 16 7.5v9a2.5 2.5 0 0 1-2.5 2.5h-8A2.5 2.5 0 0 1 3 16.5zM16 11l5-3v8l-5-3"/><path d="m3 3 18 18"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m4 12.5 5 5L20 6.5"/></svg>',
    alert: '<svg viewBox="0 0 24 24"><path d="M12 3.5 22 20H2z"/><path d="M12 10v4M12 17h.01"/></svg>',
    left: '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>',
    right: '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 3v12M7.5 10.5 12 15l4.5-4.5M4 20h16"/></svg>',
    printer: '<svg viewBox="0 0 24 24"><path d="M7 9V3h10v6M7 19H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><rect x="7" y="15" width="10" height="6" rx="1"/></svg>',
    save: '<svg viewBox="0 0 24 24"><path d="M4 4h12l4 4v12H4z"/><path d="M8 4v6h8M8 20v-6h8v6"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>',
    play: '<svg viewBox="0 0 24 24"><path d="M7 4.5 19 12 7 19.5z"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M9 4.5v15M15 4.5v15"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/></svg>',
    cam: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="13" rx="2.5"/><circle cx="12" cy="12.5" r="3.6"/><path d="M8 6l1.2-2.5h5.6L16 6"/></svg>',
    doc: '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M8.5 8h7M8.5 12h7M8.5 16h4"/></svg>',
    clockSmall: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3.2 2"/></svg>',
    /* close — для статуса PERSON: рабочего нет */
    close: '<svg viewBox="0 0 24 24"><path d="M5.5 5.5l13 13M18.5 5.5l-13 13"/></svg>',
  };

  function icon(name, cls) {
    return (ICONS[name] || "").replace("<svg", '<svg class="' + (cls || "") + '"');
  }

  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const initials = (name) =>
    name.trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  /* ---------- тост ---------- */
  function toast(msg, kind) {
    const box = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = "toast " + (kind || "ok");
    el.innerHTML = icon(kind === "err" ? "alert" : "check") + "<span>" + esc(msg) + "</span>";
    box.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .25s, transform .25s";
      el.style.opacity = "0";
      el.style.transform = "translateY(8px)";
      setTimeout(() => el.remove(), 260);
    }, 2600);
  }

  /* ---------- переиспользование событий ---------- */
  // Делегирование: один слушатель на контейнер контента
  function on(root, evt, sel, fn) {
    root.addEventListener(evt, (e) => {
      const t = e.target.closest(sel);
      if (t && root.contains(t)) fn(e, t);
    });
  }

  /* ---------- кольцевая шкала прогресса ---------- */
  function progressBar(pct, cls) {
    return '<div class="progress"><i style="width:' + U.clamp(pct, 0, 100) + '%"></i></div>';
  }

  /* ---------- badge статуса ---------- */
  function statusTag(status) {
    if (status === "work") return '<span class="tag">На месте</span>';
    if (status === "absent") return '<span class="tag bad">Отсутствует</span>';
    return '<span class="tag idle">Отпуск</span>';
  }

  global.PV.c = {
    icon, esc, initials, toast, on, progressBar, statusTag,
  };
})(window);