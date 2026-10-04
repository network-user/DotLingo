/**
 * Страница «Очередь»: задачи всех проектов, группировка по проектам,
 * live-обновления по push-событиям, фильтры и поиск.
 */

import { tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import {
  el,
  button,
  badge,
  toast,
  emptyState,
  progressBar,
  spinner,
  debounce,
} from '../components.js';

/** Человекочитаемые статусы задач. */
const STATUS_LABELS = {
  queued: 'В очереди',
  running: 'Перевод',
  paused: 'Пауза',
  complete: 'Готово',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  interrupted: 'Прервано',
};

/** Тон бейджа по статусу. */
const STATUS_TONES = {
  queued: 'muted',
  running: 'muted',
  paused: 'warning',
  complete: 'success',
  failed: 'error',
  cancelled: 'muted',
  interrupted: 'warning',
};

const FILTER_ALL = 'all';
const FILTER_ACTIVE = 'active';
const FILTER_DONE = 'done';

const FILTER_LABELS = { [FILTER_ALL]: 'Все', [FILTER_ACTIVE]: 'Активные', [FILTER_DONE]: 'Завершённые' };

const ACTIVE_STATUSES = new Set(['queued', 'running', 'paused']);
const DONE_STATUSES = new Set(['complete', 'failed', 'cancelled', 'interrupted']);

/** Текущий фильтр. */
let filter = FILTER_ALL;
/** Поисковый запрос. */
let query = '';
/** Полный список задач последнего фетча. */
let allTasks = [];
/** Отписка task_event. */
let unsubTaskEvent = null;

/* -------------------------------------------------------------------------
 * Рендер
 * ------------------------------------------------------------------------- */

function render(host) {
  filter = FILTER_ALL;
  query = '';

  const searchInput = el('input', {
    class: 'input queue-search',
    type: 'search',
    placeholder: 'Поиск по имени документа…',
    onInput: debounce((e) => {
      query = e.target.value.trim().toLowerCase();
      renderList(host);
    }, 180),
  });

  host.append(
    el('p', { class: 'queue-note text-secondary' }, [
      'Прогресс считает готовые фрагменты. Время до конца не оценивается.',
    ]),
    el('div', { class: 'queue-toolbar row row--between row--wrap' }, [
      el('div', { class: 'row row--wrap' }, [
        filterChip(FILTER_ALL, host),
        filterChip(FILTER_ACTIVE, host),
        filterChip(FILTER_DONE, host),
      ]),
      searchInput,
    ]),
    el('div', { class: 'queue-list stack' }, [el('div', { class: 'page-loading' }, [spinner('lg')])])
  );

  unsubTaskEvent?.();
  unsubTaskEvent = store.on('task_event', (payload) => onTaskEvent(payload, host));

  void refresh(host);
}

/** Чип-фильтр. */
function filterChip(value, host) {
  const node = el('button', {
    class: `filter-chip${filter === value ? ' is-selected' : ''}`,
    type: 'button',
    dataset: { filter: value },
    onClick: () => {
      filter = value;
      host.querySelectorAll('.filter-chip').forEach((chipNode) => {
        chipNode.classList.toggle('is-selected', chipNode.dataset.filter === value);
      });
      renderList(host);
    },
  }, [el('span', { text: FILTER_LABELS[value] })]);
  return node;
}

/** Загружает задачи и перерисовывает список. */
async function refresh(host) {
  const [tasks, err] = await tryCall('listTasks');
  if (!host.isConnected) return;
  const list = host.querySelector('.queue-list');
  if (!list) return;

  if (err) {
    list.replaceChildren(
      emptyState({ iconName: 'error', title: 'Не удалось загрузить очередь', text: err.message })
    );
    return;
  }

  allTasks = tasks ?? [];
  renderList(host);
}

/** Фильтрует и рендерит список задач. */
function renderList(host) {
  const list = host.querySelector('.queue-list');
  if (!list) return;

  const visible = allTasks.filter((task) => {
    if (filter === FILTER_ACTIVE && !ACTIVE_STATUSES.has(task.status)) return false;
    if (filter === FILTER_DONE && !DONE_STATUSES.has(task.status)) return false;
    if (query && !(task.documentName || '').toLowerCase().includes(query)) return false;
    return true;
  });

  if (visible.length === 0) {
    const narrowed = allTasks.length > 0;
    list.replaceChildren(
      emptyState({
        iconName: 'list',
        title: narrowed ? 'Ничего не подошло' : 'Задач пока нет',
        text: narrowed
          ? 'Смените фильтр или очистите поиск.'
          : 'Их ставят на странице «Документы», кнопкой «Перевести».',
      })
    );
    return;
  }

  const groups = new Map();
  for (const task of visible) {
    const key = task.projectTitle || 'Проект';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(task);
  }

  const nodes = [];
  for (const [title, tasks] of groups) {
    nodes.push(el('h3', { class: 'queue-group__title', text: title }));
    for (const task of tasks) nodes.push(taskCard(task, host));
  }
  list.replaceChildren(...nodes);
}

/** Карточка задачи. */
function taskCard(task, host) {
  const statusBadge = badge({
    label: STATUS_LABELS[task.status] ?? task.status,
    tone: STATUS_TONES[task.status] ?? 'muted',
  });
  statusBadge.classList.add(`task-status--${task.status}`);

  const langFrom = task.sourceLang === 'auto' ? 'авто' : langLabel(task.sourceLang);
  const langTo = langLabel(task.targetLang);

  const bar = progressBar(task.total ? task.completed / task.total : 0);

  const liveFeed = el('div', { class: 'task-live', hidden: task.status !== 'running' }, [
    el('div', { class: 'task-live__label' }, [
      el('span', { class: 'task-live__pulse', 'aria-hidden': 'true' }),
      el('span', { class: 'text-tertiary', text: 'перевод идёт' }),
    ]),
    el('div', { class: 'task-live__pairs' }),
  ]);

  const actionsBox = el('div', { class: 'task-card__actions row' }, [
    isRunState(task) ? button({
      label: 'Пауза',
      variant: 'ghost',
      size: 'sm',
      iconName: 'pause',
      onClick: () => pauseTask(task.taskId, host),
    }) : null,
    isRunState(task) ? button({
      label: 'Отменить',
      variant: 'ghost',
      size: 'sm',
      iconName: 'stop',
      onClick: () => cancelTask(task.taskId, host),
    }) : null,
    canResume(task) ? button({
      label: 'Продолжить',
      variant: 'ghost',
      size: 'sm',
      iconName: 'play',
      onClick: () => resumeTask(task.taskId, host),
    }) : null,
  ]);

  return el('article', {
    class: `task-card panel panel--flat${task.status === 'running' ? ' is-running' : ''}`,
    dataset: { taskId: task.taskId },
  }, [
    el('div', { class: 'row row--between' }, [
      el('div', { class: 'task-card__head' }, [
        el('span', { class: 'task-card__doc ellipsis', text: task.documentName || 'Документ' }),
        el('span', {
          class: 'task-card__lang text-secondary',
          text: `${langFrom} → ${langTo}`,
        }),
        statusBadge,
      ]),
      actionsBox,
    ]),
    el('div', { class: 'task-card__progress row' }, [
      bar.root,
      el('span', {
        class: 'task-card__nums text-tertiary',
        text: `${task.completed}/${task.total}`,
      }),
    ]),
    liveFeed,
    task.error ? el('p', { class: 'task-card__error', text: task.error }) : null,
    task.modelId
      ? el('p', { class: 'task-card__model text-tertiary ellipsis', text: task.modelId })
      : null,
  ]);
}

/** Метка языка. */
function langLabel(code) {
  const languages = store.get('languages') || {};
  return languages[code] || code.toUpperCase();
}

/** running/queued - можно поставить на паузу или отменить. */
function isRunState(task) {
  return task.status === 'running' || task.status === 'queued';
}

/** paused/interrupted/failed/cancelled - можно продолжить. */
function canResume(task) {
  return ['paused', 'interrupted', 'failed', 'cancelled'].includes(task.status);
}

/* -------------------------------------------------------------------------
 * Действия над задачами
 * ------------------------------------------------------------------------- */

async function pauseTask(taskId, host) {
  const [, err] = await tryCall('pauseTask', taskId);
  if (err) {
    toast(err.message, 'error');
    return;
  }
  void refresh(host);
}

async function resumeTask(taskId, host) {
  const [, err] = await tryCall('resumeTask', taskId);
  if (err) {
    toast(err.message, 'error');
    return;
  }
  void refresh(host);
}

async function cancelTask(taskId, host) {
  const [, err] = await tryCall('cancelTask', taskId);
  if (err) {
    toast(err.message, 'error');
    return;
  }
  void refresh(host);
}

/* -------------------------------------------------------------------------
 * Live-обновления
 * ------------------------------------------------------------------------- */

/** Обновление одной карточки по push-событию. */
function onTaskEvent(payload, host) {
  const event = payload?.task ?? payload;
  const taskId = event?.task_id;
  if (!taskId) return;

  const task = allTasks.find((item) => item.taskId === taskId);
  if (task) {
    if (event.completed != null) task.completed = event.completed;
    if (event.total != null) task.total = event.total;
    if (event.status) task.status = event.status;
  }

  const card = host.querySelector(`.task-card[data-task-id="${CSS.escape(taskId)}"]`);
  if (!card) {
    void refresh(host);
    return;
  }

  if (event.status === 'complete' || event.status === 'failed') {
    void refresh(host);
    return;
  }

  updateCard(card, event);
  if (event.status === 'running' && event.current_source) {
    appendLivePair(card, event.current_source, event.current_translation || '');
  }
}

/** Добавить пару «оригинал → перевод» в живую ленту карточки. */
function appendLivePair(card, source, translation) {
  const pairs = card.querySelector('.task-live__pairs');
  if (!pairs) return;

  const clip = (text) => (text || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  const pair = el('div', { class: 'task-live__pair' }, [
    el('p', { class: 'task-live__source ellipsis', text: clip(source) }),
    el('span', { class: 'task-live__arrow', 'aria-hidden': 'true', text: '→' }),
    el('p', { class: 'task-live__translation ellipsis', text: clip(translation) || '…' }),
  ]);
  pairs.prepend(pair);
  while (pairs.children.length > 2) pairs.lastChild.remove();
}

/** Точечное обновление DOM карточки. */
function updateCard(card, event) {
  const completed = event.completed ?? 0;
  const total = event.total ?? 0;

  const nums = card.querySelector('.task-card__nums');
  if (nums) {
    const rate = Number(event.chars_per_sec);
    const pace = Number.isFinite(rate) && rate > 0 ? ` · ${Math.round(rate)} симв/с` : '';
    nums.textContent = `${completed}/${total}${pace}`;
  }
  const liveLabel = card.querySelector('.task-live .text-tertiary');
  if (liveLabel && event.device) liveLabel.textContent = event.device;

  const bar = card.querySelector('.progress__bar');
  if (bar && total > 0) {
    bar.style.width = `${Math.round(Math.min(1, completed / total) * 100)}%`;
  }

  if (event.status) {
    const statusNode = card.querySelector('.task-card__head .badge');
    if (statusNode) {
      statusNode.className = `badge badge--${STATUS_TONES[event.status] ?? 'muted'} task-status--${event.status}`;
      const label = statusNode.lastChild;
      if (label) label.textContent = STATUS_LABELS[event.status] ?? event.status;
    }
    card.classList.toggle('is-running', event.status === 'running');
    const live = card.querySelector('.task-live');
    if (live) live.hidden = event.status !== 'running';
  }
}

/* -------------------------------------------------------------------------
 * Действия шапки
 * ------------------------------------------------------------------------- */

function actions(host) {
  return [
    button({
      label: 'Обновить',
      variant: 'ghost',
      iconName: 'refresh',
      onClick: () => void refresh(host),
    }),
  ];
}

/* -------------------------------------------------------------------------
 * Жизненный цикл
 * ------------------------------------------------------------------------- */

function destroy() {
  unsubTaskEvent?.();
  unsubTaskEvent = null;
}

router.registerPage('queue', {
  title: 'Очередь',
  subtitle: 'Задачи по всем проектам',
  help: 'Счётчик показывает готовые фрагменты, не минуты. Пауза останавливает задачу, «Продолжить» возвращает её в работу. Отмена снимает задачу с очереди. Уже записанные фрагменты в документе остаются.',
  render,
  destroy,
  actions,
});
