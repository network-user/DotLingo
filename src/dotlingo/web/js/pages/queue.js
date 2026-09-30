/**
 * Очередь перевода по всем проектам: пауза, продолжение, отмена.
 */

import { call } from '../bridge.js';
import { confirmDialog, debounce, spinner } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';
import {
  button,
  el,
  emptyState,
  langDisplay,
  run,
  statusBadge,
  toast,
} from './common.js';

let generation = 0;
let unlisten = () => {};

router.registerPage('queue', {
  title: 'Очередь',
  subtitle: 'Готовые фрагменты сохраняются сразу, срок окончания не показывается',
  layout: 'wide',
  render(host) {
    const ticket = ++generation;
    unlisten();
    const refresh = debounce(() => {
      if (ticket === generation) paint(host, ticket);
    }, 400);
    unlisten = store.on('task_event', refresh);
    paint(host, ticket);
  },
  destroy() {
    generation += 1;
    unlisten();
    unlisten = () => {};
  },
});

async function paint(host, ticket) {
  if (!host.querySelector('.table-wrap') && !host.querySelector('.empty')) {
    host.replaceChildren(spinner());
  }
  const tasks = await run(() => call('listTasks'));
  if (ticket !== generation || !host.isConnected) return;
  if (tasks === undefined) {
    host.replaceChildren(emptyState({ iconName: 'error', title: 'Очередь не прочиталась' }));
    return;
  }
  if (!tasks.length) {
    host.replaceChildren(
      emptyState({
        iconName: 'list',
        title: 'Очередь пуста',
        text: 'Запуск перевода ставит сюда отдельную задачу на каждый документ и язык.',
      }),
    );
    return;
  }
  const body = el('tbody');
  for (const task of tasks) {
    const total = task.total || 0;
    const done = task.completed || 0;
    body.append(
      el('tr', {}, [
        el('td', {}, [
          el('div', { class: 'truncate', text: task.documentName || 'Документ' }),
          el('div', { class: 'tiny truncate', text: task.projectTitle || '' }),
        ]),
        el('td', { text: `${langDisplay(task.sourceLang)} → ${langDisplay(task.targetLang)}` }),
        el('td', {}, [statusBadge(task.status)]),
        el('td', { text: total ? `${done} / ${total}` : '0' }),
        el('td', {}, [
          task.error ? el('div', { class: 'tiny', text: task.error }) : el('span', { class: 'tiny', text: '' }),
        ]),
        el('td', {}, [el('div', { class: 'row-actions' }, actions(task, host, ticket))]),
      ]),
    );
  }
  host.replaceChildren(
    el('div', { class: 'table-wrap' }, [
      el('table', { class: 'table' }, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: 'Документ' }),
            el('th', { text: 'Направление' }),
            el('th', { text: 'Статус' }),
            el('th', { text: 'Фрагменты' }),
            el('th', { text: 'Сообщение' }),
            el('th', { text: '' }),
          ]),
        ]),
        body,
      ]),
    ]),
  );
}

function actions(task, host, ticket) {
  const nodes = [];
  const status = task.status;
  if (status === 'running' || status === 'queued') {
    nodes.push(button({
      label: 'Пауза',
      iconName: 'pause',
      size: 'sm',
      onClick: () => act('pauseTask', task.taskId, host, ticket),
    }));
  }
  if (status === 'paused' || status === 'interrupted' || status === 'failed') {
    nodes.push(button({
      label: 'Продолжить',
      iconName: 'play',
      size: 'sm',
      variant: 'primary',
      onClick: () => act('resumeTask', task.taskId, host, ticket),
    }));
  }
  if (status !== 'complete' && status !== 'cancelled') {
    nodes.push(button({
      label: 'Отмена',
      iconName: 'stop',
      size: 'sm',
      variant: 'danger',
      onClick: async () => {
        const yes = await confirmDialog({
          title: 'Отменить задачу?',
          text: 'Уже сохранённые фрагменты останутся. Задача получит статус «Отменено».',
          confirmLabel: 'Отменить задачу',
          danger: true,
        });
        if (!yes) return;
        act('cancelTask', task.taskId, host, ticket);
      },
    }));
  }
  return nodes;
}

async function act(method, taskId, host, ticket) {
  const done = await run(() => call(method, taskId));
  if (done === undefined) return;
  toast('Очередь обновлена', 'success');
  paint(host, ticket);
}
