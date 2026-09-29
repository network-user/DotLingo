/**
 * Обёртка над pywebview.api: промис-хелпер call(method, ...args),
 * ожидание готовности bridge и dev-фолбэк с мок-ответами.
 */

/** Таймаут готовности api, когда window.pywebview существует. */
const READY_TIMEOUT_MS = 10000;

/** Короткий таймаут, когда pywebview-объекта нет вовсе (обычный браузер). */
const NO_BRIDGE_TIMEOUT_MS = 2000;

let apiReady = false;
let readyPromise = null;
let demoMode = false;

/* -------------------------------------------------------------------------
 * Мок-ответы для dev-режима (index.html открыт без pywebview)
 * ------------------------------------------------------------------------- */

const MOCK_PROJECTS = {
  projects: [
    {
      id: 'demo-1',
      name: 'Демо-проект',
      source_lang: 'en',
      target_lang: 'ru',
      created_at: '2026-09-20T10:00:00',
      documents: 3,
    },
  ],
};

const MOCK_PREFS = {
  theme: 'dark',
  last_project: null,
  cpu_threads: 4,
};

const MOCK_HARDWARE = {
  cpu: 'Demo CPU',
  cores: 8,
  ram_total_gb: 16,
  ram_available_gb: 9.5,
  disk_free_gb: 120,
};

const MOCK_MODELS = {
  models: [
    {
      id: 'demo-model',
      name: 'Demo Model Q4_K_M',
      size_bytes: 4096000000,
      downloaded: false,
    },
  ],
};

/**
 * Мок-ответ по имени метода api.
 * @param {string} method
 */
function mockResponse(method) {
  switch (method) {
    case 'get_prefs':
      return { ...MOCK_PREFS };
    case 'list_projects':
      return MOCK_PROJECTS;
    case 'get_hardware':
      return { ...MOCK_HARDWARE };
    case 'list_models':
      return MOCK_MODELS;
    case 'set_theme':
      return { ok: true };
    default:
      console.warn(`[bridge] demo: нет мока для метода ${method}`);
      return { ok: true, demo: true };
  }
}

/* -------------------------------------------------------------------------
 * Готовность api
 * ------------------------------------------------------------------------- */

/**
 * Ждёт готовности window.pywebview.api.
 * Если pywebview-объекта нет вовсе - короткий таймаут (dev-браузер).
 * @returns {Promise<boolean>} true, когда api доступен.
 */
export function waitReady() {
  if (apiReady) return Promise.resolve(true);
  if (readyPromise) return readyPromise;

  readyPromise = new Promise((resolve) => {
    if (window.pywebview?.api) {
      apiReady = true;
      resolve(true);
      return;
    }
    // pywebview инжектит объект до старта скриптов: раз его нет - почти
    // наверняка открыт обычный браузер, долго ждать смысла нет.
    const timeout = window.pywebview ? READY_TIMEOUT_MS : NO_BRIDGE_TIMEOUT_MS;
    const timer = setTimeout(() => resolve(Boolean(window.pywebview?.api)), timeout);
    window.addEventListener(
      'pywebviewready',
      () => {
        clearTimeout(timer);
        apiReady = true;
        resolve(true);
      },
      { once: true }
    );
  });
  return readyPromise;
}

/**
 * Вызвать метод pywebview.api; в dev-режиме возвращает мок.
 * @param {string} method - имя метода api.
 * @param {...unknown} args
 * @returns {Promise<unknown>}
 */
export async function call(method, ...args) {
  const ready = await waitReady();
  if (ready) {
    const fn = window.pywebview.api[method];
    if (typeof fn !== 'function') {
      throw new Error(`[bridge] метод api.${method} не найден`);
    }
    return fn(...args);
  }
  activateDemoMode();
  await new Promise((r) => setTimeout(r, 160));
  return mockResponse(method, args);
}

/**
 * true, когда приложение работает на моках (pywebview.api недоступен).
 * @returns {boolean}
 */
export function isDemo() {
  return demoMode;
}

/** Рисует банер «demo data» (идемпотентно). */
export function activateDemoMode() {
  if (demoMode) return;
  demoMode = true;
  const banner = document.createElement('div');
  banner.className = 'demo-banner';
  banner.textContent = 'demo data';
  document.body.appendChild(banner);
}
