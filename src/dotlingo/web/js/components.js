/**
 * Фабрики DOM-компонентов. Всё строится через createElement/textContent,
 * без innerHTML для пользовательских данных (XSS-безопасность).
 * Исключение - статические SVG-иконки из icons.js.
 */

import { icon } from './icons.js';

/* -------------------------------------------------------------------------
 * Базовый el()
 * ------------------------------------------------------------------------- */

/**
 * Создать DOM-элемент.
 * @param {string} tag - имя тега.
 * @param {object} [attrs] - атрибуты/свойства: class, text, html(нет),
 *   data-*, on-события (onClick и т.п.), любые DOM-свойства.
 * @param {(Node|string)[]} [children] - дочерние узлы и строки.
 * @returns {HTMLElement}
 */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null) continue;
    if (key === 'class') {
      node.className = value;
    } else if (key === 'text') {
      node.textContent = value;
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(node.style, value);
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'dataset' && typeof value === 'object') {
      Object.assign(node.dataset, value);
    } else if (key in node) {
      node[key] = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of children) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

/* -------------------------------------------------------------------------
 * Кнопка
 * ------------------------------------------------------------------------- */

/**
 * Кнопка.
 * @param {object} opts
 * @param {string} [opts.label] - текст.
 * @param {string} [opts.iconName] - имя иконки (перед текстом).
 * @param {'primary'|'ghost'|'danger'} [opts.variant]
 * @param {'sm'|'lg'} [opts.size]
 * @param {() => void} [opts.onClick]
 * @param {boolean} [opts.disabled]
 * @param {string} [opts.title]
 * @returns {HTMLButtonElement}
 */
export function button(opts = {}) {
  const { label, iconName, variant = 'ghost', size, onClick, disabled, title } = opts;
  const btn = el('button', {
    class: ['btn', variant !== 'ghost' ? `btn--${variant}` : '', size ? `btn--${size}` : '']
      .filter(Boolean)
      .join(' '),
    type: 'button',
    title: title ?? '',
    disabled: disabled ?? false,
    onClick,
  });
  if (iconName) btn.append(icon(iconName));
  if (label) btn.append(el('span', { class: 'btn__label', text: label }));
  return btn;
}

/* -------------------------------------------------------------------------
 * Модалки
 * ------------------------------------------------------------------------- */

/** @type {(() => void)[]} стек закрытия для глобального Esc. */
const escStack = [];

/** Обработчик глобального Esc (вешается один раз из main.js). */
export function handleEscape() {
  const close = escStack[escStack.length - 1];
  if (close) close();
}

/**
 * Открыть модалку.
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} [opts.subtitle]
 * @param {(body: HTMLElement) => void} [opts.render] - содержимое тела.
 * @param {Node[]} [opts.body] - готовые узлы тела.
 * @param {Array<{label: string, variant?: string, onClick?: () => void, autofocus?: boolean}>} [opts.actions]
 * @param {boolean} [opts.dismissable=true] - закрывать по Esc/клику по фону.
 * @param {() => void} [opts.onClose] - после закрытия.
 * @returns {{ close: () => void, root: HTMLElement, body: HTMLElement }}
 */
export function modal(opts = {}) {
  const {
    title,
    subtitle,
    render,
    body,
    actions = [],
    dismissable = true,
    onClose,
  } = opts;

  const root = document.getElementById('modal-root');
  const bodyEl = el('div', { class: 'modal__body' });

  const close = () => {
    if (!scrim.isConnected) return;
    escStack.splice(escStack.indexOf(close), 1);
    scrim.classList.add('modal-scrim--closing');
    scrim.addEventListener('animationend', () => {
      scrim.remove();
      if (!root.childElementCount) document.body.classList.remove('has-modal');
      onClose?.();
    }, { once: true });
  };
  const requestClose = () => {
    if (dismissable) close();
  };

  const header = el('header', { class: 'modal__header' }, [
    el('div', {}, [
      el('h2', { class: 'modal__title', text: title }),
      subtitle ? el('p', { class: 'modal__subtitle', text: subtitle }) : null,
    ]),
    el('button', {
      class: 'modal__close',
      type: 'button',
      title: 'Закрыть',
      'aria-label': 'Закрыть',
      onClick: requestClose,
    }, [icon('close')]),
  ]);

  const footer = actions.length
    ? el('footer', { class: 'modal__footer' },
        actions.map((a) =>
          button({
            label: a.label,
            variant: a.variant,
            onClick: () => a.onClick?.(),
          })
        )
      )
    : null;

  const scrim = el('div', { class: 'modal-scrim', onClick: requestClose }, [
    el('div', {
      class: 'modal',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': title,
      onClick: (e) => e.stopPropagation(),
    }, [header, bodyEl, footer].filter(Boolean)),
  ]);

  if (render) render(bodyEl);
  if (body) bodyEl.append(...body);

  root.appendChild(scrim);
  document.body.classList.add('has-modal');
  escStack.push(close);

  // Фокус внутрь модалки, чтобы Esc и Tab работали предсказуемо.
  const focusTarget = footer?.querySelector('button') ?? scrim.querySelector('.modal__close');
  focusTarget?.focus();

  return { close, root: scrim, body: bodyEl };
}

/**
 * Диалог подтверждения (обещание).
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} [opts.text]
 * @param {string} [opts.confirmLabel='Подтвердить']
 * @param {string} [opts.cancelLabel='Отмена']
 * @param {boolean} [opts.danger=false] - акцент на отмену действия.
 * @returns {Promise<boolean>} true при подтверждении.
 */
