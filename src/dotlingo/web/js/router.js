/**
 * Hash-free SPA-роутер: showPage(name), реестр страниц, fade/slide 180ms.
 */

import { closeHelpMarks, helpMark } from './components.js';
import { icon } from './icons.js';
import * as store from './store.js';

const pages = new Map();

/** Заголовки по умолчанию для известных имён страниц (волна 2 может
 * задать свои через def.title). */
const PAGE_TITLES = {
  chat: 'Диалог',
  projects: 'Проекты',
  documents: 'Документы',
  review: 'Проверка',
  queue: 'Очередь',
  history: 'История',
  models: 'Модели',
  glossary: 'Глоссарий',
  settings: 'Настройки',
};

/** @type {string} текущая страница ('' - пока не выбрана). */
let current = '';

/**
 * Зарегистрировать страницу.
 * @param {string} name - уникальный ключ (совпадает с data-page в навигации).
 * @param {object} def
 * @param {string} def.title - заголовок в шапке.
 * @param {string} [def.subtitle] - короткая строка под заголовком.
 * @param {string} [def.help] - пояснение вкладки на знаке «?» у заголовка.
 * @param {(host: HTMLElement, ctx: object) => void} def.render -
 *   строит DOM страницы внутри host (вызывается при каждом показе).
 * @param {(host: HTMLElement) => void} [def.destroy] -
 *   очистка при уходе со страницы (снять таймеры/подписки).
 * @param {(host: HTMLElement) => HTMLElement[]} [def.actions] -
 *   кнопки для слота действий в шапке; вызывается после render.
 */
export function registerPage(name, def) {
  if (pages.has(name)) {
    console.warn(`[router] страница ${name} уже зарегистрирована, перезаписываю`);
  }
  pages.set(name, def);
}

/** Список имён зарегистрированных страниц. */
export function pageNames() {
  return [...pages.keys()];
}

/** Имя текущей страницы. */
export function currentPage() {
  return current;
}

/**
 * Рабочий режим «Перевод».
 * focus прячет левые разделы, sections возвращает их, не записывая это в настройки.
 * Пустая строка выключает режим на остальных страницах.
 * @param {'focus'|'sections'|''} mode
 */
export function setWorkChrome(mode) {
  const root = document.documentElement;
  if (mode === 'focus' || mode === 'sections') root.dataset.work = mode;
  else delete root.dataset.work;
  const hidden = root.dataset.work === 'focus';
  const sidebar = document.querySelector('.sidebar');
  const handle = document.getElementById('sidebar-resize');
  if (sidebar) {
    sidebar.toggleAttribute('inert', hidden);
    sidebar.setAttribute('aria-hidden', hidden ? 'true' : 'false');
  }
  if (handle) {
    handle.toggleAttribute('inert', hidden);
    handle.tabIndex = hidden ? -1 : 0;
  }
  window.dispatchEvent(new CustomEvent('dl-work-chrome', { detail: root.dataset.work || '' }));
}

/**
 * Переключить страницу. Если модуль ещё не подключился, экран не подменяется
 * заглушкой: оболочка догружает модуль и вызывает показ ещё раз.
 * @param {string} name
 */
export function showPage(name) {
  if (name === 'documents') name = 'chat';
  const host = document.getElementById('page-host');
  if (!host) return;
  const def = pages.get(name);
  if (!def) {
    // Не подменять экран текстом-заглушкой. Оболочка догрузит модуль и вызовет снова.
    window.dispatchEvent(new CustomEvent('dl-page-missing', { detail: name }));
    return;
  }
  if (name === current) return;

  const prev = pages.get(current);
  if (prev?.destroy) {
    try {
      prev.destroy(host);
    } catch (e) {
      console.error(`[router] destroy «${current}»`, e);
    }
  }

  current = name;
  store.set('page', name);
  setWorkChrome(name === 'chat' ? 'focus' : '');
  host.replaceChildren();

  const title = document.getElementById('page-title');
  const subtitle = document.getElementById('page-subtitle');
  const actions = document.getElementById('page-actions');

  const page = document.createElement('div');
  page.className = def?.layout ? `page page--${def.layout}` : 'page';

  if (def) {
    if (title) title.textContent = def.title ?? PAGE_TITLES[name] ?? name;
    if (subtitle) {
      subtitle.textContent = def.subtitle ?? '';
      subtitle.hidden = !def.subtitle;
    }
    closeHelpMarks();
    const helpHost = document.getElementById('page-help');
    if (helpHost) {
      helpHost.replaceChildren();
      if (def.help) helpHost.append(helpMark(def.help));
      helpHost.hidden = !def.help;
    }
    actions.replaceChildren();
    try {
      def.render(page, { name });
      if (def.actions) actions.replaceChildren(...def.actions(page));
    } catch (e) {
      console.error(`[router] render «${name}»`, e);
      page.replaceChildren(
        stubMessage('Ошибка отрисовки страницы. Подробности в консоли.', 'error')
      );
    }
  }

  host.appendChild(page);
  host.scrollTop = 0;

  document.querySelectorAll('.nav-item[data-page]').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.page === name);
  });
}

/** Заглушка для незарегистрированной страницы. */
function stubMessage(text, iconName = 'globe') {
  const wrap = document.createElement('div');
  wrap.className = 'empty';
  wrap.dataset.stub = 'true';
  const iconBox = document.createElement('div');
  iconBox.className = 'empty__icon';
  iconBox.append(icon(iconName));
  const t = document.createElement('p');
  t.className = 'empty__title';
  t.textContent = text;
  wrap.append(iconBox, t);
  return wrap;
}
