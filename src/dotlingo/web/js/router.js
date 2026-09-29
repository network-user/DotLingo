/**
 * Hash-free SPA-роутер: showPage(name), реестр страниц, fade/slide 180ms.
 */

import { icon } from './icons.js';
import * as store from './store.js';

const pages = new Map();

/** Заголовки по умолчанию для известных имён страниц (волна 2 может
 * задать свои через def.title). */
const PAGE_TITLES = {
  projects: 'Проекты',
  documents: 'Документы',
  review: 'Проверка',
  queue: 'Очередь',
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
 * @param {string} [def.subtitle] - подзаголовок в шапке.
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
 * Переключить страницу. Если страница не зарегистрирована - заглушка
 * «каркас готов» (страницы добавит волна 2).
 * @param {string} name
 */
export function showPage(name) {
  if (name === current) return;

  const host = document.getElementById('page-host');
  if (!host) return;

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
  host.replaceChildren();

  const def = pages.get(name);
  const title = document.getElementById('page-title');
  const subtitle = document.getElementById('page-subtitle');
  const actions = document.getElementById('page-actions');

  const page = document.createElement('div');
  page.className = 'page';

  if (def) {
    if (title) title.textContent = def.title ?? PAGE_TITLES[name] ?? name;
    if (subtitle) {
      subtitle.textContent = def.subtitle ?? '';
      subtitle.hidden = !def.subtitle;
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
  } else {
    if (title) title.textContent = PAGE_TITLES[name] ?? 'DotLingo';
    if (subtitle) subtitle.hidden = true;
    actions.replaceChildren();
    page.append(stubMessage('Каркас готов. Страница появится в следующем обновлении.'));
  }

  host.appendChild(page);
  host.scrollTop = 0;

  // Появление: класс вешаем через двойной rAF, чтобы transition сработал
  // от исходного состояния (opacity 0 + translateY(10px)).
  requestAnimationFrame(() => {
    requestAnimationFrame(() => page.classList.add('is-in'));
  });

  document.querySelectorAll('.nav-item[data-page]').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.page === name);
  });
}

/** Заглушка для незарегистрированной страницы. */
function stubMessage(text, iconName = 'globe') {
  const wrap = document.createElement('div');
  wrap.className = 'empty';
  const iconBox = document.createElement('div');
  iconBox.className = 'empty__icon';
  iconBox.append(icon(iconName));
  const t = document.createElement('p');
  t.className = 'empty__title';
  t.textContent = text;
  wrap.append(iconBox, t);
  return wrap;
}