export function confirmDialog(opts = {}) {
  const {
    title,
    text,
    confirmLabel = 'Подтвердить',
    cancelLabel = 'Отмена',
    danger = false,
  } = opts;
  return new Promise((resolve) => {
    let result = false;
    const dialog = modal({
      title,
      subtitle: text,
      actions: [
        { label: cancelLabel, onClick: () => dialog.close() },
        {
          label: confirmLabel,
          variant: danger ? 'danger' : 'primary',
          onClick: () => {
            result = true;
            dialog.close();
          },
        },
      ],
      onClose: () => resolve(result),
    });
  });
}

/* -------------------------------------------------------------------------
 * Тосты
 * ------------------------------------------------------------------------- */

/** Показать тост.
 * @param {string} message
 * @param {'info'|'success'|'error'|'warning'} [type='info']
 * @param {number} [duration=4000] - мс до авто-скрытия (0 - не скрывать).
 */
export function toast(message, type = 'info', duration = 4000) {
  const host = document.getElementById('toasts');
  if (!host) return;

  const iconName = { info: 'info', success: 'check', error: 'error', warning: 'warning' }[type] ?? 'info';
  const node = el('div', { class: `toast toast--${type}`, role: 'status' }, [
    el('span', { class: 'toast__icon' }, [icon(iconName)]),
    el('div', { class: 'toast__message', text: message }),
    el('button', {
      class: 'toast__close',
      type: 'button',
      'aria-label': 'Закрыть',
      onClick: () => hide(),
    }, [icon('close')]),
  ]);

  let timer = 0;
  const hide = () => {
    clearTimeout(timer);
    if (!node.isConnected) return;
    node.classList.add('toast--closing');
    node.addEventListener('animationend', () => node.remove(), { once: true });
  };

  host.appendChild(node);
  if (duration > 0) timer = setTimeout(hide, duration);
  return hide;
}

/* -------------------------------------------------------------------------
 * Прогресс, спиннер, пустые состояния
 * ------------------------------------------------------------------------- */

/**
 * Прогрессбар.
 * @param {number} [value=0] - 0..1; null - неопределённый.
 * @returns {{ root: HTMLElement, set: (value: number|null) => void }}
 */
export function progressBar(value = 0) {
  const bar = el('div', { class: 'progress__bar' });
  const root = el('div', { class: 'progress' }, [bar]);
  const api = {
    root,
    /** @param {number|null} v */
    set(v) {
      if (v == null) {
        bar.classList.add('progress__bar--indeterminate');
        bar.style.width = '';
      } else {
        bar.classList.remove('progress__bar--indeterminate');
        bar.style.width = `${Math.round(clamp01(v) * 100)}%`;
      }
    },
  };
  api.set(value);
  return api;
}

function clamp01(v) {
  return Math.min(1, Math.max(0, v));
}

/**
 * Спиннер.
 * @param {'sm'|'lg'} [size]
 */
export function spinner(size) {
  return el('div', { class: ['spinner', size ? `spinner--${size}` : ''].filter(Boolean).join(' ') });
}

/**
 * Пустое состояние.
 * @param {object} opts
 * @param {string} [opts.iconName='info']
 * @param {string} opts.title
 * @param {string} [opts.text]
 * @param {Node} [opts.action] - узел-кнопка под текстом.
 */
export function emptyState(opts = {}) {
  const { iconName = 'info', title, text, action } = opts;
  const node = el('div', { class: 'empty' }, [
    el('div', { class: 'empty__icon' }, [icon(iconName)]),
    el('div', { class: 'empty__title', text: title }),
    text ? el('p', { class: 'empty__text', text }) : null,
    action ? el('div', { class: 'empty__action' }, [action]) : null,
  ].filter(Boolean));
  return node;
}

/* -------------------------------------------------------------------------
 * Утилиты
 * ------------------------------------------------------------------------- */

/**
 * Форматирование размера файла.
 * @param {number} bytes
 * @param {string} [lang='ru'] - локаль.
 * @returns {string} например «4,1 ГБ».
 */
export function formatBytes(bytes, lang = 'ru') {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes === 0) return '0 Б';
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** i;
  const digits = value >= 100 || i === 0 ? 0 : 1;
  const formatted = new Intl.NumberFormat(lang, {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(value);
  return `${formatted} ${units[i]}`;
}

/**
 * debounce.
 * @param {(...args: unknown[]) => void} fn
 * @param {number} [ms=200]
 */
export function debounce(fn, ms = 200) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/**
 * Чип.
 * @param {object} opts
 * @param {string} opts.label
 * @param {string} [opts.iconName]
 * @param {() => void} [opts.onRemove]
 * @param {() => void} [opts.onClick]
 */
export function chip(opts = {}) {
  const { label, iconName, onRemove, onClick } = opts;
  const node = el('span', {
    class: ['chip', onRemove ? 'chip--removable' : '', onClick ? 'chip--button' : '']
      .filter(Boolean)
      .join(' '),
    onClick,
  });
  if (iconName) node.append(icon(iconName));
  node.append(el('span', { class: 'chip__label', text: label }));
  if (onRemove) {
    node.append(
      el('button', {
        class: 'chip__remove',
        type: 'button',
        'aria-label': `Убрать «${label}»`,
        onClick: (e) => {
          e.stopPropagation();
          onRemove();
        },
      }, [icon('close')])
    );
  }
  return node;
}

/**
 * Badge (статусный значок).
 * @param {object} opts
 * @param {string} opts.label
 * @param {'success'|'error'|'warning'|'muted'} [opts.tone='muted']
 * @param {boolean} [opts.dot=true] - точка перед текстом.
 */
export function badge(opts = {}) {
  const { label, tone = 'muted', dot = true } = opts;
  return el('span', { class: `badge badge--${tone}` }, [
    dot ? el('span', { class: 'badge__dot' }) : null,
    el('span', { text: label }),
  ].filter(Boolean));
}

export { icon };
