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

const DEMO_PREFS = {
  theme: 'dark',
  reduce_motion: false,
  setup_seen: true,
  last_project: '',
};

const DEMO_LANGUAGES = {
  auto: { code: 'auto', label: 'Автоопределение моделью' },
  languages: [
    { code: 'en', label: 'Английский' },
    { code: 'ru', label: 'Русский' },
    { code: 'de', label: 'Немецкий' },
    { code: 'fr', label: 'Французский' },
    { code: 'zh', label: 'Китайский' },
  ],
};

const DEMO_HARDWARE = {
  profile: 'демо',
  cpuThreads: 4,
  ramTotalGb: 16,
  ramAvailableGb: 9.5,
  diskFreeGb: 120,
  gpuNames: [],
  gpuVramGb: [],
  llamaRuntimeAvailable: false,
  llamaGpuOffloadAvailable: false,
};

const DEMO_MODELS = {
  models: [
    {
      id: 'demo-model',
      name: 'Demo Model',
      custom: false,
      status: 'available',
      installState: 'available',
      installed: false,
      sizeBytes: 4096000000,
      sizeLabel: '3.81 ГБ',
      estimatedRamGb: 6,
      license: 'Не указана',
      licenseUrl: '',
      cardUrl: '',
      revision: 'demo',
      quantization: 'Q4_K_M',
      testedOnWindows: false,
      languageCodes: ['en', 'ru'],
      uiDescription: 'Демонстрационная карточка, не модель перевода.',
      uiDetails: '',
      compatibility: null,
    },
  ],
  recommendation: null,
};

function demoError() {
  const error = new Error('Это демонстрационные данные. Запустите DotLingo, чтобы сохранить изменения.');
  error.code = 'demo';
  throw error;
}

/**
 * Мок-ответ по имени метода api. Форма совпадает с полем data настоящего моста.
 * @param {string} method
 */
function mockResponse(method) {
  switch (method) {
    case 'getPreferences':
      return { ...DEMO_PREFS };
    case 'setPreferences':
      return { ...DEMO_PREFS };
    case 'listLanguages':
      return DEMO_LANGUAGES;
    case 'listProjects':
      return [];
    case 'getActiveProject':
      return null;
    case 'listModels':
      return DEMO_MODELS;
    case 'getHardware':
      return { ...DEMO_HARDWARE };
    case 'listTasks':
      return [];
    case 'getDataDirs':
      return {
        projectsDir: 'demo/projects',
        modelsDir: 'demo/models',
        dataDir: 'demo',
      };
    case 'detectHardware':
      return { started: true };
    default:
      demoError();
      return null;
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
      throw new Error(`Метод ${method} недоступен`);
    }
    const result = await fn(...args);
    if (result && typeof result === 'object' && 'ok' in result) {
      if (!result.ok) {
        const error = new Error(result.error || 'Ошибка');
        error.code = result.code || 'error';
        throw error;
      }
      return result.data;
    }
    return result;
  }
  activateDemoMode();
  await new Promise((r) => setTimeout(r, 120));
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
