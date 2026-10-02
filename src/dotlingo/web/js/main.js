/**
 * Точка входа: boot, роутер, тема, горячие клавиши, push-события из Python.
 */

import { call, isDemo, activateDemoMode, waitReady, tryCall } from './bridge.js';
import * as store from './store.js';
import * as router from './router.js';
import * as components from './components.js';
import { icon } from './icons.js';
import { refreshCatalog } from './device.js';
import { mountDownloadDock } from './download-ui.js';

/** Порядок страниц для Ctrl+1..8 (совпадает с навигацией в index.html). */
const PAGE_ORDER = ['chat', 'projects', 'documents', 'review', 'queue', 'models', 'glossary', 'settings'];

const DEFAULT_PAGE = 'projects';
const THEME_LABEL = { dark: 'Тёмная', light: 'Светлая' };

/* -------------------------------------------------------------------------
 * Boot
 * ------------------------------------------------------------------------- */

async function boot() {
  // Контракт для консольной отладки (и для страниц); push_event появится ниже.
  window.DL = { store, router, call, components, isDemo };

  try {
    wireShell();
    wireKeyboard();
    wirePushEvents();
    components.installSelectMenus();

    // Стартуем с тёмной темой до прихода prefs, чтобы не мигало.
    store.set('theme', 'dark');
    applyTheme('dark');

    // Сначала только «Проекты», чтобы первый кадр не ждал весь граф страниц.
    try {
      await import('./pages/projects.js');
    } catch (e) {
      console.error('[pages] не удалось открыть проекты', e);
    }
    router.showPage(DEFAULT_PAGE);
  } catch (error) {
    console.error('[boot]', error);
    showBootFailure(error);
  }

  void loadExtraPages()
    .then(() => {
      if (document.querySelector('#page-host [data-stub]')) {
        router.showPage(router.currentPage() || DEFAULT_PAGE);
      }
    })
    .catch((error) => {
      console.error('[pages] не удалось загрузить остальные экраны', error);
    });
  void initBridgeData().catch((error) => {
    console.error('[boot] данные моста', error);
  });
}

/** Если модуль оболочки упал, на месте страницы остаётся текст, а не пустое поле. */
function showBootFailure(error) {
  const host = document.getElementById('page-host');
  if (!host) return;
  const box = document.createElement('div');
  box.className = 'boot';
  const title = document.createElement('p');
  title.className = 'boot__title';
  title.textContent = 'Интерфейс открыт частично';
  const text = document.createElement('p');
  text.className = 'boot__text';
  text.textContent = error?.message
    ? `${error.message} Закройте окно и запустите DotLingo ещё раз.`
    : 'Закройте окно и запустите DotLingo ещё раз.';
  box.append(title, text);
  host.replaceChildren(box);
}

/** Остальные страницы и мастер первого запуска. Один запрос на сессию. */
let extraPages = null;

function loadExtraPages() {
  if (!extraPages) {
    extraPages = import('./pages/index.js').catch((error) => {
      extraPages = null;
      console.error('[pages] не удалось загрузить', error);
      throw error;
    });
  }
  return extraPages;
}

/** Открыть страницу, дождавшись её модуля, если оболочка кликнула раньше загрузки. */
async function openPage(name) {
  if (!router.pageNames().includes(name)) {
    try {
      await loadExtraPages();
    } catch {
      // showPage покажет заглушку.
    }
  }
  router.showPage(name);
}

/**
 * Определяет режим bridge, подтягивает prefs/проекты/железо/модели/языки.
 * Ошибки отдельных вызовов не роняют каркас.
 */
async function initBridgeData() {
  const ready = await waitReady();
  if (!ready) {
    activateDemoMode();
    store.set('demo', true);
  }

  // tryCall возвращает пару [data, error], не сам ответ.
  const [prefs] = await tryCall('getPreferences');
  const theme = prefs?.theme === 'light' ? 'light' : 'dark';
  store.patch({ theme, reduceMotion: Boolean(prefs?.reduce_motion) });
  applyTheme(theme);
  document.documentElement.dataset.reduceMotion = prefs?.reduce_motion ? 'true' : 'false';

  const [[active], [hardware], [languages], [dirs]] = await Promise.all([
    tryCall('getActiveProject'),
    tryCall('getHardware'),
    tryCall('getLanguages'),
    tryCall('getDataDirs'),
  ]);

  store.patch({
    activeProject: active ?? null,
    hardware: hardware ?? null,
    languages: languages ?? {},
    dataDirs: dirs ?? null,
  });
  renderDeviceStatus(hardware ?? null);
  renderActiveProject();
  await refreshCatalog();
}

/* -------------------------------------------------------------------------
 * Push-события из Python: window.DL.push_event(name, payload)
 * ------------------------------------------------------------------------- */

