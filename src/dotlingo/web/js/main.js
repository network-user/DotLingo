/**
 * Точка входа: boot, роутер, тема, горячие клавиши, push-события из Python.
 */

import { call, isDemo, activateDemoMode, waitReady, tryCall } from './bridge.js';
import * as store from './store.js';
import * as router from './router.js';
import * as components from './components.js';
import { icon } from './icons.js';

/** Порядок страниц для Ctrl+1..7 (совпадает с навигацией в index.html). */
const PAGE_ORDER = ['projects', 'documents', 'review', 'queue', 'models', 'glossary', 'settings'];

const DEFAULT_PAGE = 'projects';
const THEME_LABEL = { dark: 'Тёмная', light: 'Светлая' };

/* -------------------------------------------------------------------------
 * Boot
 * ------------------------------------------------------------------------- */

async function boot() {
  // Контракт для консольной отладки (и для страниц); push_event появится ниже.
  window.DL = { store, router, call, components, isDemo };

  wireShell();
  wireKeyboard();
  wirePushEvents();

  // Стартуем с тёмной темой до прихода prefs, чтобы не мигало.
  store.set('theme', 'dark');
  applyTheme('dark');

  router.showPage(DEFAULT_PAGE);

  // Фоновая инициализация данных: не блокирует отрисовку каркаса.
  initBridgeData().then(() =>
    import('./pages/index.js').catch((e) => console.error('[pages] не удалось загрузить', e))
  );
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

  const [prefs] = await Promise.all([tryCall('getPreferences')]);
  const theme = prefs?.theme === 'light' ? 'light' : 'dark';
  store.patch({ theme, reduceMotion: Boolean(prefs?.reduce_motion) });
  applyTheme(theme);

  const [projects, active, hardware, models, languages, dirs] = await Promise.all([
    tryCall('listProjects'),
    tryCall('getActiveProject'),
    tryCall('getHardware'),
    tryCall('listModels'),
    tryCall('getLanguages'),
    tryCall('getDataDirs'),
  ]);

  store.patch({
    projects: projects ?? [],
    activeProject: active ?? null,
    hardware,
    models: models?.models ?? [],
    recommendation: models?.recommendation ?? null,
    languages: languages ?? {},
    dataDirs: dirs ?? null,
  });

  renderDeviceStatus(store.get('hardware'));
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
  model_verified(payload) {
    store.emit('model_verified', payload);
  },
  custom_model_imported(payload) {
    store.emit('custom_model_imported', payload);
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
    btn.addEventListener('click', () => router.showPage(btn.dataset.page));
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
    // Ctrl+1..7 - быстрый переход по страницам.
    if (!e.ctrlKey || e.altKey || e.shiftKey || e.metaKey) return;
    const index = Number(e.key) - 1;
    if (!Number.isInteger(index) || index < 0 || index >= PAGE_ORDER.length) return;
    e.preventDefault();
    router.showPage(PAGE_ORDER[index]);
  });
}

/* -------------------------------------------------------------------------
 * Запуск
 * ------------------------------------------------------------------------- */

boot();
