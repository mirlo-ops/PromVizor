/* ============================================================
   ПромВизор — вход на сайт
   ------------------------------------------------------------
   Проверяет, есть ли в ЭТОЙ вкладке действующий вход, и если нет —
   показывает форму.

   Почему проверка здесь, а не в nginx. Базовая аутентификация nginx
   запоминается браузером на всю сессию: вошли один раз — и пароль
   больше не спросят ни в новой вкладке, ни позже. Для стенда, где
   пароль раздаётся каждому подошедшему, это не годится.

   Почему отметка в sessionStorage. Это единственное хранилище,
   отдельное у каждой вкладки:
     * новая вкладка  → пароль спрашивается заново;
     * обновление (F5) → пароль не спрашивается;
     * закрыли вкладку → отметка исчезла.

   Данные при этом защищены на сервере: без пароля не отдаётся ни одна
   цифра статистики и ни одного кадра видео, любой запрос получает 401.
   ============================================================ */
(function (global) {
  "use strict";

  const KEY = "pv_auth";

  function token() {
    try {
      return sessionStorage.getItem(KEY) || "";
    } catch (e) {
      /* приватный режим браузера — отметки не будет, спросим пароль */
      return "";
    }
  }

  function setToken(value) {
    try {
      sessionStorage.setItem(KEY, value || "");
    } catch (e) {
      /* записать не дали — войти не получится, форма покажет ошибку */
    }
  }

  function clearToken() {
    try {
      sessionStorage.removeItem(KEY);
    } catch (e) {
      /* хранилище недоступно — ничего не сделать */
    }
  }

  /* ---------------------------------------------------------- *
     Страница входа.
     Всё в одном файле и без внешних ссылок: файлы интерфейса
     открыты и без пароля, но тянуть их до входа незачем, а если
     интерфейс изменится, форма не должна от него зависеть.

     Исключение — картинки: логотип и фон. Они идут из web/assets/
     и потому, что они часть фирменного стиля: рисовать их вручную
     означало бы через месяц получить два разных логотипа.
     ---------------------------------------------------------- */

  /* Значки для полей и кнопок: человек, замок, вход, вопрос.
     Рисуются прямо здесь, инлайном. Две картинки не стоят ещё
     двух запросов, а на демонстрации лишняя задержка заметна. */
  const ICON_USER =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<circle cx="12" cy="8" r="3.6"/>' +
    '<path d="M4.8 20c.9-3.6 3.7-5.6 7.2-5.6s6.3 2 7.2 5.6"/>' +
    "</svg>";

  const ICON_LOCK =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<rect x="5" y="10.5" width="14" height="9.5" rx="2"/>' +
    '<path d="M8.2 10.5V7.8a3.8 3.8 0 0 1 7.6 0v2.7"/>' +
    "</svg>";

  const ICON_IN =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H10"/>' +
    '<path d="M15 8.5 18.5 12 15 15.5"/>' +
    '<path d="M18.5 12H9"/>' +
    "</svg>";

  const ICON_HELP =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="8.6"/>' +
    '<path d="M9.8 9.4a2.3 2.3 0 0 1 4.4.8c0 1.6-2.2 1.9-2.2 3.3"/>' +
    '<circle cx="12" cy="17" r=".9" fill="currentColor" stroke="none"/>' +
    "</svg>";

  const HELP_TEXT =
    "Помощь по входу ещё не подключена: пароль и логин выдаёт " +
    "администратор системы.";
  /* Оформление окна входа.

     Всё по палитре презентации: тёмно-зелёный фон, зелёный акцент,
     светло-зелёный текст. Плюс два отличия от прежней версии,
     которые просили привести к образцу:
       * фон-градиент с бликами вместо плоской заливки;
       * карточка по центру, узкая, с закруглёнными углами.

     Значки в полях входа (человек и замок) нарисованы инлайном:
     тянуть их файлами ради двух картинок незачем, а без них поля
     выглядели бы пустыми и не читались — вводят логин и пароль. */
  const STYLE = `
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }

  body {
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
    background: #081c15;
    color: #d8f3dc;
    font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    /* Градиент с бликами рисуется дважды: картинка не загрузилась —
       страница всё равно остаётся цветной, а не чёрной. */
    background-color: #0e221b;
    background-image: url("assets/login-bg.svg");
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
  }

  /* Фиксированный слой с градиентом поверх: он не зависит от
     прокрутки и остаётся на месте, если окно не влезло по высоте. */
  body::before {
    content: "";
    position: fixed;
    inset: 0;
    background: #081c15;
    z-index: -1;
  }

  .pv-login {
    width: 100%;
    max-width: 380px;
    background: rgba(8, 28, 21, .84);
    border: 1px solid rgba(82, 183, 136, .28);
    border-radius: 14px;
    padding: 34px 30px 26px;
    box-shadow: 0 18px 50px rgba(0, 0, 0, .45);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
  }

  /* Логотип в круге — как на образце, где стоял значок «7».
     Тот же файл, что и в дашборде: два разных логотипа на одном
     экране бросились бы в глаза. */
  .pv-logo {
    width: 84px; height: 84px;
    margin: 0 auto 18px;
    border-radius: 50%;
    background: radial-gradient(circle at 30% 25%, #2a6d4f, #0e221b 72%);
    border: 2px solid rgba(82, 183, 136, .55);
    display: grid; place-items: center;
    box-shadow: 0 8px 26px rgba(0, 0, 0, .4);
  }
  .pv-logo img { width: 52px; height: auto; display: block; }

  .pv-login h1 {
    margin: 0 0 26px;
    font-size: 21px; font-weight: 600;
    text-align: center;
    color: #d8f3dc;
  }

  .pv-field { position: relative; margin-bottom: 14px; }
  .pv-field svg {
    position: absolute;
    left: 12px; top: 50%;
    width: 16px; height: 16px;
    transform: translateY(-50%);
    fill: none;
    stroke: #52b788;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
    pointer-events: none;
  }
  .pv-field input {
    width: 100%;
    padding: 11px 13px 11px 38px;
    border-radius: 8px;
    border: 1px solid rgba(82, 183, 136, .3);
    background: rgba(6, 20, 14, .75);
    color: #d8f3dc;
    font-size: 14px;
  }
  .pv-field input::placeholder { color: rgba(143, 168, 154, .75); }
  .pv-field input:focus {
    outline: none;
    border-color: #52b788;
    box-shadow: 0 0 0 3px rgba(82, 183, 136, .18);
  }

  .pv-login .err {
    display: none;
    margin: 2px 0 14px;
    color: #ff9d9d;
    font-size: 12.5px;
  }
  .pv-login .err.on { display: block; }

  .pv-actions {
    display: flex;
    gap: 10px;
    align-items: center;
    margin-top: 18px;
  }

  .pv-actions button {
    flex: 1;
    padding: 11px 16px;
    border: 0;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: 8px;
    transition: background .15s, opacity .15s;
  }
  .pv-actions button svg {
    width: 15px; height: 15px;
    fill: none; stroke: currentColor; stroke-width: 1.9;
    stroke-linecap: round; stroke-linejoin: round;
  }
  .pv-btn-primary { background: #52b788; color: #081c15; }
  .pv-btn-primary:hover { background: #6ac99b; }
  .pv-btn-primary:disabled { opacity: .6; cursor: default; }

  /* Помощь: кнопка настоящая, нажимается и показывает подсказку,
     ничего не делая. Отключать её через disabled нельзя — тогда
     она выглядит сломанной, а здесь просят именно живую. */
  .pv-btn-help {
    background: rgba(82, 183, 136, .16);
    color: #d8f3dc;
    border: 1px solid rgba(82, 183, 136, .34) !important;
  }
  .pv-btn-help:hover { background: rgba(82, 183, 136, .26); }

  /* Подпись под формой: на демонстрации пароль не должен быть виден
     на экране. Текст не содержит самого пароля. */
  .pv-login .note {
    margin: 20px 0 0;
    padding-top: 16px;
    border-top: 1px solid rgba(82, 183, 136, .16);
    font-size: 11.5px;
    line-height: 1.55;
    color: rgba(143, 168, 154, .9);
  }

  /* На узких экранах карточка становится во всю ширину, иначе
     поля съезжают с краёв телефона. */
  @media (max-width: 420px) {
    .pv-login { padding: 28px 20px 22px; }
  }`;

  function show(errorText) {
    document.documentElement.innerHTML =
      '<head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>ПромВизор — вход</title>' +
      '<link rel="icon" href="data:image/svg+xml,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 100 100\'><text y=\'.9em\' font-size=\'90\'>&#128230;</text></svg>">' +
      '<style>' + STYLE + '</style></head>' +
      '<body>' +
      '<form class="pv-login" id="pvAuthForm">' +
      // Логотип в круге — на этом месте в образце стоял значок «7».
      '<div class="pv-logo"><img src="assets/logo.png" alt="PromVizor"></div>' +
      "<h1>Авторизация</h1>" +
      '<div class="pv-field">' +
      ICON_USER +
      '<input id="pvLogin" placeholder="Логин" autocomplete="username" required>' +
      "</div>" +
      '<div class="pv-field">' +
      ICON_LOCK +
      '<input id="pvPass" type="password" placeholder="Пароль" autocomplete="current-password" required>' +
      "</div>" +
      '<div class="err" id="pvAuthErr">' + escapeHtml(errorText || "") + "</div>" +
      '<div class="pv-actions">' +
      '<button type="submit" class="pv-btn-primary" id="pvAuthBtn">' +
      ICON_IN + "<span>Войти</span></button>" +
      // Помощь: нажимается и показывает подсказку, ничего не делая.
      '<button type="button" class="pv-btn-help" id="pvHelpBtn" ' +
      'aria-label="Помощь по входу">' +
      ICON_HELP + "<span>Помощь</span></button>" +
      "</div>" +
      '<p class="note" id="pvAuthNote">Пароль спрашивается в каждой новой вкладке. ' +
      "При обновлении страницы он не требуется.</p>" +
      "</form></body>";

    const form = document.getElementById("pvAuthForm");
    const login = document.getElementById("pvLogin");
    const pass = document.getElementById("pvPass");
    const err = document.getElementById("pvAuthErr");
    const btn = document.getElementById("pvAuthBtn");

    login.focus();

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      btn.disabled = true;
      btn.disabled = true;
        setBusy(true);
      err.classList.remove("on");

      try {
        const res = await fetch("/api/auth/check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            login: login.value,
            password: pass.value,
          }),
        });

        if (!res.ok) {
          err.textContent = "Неверный логин или пароль";
          err.classList.add("on");
          btn.disabled = false;
          setBusy(false);
          /* Пароль остаётся в поле: иначе при второй попытке
             пришлось бы вводить всё заново, и это выглядело бы как
             «форма не работает». */
          pass.select();
          return;
        }

        const data = await res.json();
        setToken(data.token);
        /* Пароль вычищаем из памяти страницы: после входа он больше
           не нужен, а держать его в DOM неправильно. */
        pass.value = "";
        location.reload();
      } catch (e) {
        err.textContent = "Сервер недоступен";
        err.classList.add("on");
        btn.disabled = false;
        setBusy(false);
      }
    });

    /* Помощь: кнопка живая (кликается, реагирует), но ничего не
       делает — подсказка появляется в подписи под формой. Отключать
       её через disabled нельзя: выглядела бы сломанной. */
    const help = document.getElementById("pvHelpBtn");
    const note = document.getElementById("pvAuthNote");
    if (help && note) {
      const original = note.textContent;
      let showingHelp = false;
      help.addEventListener("click", () => {
        showingHelp = !showingHelp;
        note.textContent = showingHelp ? HELP_TEXT : original;
        // Подсказка не должна выглядеть ошибкой входа: цвет текста
        // приглушённый, а сообщение об ошибке остаётся отдельным.
        err.classList.remove("on");
      });
    }
  }

  /* Меняет надпись на кнопке, сохраняя значок. */
  function setBusy(busy) {
    const btn = document.getElementById("pvAuthBtn");
    if (!btn) return;
    btn.innerHTML = busy
      ? '<span>Проверяем…</span>'
      : ICON_IN + "<span>Войти</span>";
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[ch];
    });
  }

  /* Спрашивает сервер, действующа ли отметка. Сервер — источник
     истины: отметка могла остаться от прежнего пароля. */
  async function ensure() {
    let response;
    try {
      response = await fetch("/api/auth/session", {
        headers: token() ? { "X-Promvizor-Auth": token() } : {},
        cache: "no-store",
      });
    } catch (e) {
      return true; /* сервер недоступен — интерфейс сам скажет об этом */
    }

    if (response.status === 404) {
      /* защита выключена (AUTH_ENABLED=0) — входа не требуется */
      return true;
    }

    if (!response.ok) {
      clearToken();
      show("");
      return false;
    }

    const data = await response.json().catch(() => ({}));
    if (data.auth_required && !data.ok) {
      show("");
      return false;
    }
    return true;
  }

  global.PV = global.PV || {};
  global.PV.auth = {
    token: token,
    setToken: setToken,
    clearToken: clearToken,
    ensure: ensure,
    show: show,
  };
})(window);