/* ============================================================
   ПромВизор — клиент API
   ------------------------------------------------------------
   Говорит с backend'ом «ПромВизор» (FastAPI, Этап 6).

   Главное правило этого файла: ФРОНТЕНД НИЧЕГО НЕ ПЕРЕСЧИТЫВАЕТ.
   Ущерб приходит готовым в estimated_loss — по формуле
   calculate_loss() из app/core/types.py. Согласно Handoff
   (раздел 14), право считать ущерб есть только у Lev.
   Здесь нет ни формулы, ни стоимости минуты для пересчёта.

   Что этот файл НЕ делает:
   * не подставляет mock-данные, если сервер недоступен;
   * не «поправляет» пришедшие значения;
   * не перезапрашивает по событию того, что уже есть в кэше.

   Если backend недоступен, pv.api.ready() вернёт false, и
   интерфейс покажет честное «нет связи», а не выдуманные числа.
   ============================================================ */
(function (global) {
  "use strict";

  /* Адрес backend'а. Пустая строка — тот же хост, с которого открыт
     Dashboard: при раздаче через сам API (StaticFiles) этого
     достаточно, и CORS не нужен вовсе. */
  const DEFAULT_BASE = "";

  /* Таймаут запроса, мс. Без него при остановленном backend'е
     вкладка «висит» на минуты вместо понятного отказа. */
  const TIMEOUT_MS = 8000;

  const STATUS_KEYS = {
    WORKING: "WORKING",
    STOPPED: "STOPPED",
    UNKNOWN: "UNKNOWN",
  };
  const EVENT_KEYS = {
    DOWNTIME_STARTED: "DOWNTIME_STARTED",
    DOWNTIME_FINISHED: "DOWNTIME_FINISHED",
  };

  function apiBase() {
    try {
      const saved = JSON.parse(localStorage.getItem("pv_settings") || "{}");
      if (saved && typeof saved.api_base === "string" && saved.api_base.trim()) {
        return saved.api_base.trim().replace(/\/+$/, "");
      }
    } catch (e) {
      /* настройки битые — берём значение по умолчанию */
    }
    return DEFAULT_BASE;
  }

  /* ---------- низкоуровневый запрос ---------- */
  async function request(path, params) {
    const url = new URL(apiBase() + path, location.href);
    if (params) {
      Object.keys(params).forEach((key) => {
        const value = params[key];
        // null и undefined означают «параметр не задан» — его не шлём,
        // иначе backend получил бы пустую строку вместо отсутствия
        if (value !== null && value !== undefined && value !== "") {
          url.searchParams.set(key, value);
        }
      });
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const response = await fetch(url.toString(), {
        method: "GET",
        signal: controller.signal,
        headers: { Accept: "application/json" },
        cache: "no-store",
      });

      if (!response.ok) {
        // Тело ошибки полезно: backend кладёт туда понятный текст
        let detail = `HTTP ${response.status}`;
        try {
          const body = await response.json();
          if (body && body.detail) detail = String(body.detail);
        } catch (e) {
          /* тело не JSON — оставляем код ответа */
        }
        throw new Error(detail);
      }

      return await response.json();
    } catch (err) {
      if (err && err.name === "AbortError") {
        throw new Error(`Превышено время ожидания ${TIMEOUT_MS / 1000} с`);
      }
      throw new Error(err && err.message ? err.message : String(err));
    } finally {
      clearTimeout(timer);
    }
  }

  /* ---------- состояние соединения ---------- */
  /* connected — это «последний запрос удался», а не «сейчас всё в порядке».
     Пока нет ни одного успешного запроса, интерфейс показывает «нет связи»:
     иначе при первом запуске он мелькнёт «всё хорошо» и упадёт. */
  const state = {
    connected: false,
    lastError: null,
    lastSync: null,
  };

  /* ---------- кэш ---------- */
  /* Кэш нужен, чтобы при перерисовке не дёргать сеть на каждый экран.
     Записи группируются по ключу запроса; срок жизни — минута,
     иначе «текущий статус» перестал бы отражать текущий. */
  const cache = new Map();
  const CACHE_TTL_MS = 60 * 1000;

  function cacheGet(key) {
    const hit = cache.get(key);
    if (!hit) return null;
    if (Date.now() - hit.at > CACHE_TTL_MS) {
      cache.delete(key);
      return null;
    }
    return hit.value;
  }

  /* Значение из кэша даже после истечения срока. Оно устарело, но
     показывать его лучше, чем пустой экран: недавние данные вредят
     оператору меньше, чем никакие. */
  function cacheGetStale(key) {
    const hit = cache.get(key);
    return hit ? hit.value : null;
  }

  function cacheSet(key, value) {
    cache.set(key, { at: Date.now(), value });
    return value;
  }

  function cacheClear() {
    cache.clear();
  }

  /* Обёртка: кэш + учёт состояния соединения.
     `fresh` заставляет пойти на сервер, минуя свежий кэш: этого
     требует автообновление, иначе «текущее состояние» замерело бы
     на первых же показаниях. */
  async function cached(key, loader, fresh) {
    if (!fresh) {
      const hit = cacheGet(key);
      if (hit) return hit;
    }
    const stale = fresh ? cacheGetStale(key) : null;

    try {
      const value = await loader();
      state.connected = true;
      state.lastError = null;
      state.lastSync = new Date();
      return cacheSet(key, value);
    } catch (err) {
      state.lastError = err && err.message ? err.message : String(err);
      if (stale !== null) {
        // Данные показаны, но сервер не подтвердил их свежесть.
        // Связь считаем прерванной — иначе индикатор врал бы.
        state.connected = false;
        return stale;
      }
      state.connected = false;
      return null;
    }
  }

  /* ---------- публичные вызовы ---------- */
  const api = {
    LineStatus: STATUS_KEYS,
    EventType: EVENT_KEYS,

    status(opts) {
      const fresh = !!(opts && opts.fresh);
      return cached("status", () => request("/api/status"), fresh);
    },

    cameras(opts) {
      const fresh = !!(opts && opts.fresh);
      return cached("cameras", () => request("/api/cameras"), fresh);
    },

    config(opts) {
      const fresh = !!(opts && opts.fresh);
      return cached("config", () => request("/api/config"), fresh);
    },

    health() {
      // health всегда идёт на сервер: его назначение — проверить связь
      return request("/api/health");
    },

    events(opts) {
      const o = opts || {};
      const fresh = !!o.fresh;
      const key =
        "events:" +
        (o.since || "") + ":" + (o.until || "") + ":" +
        (o.camera_id || "") + ":" + (o.event_type || "") + ":" +
        (o.limit || "");
      return cached(
        key,
        () =>
          request("/api/events", {
            since: o.since,
            until: o.until,
            camera_id: o.camera_id,
            event_type: o.event_type,
            limit: o.limit || 200,
          }),
        fresh
      );
    },

    statistics(opts) {
      const o = opts || {};
      const fresh = !!o.fresh;
      const key = "stats:" + (o.since || "") + ":" + (o.until || "") + ":" + (o.camera_id || "");
      return cached(
        key,
        () =>
          request("/api/statistics", {
            since: o.since,
            until: o.until,
            camera_id: o.camera_id,
          }),
        fresh
      );
    },

    /* ---------- состояние соединения ---------- */
    ready() {
      return state.connected;
    },

    lastError() {
      return state.lastError;
    },

    lastSync() {
      return state.lastSync;
    },

    refresh() {
      cacheClear();
      api.markConnected(false);
    },

    markConnected(value) {
      state.connected = value === true;
    },
  };

  global.PV = global.PV || {};
  global.PV.api = api;
})(window);