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
     ---------------------------------------------------------- */
  const STYLE = `
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    display: flex; align-items: center; justify-content: center;
    background: #0f1216; color: #e6e9ee;
    font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  }
  .pv-login {
    width: 330px; padding: 30px 28px; border-radius: 12px;
    background: #171b21; border: 1px solid #262c34;
  }
  .pv-login h1 { margin: 0 0 4px; font-size: 19px; font-weight: 600; }
  .pv-login .sub { margin: 0 0 22px; color: #8b95a3; font-size: 13px; }
  .pv-login label {
    display: block; font-size: 12px; color: #8b95a3; margin-bottom: 6px;
  }
  .pv-login input {
    width: 100%; padding: 10px 12px; margin-bottom: 15px; border-radius: 7px;
    border: 1px solid #313945; background: #0f1216; color: #e6e9ee;
    font-size: 14px;
  }
  .pv-login input:focus { outline: none; border-color: #3d7d5f; }
  .pv-login button {
    width: 100%; padding: 11px; border: 0; border-radius: 7px;
    background: #2f6f52; color: #fff; font-size: 14px; font-weight: 500;
    cursor: pointer;
  }
  .pv-login button:hover { background: #3a8563; }
  .pv-login button:disabled { opacity: .6; cursor: default; }
  .pv-login .err {
    display: none; margin: -6px 0 14px; color: #e07a5f; font-size: 12px;
  }
  .pv-login .err.on { display: block; }
  /* Подпись под формой: на демонстрации пароль не должен быть виден
     на экране. Текст не содержит самого пароля — только имя поля. */
  .pv-login .note {
    margin: 18px 0 0; font-size: 11px; color: #5d6673; line-height: 1.5;
  }`;

  function show(errorText) {
    document.documentElement.innerHTML =
      '<head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>ПромВизор — вход</title>' +
      '<style>' + STYLE + '</style></head>' +
      '<body>' +
      '<form class="pv-login" id="pvAuthForm">' +
      '<h1>ПромВизор</h1>' +
      '<p class="sub">Вход для просмотра данных системы</p>' +
      '<label for="pvLogin">Логин</label>' +
      '<input id="pvLogin" autocomplete="username" required>' +
      '<label for="pvPass">Пароль</label>' +
      '<input id="pvPass" type="password" autocomplete="current-password" required>' +
      '<div class="err" id="pvAuthErr">' + escapeHtml(errorText || "") + "</div>" +
      '<button type="submit" id="pvAuthBtn">Войти</button>' +
      '<p class="note">Пароль спрашивается в каждой новой вкладке. ' +
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
      btn.textContent = "Проверяем…";
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
          btn.textContent = "Войти";
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
        btn.textContent = "Войти";
      }
    });
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