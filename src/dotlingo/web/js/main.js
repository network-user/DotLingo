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
import {
  applyMetrics,
  clampPercent,
  SCALE_DEFAULT,
  TEXT_SCALE_MAX,
  TEXT_SCALE_MIN,
  UI_SCALE_MAX,
  UI_SCALE_MIN,
} from './appearance.js';

/** Порядок страниц для клавиш 1..8 (совпадает с навигацией в index.html). */
const PAGE_ORDER = ['chat', 'review', 'queue', 'history', 'convert', 'models', 'glossary', 'settings'];

const DEFAULT_PAGE = 'chat';
const THEME_LABEL = { dark: 'Тёмная', light: 'Светлая' };

/* -------------------------------------------------------------------------
 * Boot
 * ------------------------------------------------------------------------- */

let closeStarted = false;

function reduceMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    || document.documentElement.dataset.reduceMotion === 'true';
}

function dismissLaunch() {
  const launch = document.getElementById('launch');
  if (!launch || launch.classList.contains('is-closing')) return;
  if (reduceMotion()) {
    launch.hidden = true;
    return;
  }
  const hide = () => {
    if (closeStarted || launch.classList.contains('is-closing')) return;
    launch.hidden = true;
  };
  launch.addEventListener('animationend', (event) => {
    if (event.target === launch && event.animationName === 'launch-fade') hide();
  });
  // Дольше кадра заставки (0.92 с), чтобы запасной таймер не оборвал листание.
  setTimeout(hide, 1150);
}

/** Книга захлопывается, затем окно отпускается. Повторный вызов ничего не делает. */
function requestWindowClose() {
  void call('finishClose').catch(() => {});
}

function playClose() {
  if (closeStarted) return;
  closeStarted = true;
  if (reduceMotion()) {
    requestWindowClose();
    return;
  }
  const launch = document.getElementById('launch');
  if (!launch) {
    requestWindowClose();
    return;
  }
  launch.hidden = false;
  launch.classList.add('is-closing');
  const finish = () => {
    if (finish.done) return;
    finish.done = true;
    requestWindowClose();
  };
  const book = launch.querySelector('.launch__book');
  launch.addEventListener('animationend', (event) => {
    if (event.target === book && event.animationName === 'close-sit') finish();
  });
  // Запас, если animationend не придёт. Сам кадр close-sit длится 0.56 с.
  setTimeout(finish, 600);
}

window.DL = { playClose };