/** Обработчики push-событий по имени. */
const pushHandlers = {
  task_event(payload) {
    store.pushTaskEvent(payload);
    store.emit('task_event', payload);
  },
  hardware_detected(payload) {
    store.set('hardware', payload);
    renderDeviceStatus(payload);
    store.emit('hardware_detected', payload);
    // listModels на старте часто уходит раньше, чем проверка устройства.
    // Без повторного запроса рекомендация так и остаётся пустой.
    void refreshCatalog();
  },
  documents_imported(payload) {
    store.emit('documents_imported', payload);
  },
  export_done(payload) {
    store.emit('export_done', payload);
  },
  download_progress(payload) {
    store.emit('download_progress', payload);
  },
  download_done(payload) {
    store.emit('download_done', payload);
  },
  chat_token(payload) {
    store.emit('chat_token', payload);
  },
  chat_done(payload) {
    store.emit('chat_done', payload);
  },
  model_verified(payload) {
    store.emit('model_verified', payload);
  },
  custom_model_imported(payload) {
    store.emit('custom_model_imported', payload);
  },
  market_refreshed(payload) {
    store.emit('market_refreshed', payload);
  },
};

/** Точка входа для Python (api._push): window.DL.push_event(name, payload). */
function wirePushEvents() {
  window.DL.push_event = (name, payload) => {
    const handler = pushHandlers[name];
    if (!handler) {
      console.warn('[push] нет обработчика для', name);
      return;
    }
    try {
      handler(payload);
    } catch (e) {
      console.error('[push]', name, e);
    }
  };
}

/* -------------------------------------------------------------------------
 * Shell: иконки, навигация, шапка, тема, статус устройства
 * ------------------------------------------------------------------------- */

function wireShell() {
  // Инжект SVG-иконок в слоты [data-icon].
  document.querySelectorAll('[data-icon]').forEach((slot) => {
    slot.replaceChildren(icon(slot.dataset.icon));
  });

  // Навигация.
  document.querySelectorAll('.nav-item[data-page]').forEach((btn) => {
    btn.addEventListener('click', () => void openPage(btn.dataset.page));
  });

  // Переключатель темы.
  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    const next = store.get('theme') === 'dark' ? 'light' : 'dark';
    store.set('theme', next);
    applyTheme(next);
    call('setPreferences', { theme: next }).catch(() => {});
  });

  // Первичное состояние индикатора устройства.
  renderDeviceStatus(null);
  const footer = document.querySelector('.sidebar__footer');
  if (footer) mountDownloadDock(footer);

  document.getElementById('active-project')?.addEventListener('click', () => {
    router.showPage('projects');
  });
  store.subscribe((key) => {
    if (key === 'activeProject') renderActiveProject();
  });
  renderActiveProject();
}

function renderActiveProject() {
  const label = document.getElementById('active-project-label');
  if (!label) return;
  const project = store.get('activeProject');
  label.textContent = project?.title || 'Не выбран';
}

/** Применяет тему к <html data-theme> и обновляет кнопку-переключатель. */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;

  const slot = document.querySelector('#theme-toggle .theme-toggle__icon');
  if (slot) slot.replaceChildren(icon(theme === 'dark' ? 'sun' : 'moon'));

  const label = document.getElementById('theme-label');
  if (label) {
    const next = theme === 'dark' ? 'light' : 'dark';
    label.textContent = `${THEME_LABEL[next]} тема`;
  }
}

/**
 * Обновляет индикатор устройства в подвале sidebar.
 * @param {object|null} hw - сериализованный HardwareSnapshot.
 */
function renderDeviceStatus(hw) {
  const status = document.getElementById('device-status');
  const label = document.getElementById('device-label');
  if (!status || !label) return;

  if (!hw) {
    status.dataset.state = 'unknown';
    label.textContent = isDemo() ? 'demo data' : 'Проверка устройства…';
    return;
  }

  const parts = [];
  if (hw.cpuThreads) parts.push(`${hw.cpuThreads} потоков CPU`);
  if (hw.ramTotalGb) parts.push(`${components.formatBytes(hw.ramTotalGb * 1024 ** 3)} RAM`);
  if (hw.profile) parts.push(hw.profile);

  status.dataset.state = hw.llamaRuntimeAvailable ? 'ok' : 'warn';
  label.textContent = parts.join(' · ') || 'Готово';
}

/* -------------------------------------------------------------------------
 * Клавиатура: Ctrl+1..7, Esc для модалок
 * ------------------------------------------------------------------------- */

function wireKeyboard() {
  document.addEventListener('keydown', (e) => {
    // Esc закрывает верхнюю модалку.
    if (e.key === 'Escape') {
      components.handleEscape();
      return;
    }
    // Ctrl+1..8 - быстрый переход по страницам.
    if (!e.ctrlKey || e.altKey || e.shiftKey || e.metaKey) return;
    const index = Number(e.key) - 1;
    if (!Number.isInteger(index) || index < 0 || index >= PAGE_ORDER.length) return;
    e.preventDefault();
    void openPage(PAGE_ORDER[index]);
  });
}

/* -------------------------------------------------------------------------
 * Запуск
 * ------------------------------------------------------------------------- */

boot();
