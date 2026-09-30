/**
 * Общие куски страниц: вызов моста, подписи языков и статусов, поля форм.
 */

import { call } from '../bridge.js';
import { badge, button, el, emptyState, toast } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';

const STATUS = {
  queued: ['В очереди', 'muted'],
  running: ['Переводится', 'warning'],
  paused: ['Пауза', 'warning'],
  complete: ['Готово', 'success'],
  failed: ['Ошибка', 'error'],
  cancelled: ['Отменено', 'muted'],
  interrupted: ['Прервано', 'warning'],
};

const INSTALL = {
  installed: ['Установлена', 'success'],
  available: ['Можно загрузить', 'muted'],
  missing: ['Файл не найден', 'error'],
  unverified: ['Не проверена', 'warning'],
};

/** Выполнить вызов моста и показать ошибку. undefined означает сбой. */
export async function run(work) {
  try {
    return await work();
  } catch (error) {
    toast(error?.message || 'Не удалось выполнить действие', 'error', 6000);
    return undefined;
  }
}

export function langLabel(code) {
  const pack = store.get('languages');
  if (!code) return '';
  if (pack?.auto?.code === code) return pack.auto.label;
  const found = pack?.languages?.find((item) => item.code === code);
  return found?.label || code;
}

export function langDisplay(code) {
  if (!code || code === 'auto') return langLabel(code || 'auto');
  return `${langLabel(code)} · ${code}`;
}

export function statusBadge(status) {
  const [label, tone] = STATUS[status] || [status || 'неизвестно', 'muted'];
  return badge({ label, tone });
}

export function installBadge(state) {
  const [label, tone] = INSTALL[state] || [state || 'неизвестно', 'muted'];
  return badge({ label, tone });
}

export function formatWhen(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('ru', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export async function reloadProjects() {
  const projects = await call('listProjects');
  const active = await call('getActiveProject');
  store.patch({ projects: projects || [], activeProject: active });
  return { projects: projects || [], active };
}

export async function loadLanguages() {
  const pack = await call('listLanguages');
  store.set('languages', pack);
  return pack;
}

export async function loadModels() {
  const data = await call('listModels');
  store.patch({
    models: data?.models || [],
    recommendation: data?.recommendation || null,
  });
  return data;
}

/** Пустое состояние, если проект не открыт. Возвращает проект или null. */
export function requireProject(host, text) {
  const project = store.get('activeProject');
  if (project) return project;
  host.append(
    emptyState({
      iconName: 'folder',
      title: 'Нет открытого проекта',
      text,
      action: button({
        label: 'К проектам',
        variant: 'primary',
        onClick: () => router.showPage('projects'),
      }),
    }),
  );
  return null;
}

export function field(label, control, hint) {
  return el('div', { class: 'field' }, [
    el('span', { class: 'field__label', text: label }),
    control,
    hint ? el('span', { class: 'field__hint', text: hint }) : null,
  ]);
}

export function selectBox(options, value) {
  const node = el('select', { class: 'select' });
  for (const option of options) {
    const item = el('option', { value: option.value, text: option.label });
    if (option.value === value) item.selected = true;
    node.append(item);
  }
  return node;
}

/**
 * Сетка чекбоксов языков.
 * @param {Array<{code: string, label: string, disabled?: boolean, title?: string}>} codes
 * @param {Set<string>} selected
 */
export function languageChecks(codes, selected) {
  const inputs = [];
  const box = el('div', { class: 'checks' });
  for (const item of codes) {
    const input = el('input', {
      type: 'checkbox',
      value: item.code,
      checked: selected.has(item.code) && !item.disabled,
      disabled: Boolean(item.disabled),
    });
    inputs.push(input);
    box.append(
      el('label', { class: 'check', title: item.title || '' }, [
        input,
        el('span', { text: item.label }),
      ]),
    );
  }
  return {
    element: box,
    value: () => inputs.filter((input) => input.checked).map((input) => input.value),
  };
}

export function allLanguageOptions() {
  const pack = store.get('languages');
  return pack?.languages || [];
}

export { button, el, emptyState, toast };
