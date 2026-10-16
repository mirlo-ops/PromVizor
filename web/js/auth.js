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
  /* Композиция входа — форма перекрывает промо-панель, как в референсе.
     Градиент и изображения отдаются из web/assets, поэтому пути здесь
     относительно корня сайта: экран авторизации собирается динамически.

     Оформление тёмное — те же цвета, что и у приложения (палитра из
     презентации): #081C15 фон, #0F2C21 карточка, #52B788 и #D8F3DC
     акценты. Светлая форма на тёмном фоне сайта выглядела бы как
     чужое окно, вставленное в тёмный интерфейс. */
  const STYLE = `
  * { box-sizing: border-box; }
  html { min-height: 100%; background: #081c15; }
  body {
    min-height: 100vh; min-height: 100dvh; margin: 0;
    display: grid; place-items: center;
    padding: clamp(18px, 4vw, 56px);
    background: #081c15 url("assets/gradient.png") center / cover no-repeat fixed;
    color: #e8f3ec;
    font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  }

  .pv-auth-layout {
    display: flex; align-items: center;
    width: min(1120px, 100%);
    min-height: 510px;
  }
  .pv-promo {
    position: relative; isolation: isolate; overflow: hidden;
    flex: 1 1 auto; min-width: 0; min-height: 510px;
    border: 1px solid rgba(216,243,220,.16);
    border-radius: 24px;
    background: #0e221b;
    box-shadow: 0 28px 70px rgba(3,12,8,.55);
  }
  .pv-promo img {
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    display: block; object-fit: cover; object-position: center;
  }
  .pv-login {
    position: relative; z-index: 2; flex: 0 0 440px;
    margin-right: -118px; padding: 48px 46px 34px;
    border: 1px solid rgba(216,243,220,.14); border-radius: 22px;
    background: #0f2c21;
    box-shadow: 0 24px 64px rgba(3,12,8,.5);
  }
  .pv-eyebrow {
    margin-bottom: 7px; color: #d8f3dc;
    font-family: "Century Gothic", "Avenir Next", "Montserrat", sans-serif;
    font-size: 17px; font-weight: 500; letter-spacing: .015em;
  }
  .pv-login h1 { margin: 0 0 30px; color: #e8f3ec; font-size: 29px; line-height: 1.2; font-weight: 700; letter-spacing: -.035em; }
  .pv-field { position: relative; margin-bottom: 18px; }
  .pv-field label { display: block; margin-bottom: 7px; color: #b9cfc3; font-size: 13px; font-weight: 600; }
  .pv-field svg {
    position: absolute;
    left: 13px; top: 39px;
    width: 16px; height: 16px;
    fill: none;
    stroke: #52b788;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
    pointer-events: none;
  }
  .pv-field input {
    width: 100%;
    min-height: 48px; padding: 11px 13px 11px 40px;
    border-radius: 10px; border: 1px solid #22352c;
    background: #182e26; color: #e8f3ec; font: inherit; font-size: 14px;
  }
  .pv-field input::placeholder { color: #7d9689; }
  /* Подсветка рамки при наведении, чтобы поле читалось границей, а не
     только сменой фона: тёмный фон поля слишком близок к карточке. */
  .pv-field input:hover { border-color: #2f4a3e; }
  .pv-field input:focus {
    outline: 3px solid rgba(216,243,220,.16); outline-offset: 1px;
    border-color: #52b788;
  }
  .pv-login .err {
    display: none; margin: 2px 0 14px; padding: 10px 12px;
    border: 1px solid rgba(255,107,107,.4); border-radius: 9px;
    background: rgba(255,107,107,.12); color: #ffb0ad; font-size: 13px;
  }
  .pv-login .err.on { display: block; }
  .pv-actions {
    display: flex; gap: 10px; align-items: center; margin-top: 22px;
  }
  .pv-actions button {
    min-height: 48px; flex: 1; padding: 11px 16px;
    border: 0; border-radius: 10px; font-size: 14px; font-weight: 650;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: 8px;
    transition: background .15s, opacity .15s, transform .15s;
  }
  .pv-actions button:focus-visible { outline: 3px solid #d8f3dc; outline-offset: 3px; }
  .pv-actions button:active { transform: translateY(1px); }
  .pv-actions button svg {
    width: 15px; height: 15px;
    fill: none; stroke: currentColor; stroke-width: 1.9;
    stroke-linecap: round; stroke-linejoin: round;
  }
  /* Светло-зелёная заливка с тёмным текстом: на тёмной форме это
     единственная кнопка, которая сразу видна глазом. */
  .pv-btn-primary { background: #d8f3dc; color: #081c15; }
  .pv-btn-primary:hover { background: #b9e5c4; }
  .pv-btn-primary:disabled { opacity: .55; cursor: default; }
  .pv-btn-help {
    background: #182e26; color: #b9cfc3;
    border: 1px solid #2c5341 !important;
  }
  .pv-btn-help:hover { background: #1e3830; color: #e8f3ec; }
  .pv-login .note {
    margin: 20px 0 0; padding-top: 15px;
    border-top: 1px solid #1a2c25;
    font-size: 12px; line-height: 1.55; color: #8fa89a;
  }
  @media (max-width: 760px) {
    body { display: block; padding: 24px 16px; background-attachment: scroll; }
    .pv-auth-layout { width: min(500px, 100%); min-height: 0; margin: 0 auto; flex-direction: column; align-items: stretch; }
    .pv-login { flex: none; width: 100%; margin: 0 0 -38px; padding: 34px clamp(22px, 7vw, 42px) 28px; }
    .pv-promo { flex: none; width: 100%; min-height: 250px; padding-top: 38px; border-radius: 20px; }
  }
  @media (max-width: 420px) {
    .pv-login { padding: 30px 22px 24px; }
    .pv-login h1 { font-size: 26px; margin-bottom: 24px; }
    .pv-actions { flex-direction: column; align-items: stretch; }
    .pv-actions button { width: 100%; }
    .pv-promo { min-height: 210px; }
  }`;

  function show(errorText) {
    document.documentElement.innerHTML =
      '<head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>ПромВизор — вход</title>' +
      '<link rel="icon" href="data:image/svg+xml,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 100 100\'><text y=\'.9em\' font-size=\'90\'>&#128230;</text></svg>">' +
      '<style>' + STYLE + '</style></head>' +
      '<body>' +
      '<main class="pv-auth-layout" aria-label="Вход в PromVizor">' +
      '<form class="pv-login" id="pvAuthForm">' +
      '<p class="pv-eyebrow">PromVizor</p>' +
      "<h1>Вход в систему</h1>" +
      '<div class="pv-field">' +
      ICON_USER +
      '<label for="pvLogin">Логин</label>' +
      '<input id="pvLogin" name="login" placeholder="Введите логин" autocomplete="username" required>' +
      "</div>" +
      '<div class="pv-field">' +
      ICON_LOCK +
      '<label for="pvPass">Пароль</label>' +
      '<input id="pvPass" name="password" type="password" placeholder="Введите пароль" autocomplete="current-password" required>' +
      "</div>" +
      '<div class="err" id="pvAuthErr" role="alert" aria-live="assertive">' + escapeHtml(errorText || "") + "</div>" +
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
      "</form>" +
      '<aside class="pv-promo" aria-label="PromVizor — система контроля персонала">' +
      '<img src="assets/logo_na_fone.png" alt="Логотип PromVizor на фоне декоративной иллюстрации">' +
      "</aside></main></body>";

    const form = document.getElementById("pvAuthForm");
    const login = document.getElementById("pvLogin");
    const pass = document.getElementById("pvPass");
    const err = document.getElementById("pvAuthErr");
    const btn = document.getElementById("pvAuthBtn");

    login.focus();

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
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
