/**
 * Обёртка над pywebview.api: call(method, ...args) всегда возвращает
 * распакованный конверт {ok, data, error, code} → data (или кидает BridgeError).
 * Ожидание готовности bridge и dev-фолбэк с мок-ответами.
 */

/** Таймаут готовности api, когда window.pywebview существует. */
const READY_TIMEOUT_MS = 10000;

/** Короткий таймаут, когда pywebview-объекта нет вовсе (обычный браузер). */
const NO_BRIDGE_TIMEOUT_MS = 2000;

let apiReady = false;
let readyPromise = null;
let demoMode = false;

/* -------------------------------------------------------------------------
 * BridgeError: ошибка метода api (ok: false)
 * ------------------------------------------------------------------------- */

export class BridgeError extends Error {
  /**
   * @param {string} message
   * @param {string} [code]
   */
  constructor(message, code = 'error') {
    super(message);
    this.name = 'BridgeError';
    this.code = code;
  }
}

/* -------------------------------------------------------------------------
 * Мок-ответы для dev-режима (index.html открыт без pywebview)
 * ------------------------------------------------------------------------- */

const MOCK_LANGUAGES = { ru: 'Русский', en: 'Английский', de: 'Немецкий', fr: 'Французский' };

const MOCK_PREFS = { theme: 'dark', reduce_motion: false, last_project: '', setup_seen: true };

const MOCK_MODELS = {
  models: [
    {
      id: 'demo-model',
      name: 'Demo Model Q4_K_M',
      custom: false,
      status: 'available',
      installState: 'available',
      installed: false,
      sizeBytes: 4096000000,
      sizeLabel: '3.81 ГБ',
      estimatedRamGb: 5.2,
      license: 'Apache-2.0',
      licenseUrl: '',
      cardUrl: '',
      repo: '',
      revision: '',
      quantization: 'Q4_K_M',
      testedOnWindows: true,
      languageCodes: ['ru', 'en', 'de', 'fr'],
      uiDescription: 'Демонстрационная карточка модели.',
      uiDetails: '',
      notes: '',
      compatibility: null,
    },
  ],
  recommendation: null,
};

/**
 * Мок-ответ по имени метода api (конверт как в api.py).
 * @param {string} method
 * @returns {{ok: boolean, data: unknown}}
 */
