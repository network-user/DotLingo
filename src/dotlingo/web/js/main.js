/**
 * Точка входа: boot, роутер, тема, горячие клавиши.
 * Страниц в реестре пока нет - придет волна 2 (другие агенты).
 */

import { call, isDemo, activateDemoMode, waitReady } from './bridge.js';
import * as store from './store.js';
import * as router from './router.js';
import * as components from './components.js';
import { icon } from './icons.js';
import './pages/events.js';
import './pages/projects.js';
import './pages/documents.js';
import './pages/review.js';
import './pages/queue.js';
import './pages/models.js';
import './pages/glossary.js';
import './pages/settings.js';

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

  const pushEvent = window.DL?.push_event;
  window.DL = { store, router, call, components, isDemo, push_event: pushEvent };
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

  const prefs = await safe(() => call('getPreferences'), {});
  const theme = prefs?.theme === 'light' ? 'light' : 'dark';
  store.set('theme', theme);
  applyTheme(theme);
  applyMotion(Boolean(prefs?.reduce_motion));

  const [projects, hardware, models, active, languages] = await Promise.all([
    safe(() => call('listProjects'), []),
    safe(() => call('getHardware'), null),
    safe(() => call('listModels'), { models: [], recommendation: null }),
    safe(() => call('getActiveProject'), null),
    safe(() => call('listLanguages'), null),
  ]);

  store.patch({
    projects: projects || [],
    hardware,
    models: models?.models || [],
    recommendation: models?.recommendation || null,
    activeProject: active,
    languages,
    reduceMotion: Boolean(prefs?.reduce_motion),
  });

  renderDeviceStatus(hardware);
  renderActiveProject(active);
  if (!isDemo() && prefs && prefs.setup_seen === false) welcome();
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

  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    const next = store.get('theme') === 'dark' ? 'light' : 'dark';
    store.set('theme', next);
    call('setPreferences', { theme: next }).catch(() => {});
  });

  document.getElementById('active-project')?.addEventListener('click', () => {
    router.showPage('projects');
  });

  store.watch('theme', applyTheme);
  store.watch('hardware', renderDeviceStatus);
  store.watch('activeProject', renderActiveProject);
  renderDeviceStatus(null);
  renderActiveProject(null);
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

function applyMotion(enabled) {
  document.documentElement.dataset.reduceMotion = enabled ? 'true' : 'false';
}

function renderActiveProject(project) {
  const label = document.getElementById('active-project-label');
  if (!label) return;
  label.textContent = project?.title || 'Не выбран';
}

function welcome() {
  let marked = false;
  const mark = () => {
    if (marked) return;
    marked = true;
    call('setPreferences', { setup_seen: true }).catch(() => {});
  };
  const dialog = components.modal({
    title: 'Локальный перевод',
    subtitle: 'Оригинал остаётся копией. Модель скачивается только когда вы подтвердите файл.',
    onClose: mark,
    render: (body) => {
      body.append(components.el('div', { class: 'stack stack--lg' }, [
        components.el('p', { class: 'muted', text: 'Создайте проект, добавьте документ и выберите модель под память компьютера.' }),
        components.el('p', { class: 'muted', text: 'Перевод идёт очередью, без оценки срока. Проверка стоит рядом с исходным текстом.' }),
      ]));
    },
    actions: [
      {
        label: 'Начать',
        variant: 'primary',
        onClick: () => {
          mark();
          dialog.close();
        },
      },
    ],
  });
}

/**
 * Индикатор устройства в подвале боковой панели.
 * @param {object|null} hw
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
  if (hw.profile) parts.push(hw.profile);
  if (hw.ramTotalGb) parts.push(`${hw.ramTotalGb} ГБ RAM`);
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