async function boot() {
  dismissLaunch();
  // Контракт для консольной отладки (и для страниц); push_event появится ниже.
  window.DL = { store, router, call, components, isDemo, playClose };

  try {
    wireShell();
    wireKeyboard();
    wirePushEvents();
    components.installSelectMenus();

    // Стартуем с тёмной темой до прихода prefs, чтобы не мигало.
    store.set('theme', 'dark');
    applyTheme('dark');

    // Сначала «Перевод»: в этом модуле же живёт колода проекта.
    try {
      await loadPage('chat');
    } catch (e) {
      console.error('[pages] не удалось открыть перевод', e);
    }
    router.showPage(DEFAULT_PAGE);
  } catch (error) {
    console.error('[boot]', error);
    showBootFailure(error);
  }

  // Остальные экраны подключаются до того, как по ним можно кликнуть без ожидания.
  void loadExtraPages();
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

/** Каждая страница грузится отдельно: сбой одной не гасит остальные. */
const PAGE_LOADERS = {
  chat: () => import('./pages/chat.js'),
  projects: () => import('./pages/projects.js'),
  documents: () => import('./pages/documents.js'),
  review: () => import('./pages/review.js'),
  queue: () => import('./pages/queue.js'),
  history: () => import('./pages/results.js'),
  convert: () => import('./pages/convert.js'),
  models: () => import('./pages/models.js'),
  glossary: () => import('./pages/glossary.js'),
  settings: () => import('./pages/settings.js'),
};

/** @type {Map<string, Promise<void>>} */
const pageLoads = new Map();

function loadPage(name) {
  if (router.pageNames().includes(name)) return Promise.resolve();
  const load = PAGE_LOADERS[name];
  if (!load) return Promise.reject(new Error(`Неизвестная страница «${name}».`));
  let pending = pageLoads.get(name);
  if (!pending) {
    pending = load()
      .then(() => {})
      .catch((error) => {
        pageLoads.delete(name);
        throw error;
      });
    pageLoads.set(name, pending);
  }
  return pending;
}

function loadExtraPages() {
  const pages = Object.keys(PAGE_LOADERS).map((name) => loadPage(name).catch((error) => {
    console.error(`[pages] ${name}`, error);
  }));
  const wizard = import('./pages/wizard.js').catch((error) => {
    console.error('[pages] мастер', error);
  });
  return Promise.all([...pages, wizard]);
}

/** Открыть страницу, дождавшись её модуля. */
async function openPage(name) {
  if (!router.pageNames().includes(name)) {
    try {
      await loadPage(name);
    } catch (error) {
      console.error(`[pages] ${name}`, error);
      showLoadError(name);
      return;
    }
  }
  router.showPage(name);
}

function showLoadError(name) {
  const host = document.getElementById('page-host');
  const title = document.getElementById('page-title');
  if (title) title.textContent = 'Страница не открылась';
  if (!host) return;
  host.replaceChildren();
  const box = document.createElement('div');
  box.className = 'boot';
  const heading = document.createElement('p');
  heading.className = 'boot__title';
  heading.textContent = 'Страница не открылась';
  const text = document.createElement('p');
  text.className = 'boot__text';
  text.textContent = 'Закройте окно и запустите DotLingo ещё раз.';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'btn btn--primary';
  retry.textContent = 'Повторить';
  retry.addEventListener('click', () => void openPage(name));
  box.append(heading, text, retry);
  host.append(box);
}

let pendingPage = '';

window.addEventListener('dl-page-missing', (event) => {
  const name = event.detail;
  if (!name || !PAGE_LOADERS[name]) return;
  pendingPage = name;
  loadPage(name)
    .then(() => {
      if (pendingPage === name) router.showPage(name);
    })
    .catch((error) => {
      console.error(`[pages] ${name}`, error);
      if (pendingPage === name) showLoadError(name);
    });
});

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
  const sidebarCollapsed = Boolean(prefs?.sidebar_collapsed);
  store.patch({
    theme,
    reduceMotion: Boolean(prefs?.reduce_motion),
    sidebarCollapsed,
    sidebarWidth: paneSize(prefs?.sidebar_width, SIDEBAR_RAIL, SIDEBAR_MAX, SIDEBAR_DEFAULT),
    chatListWidth: paneSize(prefs?.chat_list_width, 168, 1200, 240),
    chatListHeight: paneSize(prefs?.chat_list_height, 120, 800, 200),
    chatListHidden: Boolean(prefs?.chat_list_hidden),
    translateSource: prefs?.translate_source || 'auto',
    translateTarget: prefs?.translate_target || 'ru',
    translateSuffix: prefs?.translate_suffix || '',
    translateModel: prefs?.translate_model || '',
    translateContext: prefs?.translate_context || '',
    translateGlossary: prefs?.translate_glossary !== false,
    translateSurface: prefs?.translate_surface === 'text' ? 'text' : 'file',
    updateCheckEnabled: prefs?.update_check_enabled !== false,
    updateAutoPrompt: prefs?.update_auto_prompt !== false,
    translateReady: true,
    uiScale: clampPercent(prefs?.ui_scale, UI_SCALE_MIN, UI_SCALE_MAX, SCALE_DEFAULT),
    textScale: clampPercent(prefs?.text_scale, TEXT_SCALE_MIN, TEXT_SCALE_MAX, SCALE_DEFAULT),
  });
  applySidebar(sidebarCollapsed);
  applyTheme(theme);
  applyMetrics(store.get('uiScale'), store.get('textScale'));
  document.documentElement.dataset.reduceMotion = prefs?.reduce_motion ? 'true' : 'false';
  if (prefs?.reduce_motion) dismissLaunch();

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
    const status = payload?.task?.status;
    const name = payload?.documentName || 'Документ';
    if (status === 'failed') {
      components.toast(
        payload?.task?.message || `${name}: перевод остановился с ошибкой`,
        'error',
        7000,
      );
    } else if (status === 'complete') {
      components.toast(`${name}: перевод сохранён`, 'success');
    }
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
  exports_ready(payload) {
    store.emit('exports_ready', payload);
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
  runtime_install_progress(payload) {
    store.emit('runtime_install_progress', payload);
  },
  runtime_install_done(payload) {
    store.emit('runtime_install_done', payload);
    components.toast(
      payload?.ok ? 'Сборка llama.cpp установлена.' : (payload?.error || 'Не удалось поставить runtime.'),
      payload?.ok ? 'success' : 'error',
    );
  },
  convert_progress(payload) {
    store.emit('convert_progress', payload);
  },
  convert_done(payload) {
    store.emit('convert_done', payload);
  },
  update_status(payload) {
    store.set('updateStatus', payload);
    store.emit('update_status', payload);
    if (!payload || payload.busy) return;
    if (payload.restartRequired) {
      components.toast(
        payload.message || 'Обновление применено. Перезапустите DotLingo.',
        'success',
        8000,
      );
      return;
    }
    if (payload.prompt === false) return;
    if (payload.needsConfirmDirty) {
      components.toast(
        payload.message || 'В клоне есть локальные правки. Подтвердите обновление в настройках.',
        'warning',
        8000,
      );
      return;
    }
    if (payload.available) {
      components.toast(
        payload.message || 'На ветке main есть новая версия. Откройте Настройки, чтобы обновить.',
        'info',
        8000,
      );
      return;
    }
    if (
      payload.notify
      && payload.ok === false
      && payload.message
      && !String(payload.message).toLowerCase().includes('отмен')
    ) {
      components.toast(payload.message, 'error', 7000);
    }
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

  document.getElementById('sidebar-toggle')?.addEventListener('click', () => {
    const collapsed = document.documentElement.dataset.sidebar !== 'collapsed';
    setSidebarCollapsed(collapsed);
  });
  applySidebar(false);
  bindSidebarResize();

  // Первичное состояние индикатора устройства.
  renderDeviceStatus(null);
  const footer = document.querySelector('.sidebar__footer');
  if (footer) mountDownloadDock(footer);

  document.getElementById('active-project')?.addEventListener('click', () => {
    const project = store.get('activeProject');
    if (project?.id && router.currentPage() === 'chat') {
      window.dispatchEvent(new CustomEvent('dl-focus-project', { detail: project.id }));
      return;
    }
    if (window.DL) window.DL.focusProjectId = project?.id || '';
    void openPage('chat');
  });
  store.subscribe((key) => {
    if (key === 'activeProject') renderActiveProject();
  });
  renderActiveProject();
}

function paneSize(value, low, high, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(high, Math.max(low, Math.round(number)));
}

const SIDEBAR_MAX = 480;
const SIDEBAR_DEFAULT = 252;
const SIDEBAR_RAIL = 80;
// Ниже этой ширины имя вкладки уже не читается: колонка остаётся рельсом значков.
const SIDEBAR_TEXT_MIN = 82;

function sidebarLimit() {
  const room = Math.max(SIDEBAR_RAIL, window.innerWidth - 360);
  return Math.min(SIDEBAR_MAX, room);
}

function clampSidebar(width) {
  return Math.min(sidebarLimit(), Math.max(SIDEBAR_RAIL, Math.round(width)));
}

function sidebarOpenWidth() {
  const limit = sidebarLimit();
  const width = paneSize(store.get('sidebarWidth'), SIDEBAR_RAIL, limit, SIDEBAR_DEFAULT);
  if (width < SIDEBAR_TEXT_MIN) return Math.min(limit, SIDEBAR_DEFAULT);
  return width;
}

function paintSidebarWidth(width) {
  const app = document.getElementById('app');
  if (!app) return;
  app.style.setProperty('--sidebar-w', `${width}px`);
  syncSidebarHandle(width);
}

function syncSidebarHandle(width) {
  const handle = document.getElementById('sidebar-resize');
  if (!handle) return;
  handle.ariaValueMin = String(SIDEBAR_RAIL);
  handle.ariaValueMax = String(sidebarLimit());
  handle.ariaValueNow = String(width);
}

function collapsedNow() {
  return document.documentElement.dataset.sidebar === 'collapsed';
}

function setSidebarMode(collapsed) {
  document.documentElement.dataset.sidebar = collapsed ? 'collapsed' : 'open';
  const toggle = document.getElementById('sidebar-toggle');
  if (toggle) {
    const label = collapsed ? 'Показать вкладки' : 'Скрыть вкладки';
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    toggle.title = label;
    toggle.setAttribute('aria-label', label);
  }
  document.querySelectorAll('.nav-item').forEach((button) => {
    const name = button.querySelector('.nav-item__name')?.textContent?.trim()
      || button.querySelector('.nav-item__label')?.textContent?.trim()
      || '';
    const key = button.getAttribute('aria-keyshortcuts') || '';
    if (collapsed && name) button.title = key ? `${name}. Клавиша ${key}` : name;
    else button.removeAttribute('title');
  });
}

function applySidebar(collapsed) {
  setSidebarMode(collapsed);
  paintSidebarWidth(collapsed ? SIDEBAR_RAIL : sidebarOpenWidth());
}

function rememberSidebar(width) {
  store.set('sidebarCollapsed', false);
  store.set('sidebarWidth', width);
  applySidebar(false);
  call('setPreferences', { sidebar_collapsed: false, sidebar_width: width }).catch(() => {});
}

function collapseSidebar() {
  applySidebar(true);
  store.set('sidebarCollapsed', true);
  call('setPreferences', { sidebar_collapsed: true }).catch(() => {});
}

function setSidebarCollapsed(collapsed) {
  if (collapsed) collapseSidebar();
  else {
    store.set('sidebarCollapsed', false);
    applySidebar(false);
    call('setPreferences', { sidebar_collapsed: false }).catch(() => {});
  }
}

function commitSidebar(width) {
  if (width < SIDEBAR_TEXT_MIN) collapseSidebar();
  else rememberSidebar(width);
}

function bindSidebarResize() {
  const handle = document.getElementById('sidebar-resize');
  const app = document.getElementById('app');
  if (!handle || !app) return;
  let drag = null;

  const endDrag = (event) => {
    if (event && drag && event.pointerId !== drag.pointerId) return;
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', endDrag);
    window.removeEventListener('pointercancel', endDrag);
    if (!drag) return;
    const moved = drag.moved;
    const width = Number(handle.ariaValueNow);
    drag = null;
    handle.classList.remove('is-dragging');
    document.documentElement.classList.remove('is-sidebar-resizing');
    document.body.classList.remove('is-resizing');
    if (!moved || !Number.isFinite(width)) return;
    commitSidebar(width);
  };

  const onPointerMove = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.moved = true;
    const size = clampSidebar(drag.startWidth + (event.clientX - drag.startX));
    const atRail = size < SIDEBAR_TEXT_MIN;
    if (atRail !== collapsedNow()) setSidebarMode(atRail);
    paintSidebarWidth(size);
  };

  handle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || drag) return;
    event.preventDefault();
    const sidebar = document.querySelector('.sidebar');
    const openWidth = collapsedNow() ? SIDEBAR_RAIL : sidebarOpenWidth();
    drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: sidebar?.getBoundingClientRect().width || openWidth,
      moved: false,
    };
    handle.classList.add('is-dragging');
    document.documentElement.classList.add('is-sidebar-resizing');
    document.body.classList.add('is-resizing');
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
    try {
      handle.setPointerCapture(event.pointerId);
    } catch {
      // Синтетический указатель не захватывается. Слушатели на окне всё равно ведут жест.
    }
  });

  handle.addEventListener('dblclick', () => {
    rememberSidebar(SIDEBAR_DEFAULT);
  });

  handle.addEventListener('keydown', (event) => {
    const grow = event.key === 'ArrowRight';
    const shrink = event.key === 'ArrowLeft';
    if (!grow && !shrink && event.key !== 'Home') return;
    event.preventDefault();
    if (event.key === 'Home') {
      rememberSidebar(SIDEBAR_DEFAULT);
      return;
    }
    const current = collapsedNow() ? SIDEBAR_RAIL : sidebarOpenWidth();
    commitSidebar(clampSidebar(current + (grow ? 16 : -16)));
  });

  window.addEventListener('resize', () => {
    if (drag) return;
    paintSidebarWidth(collapsedNow() ? SIDEBAR_RAIL : sidebarOpenWidth());
  });
}