function mockResponse(method) {
  switch (method) {
    case 'getPreferences':
      return { ok: true, data: { ...MOCK_PREFS } };
    case 'setPreferences':
      return { ok: true, data: { ...MOCK_PREFS } };
    case 'getLanguages':
      return { ok: true, data: MOCK_LANGUAGES };
    case 'getLocaleHints':
      return { ok: true, data: { keyboard: '', interface: '' } };
    case 'listProjects':
      return { ok: true, data: [] };
    case 'getActiveProject':
      return { ok: true, data: null };
    case 'getHardware':
      return { ok: true, data: null };
    case 'listModels':
      return { ok: true, data: MOCK_MODELS };
    case 'planSetup':
      return {
        ok: true,
        data: {
          action: 'skip',
          modelId: null,
          reason: 'Демонстрация без загрузки. Приложение открывается сразу.',
          name: '',
          sizeLabel: '',
          license: '',
        },
      };
    case 'detectHardware':
      return { ok: true, data: { started: true } };
    case 'listDialogs':
      return { ok: true, data: [] };
    case 'loadDialog':
      return { ok: false, error: 'Диалог не найден.', code: 'not_found' };
    case 'saveDialog':
      return { ok: true, data: null };
    case 'deleteDialog':
      return { ok: true, data: null };
    case 'readChatAttachment':
      return { ok: true, data: { name: 'demo.txt', text: 'Демонстрационный файл.', truncated: false, chars: 24 } };
    case 'dialogMeter':
      return {
        ok: true,
        data: {
          cpuPercent: null,
          ramPercent: null,
          placement: 'CPU',
          contextLimit: 2048,
          contextUsed: 0,
          contextPercent: 0,
        },
      };
    case 'resolveImportPaths':
      return { ok: true, data: [] };
    case 'listMarket':
      return { ok: true, data: { fetchedAt: null, stale: false, errors: [], offers: [] } };
    case 'resolveMarketLink':
      return {
        ok: false,
        error: 'В браузере без окна приложения ссылка не проверяется.',
        code: 'invalid',
      };
    case 'getDataDirs':
      return { ok: true, data: { projectsDir: 'C:\\demo\\projects', modelsDir: 'C:\\demo\\models' } };
    case 'listTasks':
      return { ok: true, data: [] };
    case 'publishPendingOutputs':
      return { ok: true, data: { started: true } };
    case 'openPath':
    case 'revealPath':
      return { ok: true, data: null };
    case 'previewExport':
      return { ok: true, data: { kind: 'text', name: 'demo.txt', text: 'Демонстрационный просмотр.' } };
    case 'listConversionTargets':
      return {
        ok: true,
        data: [
          { suffix: '.txt', label: 'Текст', note: 'Демонстрация без записи файла.' },
          { suffix: '.html', label: 'HTML', note: 'Демонстрация без записи файла.' },
        ],
      };
    case 'inspectConversion':
      return { ok: true, data: { files: [], errors: [] } };
    case 'convertDocuments':
      return { ok: false, error: 'Конвертация запускается в окне приложения.', code: 'no_window' };
    case 'resolveOutputDirectory':
      return { ok: true, data: null };
    default:
      console.warn(`[bridge] demo: нет мока для метода ${method}`);
      return { ok: true, data: null };
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
    const succeed = () => {
      clearInterval(timer);
      apiReady = true;
      resolve(true);
    };
    if (window.pywebview?.api) {
      apiReady = true;
      resolve(true);
      return;
    }
    // Событие pywebviewready часто приходит раньше подписки модуля.
    // Один длинный таймер тогда держит экран на спиннере до 10 секунд.
    const limit = window.pywebview ? READY_TIMEOUT_MS : NO_BRIDGE_TIMEOUT_MS;
    const started = performance.now();
    const timer = setInterval(() => {
      if (window.pywebview?.api) succeed();
      else if (performance.now() - started >= limit) {
        clearInterval(timer);
        resolve(false);
      }
    }, 40);
    window.addEventListener('pywebviewready', succeed, { once: true });
  });
  return readyPromise;
}

/**
 * Вызвать метод pywebview.api и распаковать конверт.
 * В dev-режиме возвращает мок. При ok:false кидает BridgeError.
 * @param {string} method - имя метода api (camelCase).
 * @param {...unknown} args
 * @returns {Promise<unknown>} data из конверта.
 */
export async function call(method, ...args) {
  const ready = await waitReady();
  if (ready) {
    const fn = window.pywebview.api[method];
    if (typeof fn !== 'function') {
      throw new BridgeError(`Метод api.${method} не найден`);
    }
    const envelope = await fn(...args);
    if (!envelope || typeof envelope !== 'object') {
      throw new BridgeError(`Пустой ответ метода ${method}`);
    }
    if (envelope.ok !== true) {
      throw new BridgeError(envelope.error || 'Неизвестная ошибка', envelope.code || 'error');
    }
    return envelope.data;
  }
  activateDemoMode();
  await new Promise((r) => setTimeout(r, 120));
  const mocked = mockResponse(method);
  if (mocked.ok !== true) throw new BridgeError(mocked.error || 'demo error', mocked.code);
  return mocked.data;
}

/**
 * Как call, но не кидает исключение: возвращает [data, null] или [null, error].
 * @param {string} method
 * @param {...unknown} args
 * @returns {Promise<[unknown, BridgeError|null]>}
 */
export async function tryCall(method, ...args) {
  try {
    return [await call(method, ...args), null];
  } catch (e) {
    return [null, e instanceof BridgeError ? e : new BridgeError(String(e))];
  }
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
