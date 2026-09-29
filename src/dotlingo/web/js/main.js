/**
 * Точка входа: boot, роутер, тема, горячие клавиши.
 * Страниц в реестре пока нет - придет волна 2 (другие агенты).
 */

import { call, isDemo, activateDemoMode, waitReady } from './bridge.js';
import * as store from './store.js';
import * as router from './router.js';
import * as components from './components.js';
import { icon } from './icons.js';

/** Порядок страниц для Ctrl+1..7 (совпадает с навигацией в index.html). */
const PAGE_ORDER = [
  'projects',
  'documents',
  'review',
  'queue',
  'models',
  'glossary',
  'settings',
];

const DEFAULT_PAGE = 'projects';
const THEME_LABEL = { dark: 'Тёмная', light: 'Светлая' };

/* -------------------------------------------------------------------------
 * Boot
 * ------------------------------------------------------------------------- */

async function boot() {
  wireShell();
  wireKeyboard();

  // Стартуем с тёмной темой до прихода prefs, чтобы не мигало.
  store.set('theme', 'dark');
  applyTheme('dark');

  router.showPage(DEFAULT_PAGE);

  // Фоновая инициализация данных: не блокирует отрисовку каркаса.
  initBridgeData();

  // Контракт для консольной отладки (и для волны 2).
  window.DL = { store, router, call, components, isDemo };
}

/**
 * Определяет режим bridge, подтягивает prefs/проекты/железо/модели.
 * Ошибки отдельных вызовов не роняют каркас.
 */
async function initBridgeData() {
  const ready = await waitReady();
  if (!ready) {
    activateDemoMode();
    store.set('demo', true);
  }

  const prefs = await safe(() => call('get_prefs'), {});
  const theme = prefs?.theme === 'light' ? 'light' : 'dark';
  store.set('theme', theme);
  applyTheme(theme);

  const [projects, hardware, models] = await Promise.all([
    safe(() => call('list_projects'), { projects: [] }),
    safe(() => call('get_hardware'), null),
    safe(() => call('list_models'), { models: [] }),
  ]);

  store.patch({
    projects: projects?.projects ?? [],
    hardware,
    models: models?.models ?? [],
  });

  renderDeviceStatus(hardware);
}

/** Пробует вызвать bridge-метод, при любой ошибке возвращает fallback. */
async function safe(fn, fallback) {
  try {
    return await fn();
  } catch (e) {
    console.warn('[boot]', e?.message ?? e);
    return fallback;
  }
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
    call('set_theme', next).catch(() => {});
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
 * @param {object|null} hw - ответ get_hardware (cpu, cores, ram_*).
 */
function renderDeviceStatus(hw) {
  const status = document.getElementById('device-status');
  const label = document.getElementById('device-label');
  if (!status || !label) return;

  if (!hw) {
    status.dataset.state = 'unknown';
    label.textContent = isDemo() ? 'demo data' : 'Устройство…';
    return;
  }

  const parts = [];
  if (hw.cpu) parts.push(hw.cpu);
  else if (hw.cores) parts.push(`${hw.cores} ядер`);
  if (hw.ram_total_gb) parts.push(`${components.formatBytes(hw.ram_total_gb * 1024 ** 3)} RAM`);

  status.dataset.state = 'ok';
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