function renderActiveProject() {
  const label = document.getElementById('active-project-label');
  const button = document.getElementById('active-project');
  if (!label) return;
  const project = store.get('activeProject');
  const kicker = button?.querySelector('.active-project__kicker');
  if (project?.title) {
    if (kicker) kicker.textContent = 'Проект';
    label.textContent = project.title;
    if (button) button.title = `Проект «${project.title}». Открыть.`;
  } else {
    if (kicker) kicker.textContent = 'Проект';
    label.textContent = 'Выбрать или создать';
    if (button) button.title = 'Проект не выбран. Нажмите, чтобы создать или открыть.';
  }
}

/** Применяет тему к <html data-theme> и обновляет кнопку-переключатель. */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;

  const slot = document.querySelector('#theme-toggle .theme-toggle__icon');
  if (slot) slot.replaceChildren(icon(theme === 'dark' ? 'moon' : 'sun'));

  const label = document.getElementById('theme-label');
  const toggle = document.getElementById('theme-toggle');
  const current = THEME_LABEL[theme] || theme;
  const other = theme === 'dark' ? 'светлую' : 'тёмную';
  if (label) label.textContent = `${current} тема`;
  if (toggle) toggle.title = `Сейчас ${current.toLowerCase()}. Переключить на ${other}.`;
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
    status.title = label.textContent;
    return;
  }

  const parts = [];
  if (hw.cpuThreads) parts.push(`${hw.cpuThreads} потоков CPU`);
  if (hw.ramTotalGb) parts.push(`${components.formatBytes(hw.ramTotalGb * 1024 ** 3)} RAM`);
  if (hw.profile) parts.push(hw.profile);

  status.dataset.state = hw.llamaRuntimeAvailable ? 'ok' : 'warn';
  label.textContent = parts.join(' · ') || 'Готово';
  status.title = hw.llamaRuntimeAvailable
    ? 'Компьютер готов к переводу'
    : 'Программа перевода на этом компьютере не найдена';
}

/* -------------------------------------------------------------------------
 * Клавиатура: 1..8 и Ctrl+1..8, Esc для модалок
 * ------------------------------------------------------------------------- */

function typingTarget(target) {
  if (!(target instanceof Element)) return false;
  const field = target.closest('input, textarea, select, [contenteditable="true"]');
  if (!field) return false;
  if (field instanceof HTMLInputElement) {
    const inert = ['button', 'checkbox', 'radio', 'range', 'submit', 'reset', 'file', 'color'];
    if (inert.includes(field.type)) return false;
  }
  return true;
}

function pageIndexFromKey(event) {
  const key = event.key;
  if (key.length !== 1 || key < '1' || key > '8') return null;
  const index = Number(key) - 1;
  if (index >= PAGE_ORDER.length) return null;
  return index;
}

/** Цифра без Ctrl не уводит со страницы, пока человек печатает или открыт слой. */
function bareDigitBlocked(event) {
  if (event.ctrlKey) return false;
  if (event.repeat || event.isComposing) return true;
  if (typingTarget(event.target)) return true;
  if (document.body.classList.contains('has-modal')) return true;
  return Boolean(document.querySelector('.setup, .select-menu'));
}

function wireKeyboard() {
  document.addEventListener('keydown', (e) => {
    // Esc закрывает верхнюю модалку.
    if (e.key === 'Escape') {
      components.handleEscape();
      return;
    }
    if (e.altKey || e.shiftKey || e.metaKey) return;
    const index = pageIndexFromKey(e);
    if (index == null || bareDigitBlocked(e)) return;
    e.preventDefault();
    void openPage(PAGE_ORDER[index]);
  });
}

/* -------------------------------------------------------------------------
 * Запуск
 * ------------------------------------------------------------------------- */

boot();
