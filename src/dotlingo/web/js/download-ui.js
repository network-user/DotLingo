/**
 * Загрузка модели: окно можно скрыть, прогресс остаётся значком в подвале.
 * По окончании показывается окно, что вес проверен и можно работать.
 */

import { call } from './bridge.js';
import * as store from './store.js';
import * as router from './router.js';
import { el, modal, toast, progressBar, formatBytes } from './components.js';
import { icon } from './icons.js';
import { formatEta, formatSpeed, refreshCatalog } from './device.js';

/** @type {{ close: () => void }|null} */
let activeDialog = null;

/** @type {object|null} модель открытого или текущего скачивания */
let activeModel = null;

/** Не показывать «готово» дважды для одного завершения. */
let finishedId = '';

/**
 * Начать загрузку и открыть окно прогресса.
 * Повторный вызов во время той же загрузки только возвращает окно.
 * @param {object} model
 */
export async function downloadFlow(model) {
  const current = store.get('download');
  if (current?.active && current.modelId === model.id) {
    openDownloadDialog(model);
    return;
  }
  remember(model, { phase: 'starting', bytes: 0, total: model.sizeBytes || 0 });
  try {
    await call(model.market ? 'downloadMarketModel' : 'downloadModel', model.id);
  } catch (error) {
    if (error.code === 'busy') {
      openDownloadDialog(model);
      return;
    }
    clearDownload();
    toast(error.message, 'error');
    return;
  }
  openDownloadDialog(model);
}

/** Вернуть скрытое окно прогресса, не запуская загрузку заново. */
export function openDownloadDialog(model) {
  if (!model) return;
  if (activeDialog) return;
  activeModel = model;
  const bar = progressBar(null);
  const status = el('p', { class: 'download-status', text: 'Подготовка загрузки…' });
  const off = [];

  const dialog = modal({
    title: `Загрузка · ${model.name}`,
    closeTitle: 'Скрыть',
    body: [
      el('div', { class: 'stack' }, [
        el('p', {
          class: 'download-note',
          text: 'Окно можно скрыть: загрузка продолжится, а значок внизу панели покажет прогресс.',
        }),
        bar.root,
        status,
      ]),
    ],
    actions: [
      { label: 'Скрыть', onClick: () => dialog.close() },
      { label: 'Отменить', onClick: () => void cancel(status) },
    ],
    onClose: () => {
      off.forEach((stop) => stop());
      activeDialog = null;
      paintDock();
    },
  });
  activeDialog = dialog;
  paintDock();

  const cancelBtn = [...dialog.root.querySelectorAll('.modal__footer .btn')].at(-1);

  const apply = (payload) => {
    if (payload?.modelId !== model.id) return;
    paintProgress(bar, status, cancelBtn, payload);
  };
  off.push(store.on('download_progress', apply));
  const existing = store.get('download');
  if (existing?.modelId === model.id) apply(existing);
}

/**
 * Значок загрузки в подвале боковой панели.
 * @param {HTMLElement} footer
 */
export function mountDownloadDock(footer) {
  const buttonEl = el('button', {
    class: 'download-dock',
    type: 'button',
    hidden: true,
    id: 'download-dock',
  });
  buttonEl.append(
    el('span', { class: 'download-dock__icon', 'aria-hidden': 'true' }),
    el('span', { class: 'download-dock__copy' }, [
      el('span', { class: 'download-dock__name' }),
      el('span', { class: 'download-dock__meta' }),
    ]),
  );
  const glyph = buttonEl.querySelector('.download-dock__icon');
  glyph?.append(icon('download'));
  buttonEl.addEventListener('click', () => {
    const current = store.get('download');
    if (!current) return;
    if (current.error) {
      clearDownload();
      return;
    }
    const model = (store.get('models') || []).find((item) => item.id === current.modelId) || activeModel;
    if (model) openDownloadDialog(model);
  });
  footer.prepend(buttonEl);

  store.on('download_progress', (payload) => {
    if (!payload?.modelId) return;
    const known = (store.get('models') || []).find((item) => item.id === payload.modelId);
    remember(known || { id: payload.modelId, name: payload.modelId }, payload);
  });
  store.on('download_done', (payload) => {
    if (!payload?.modelId) return;
    const current = store.get('download');
    const name = current?.modelId === payload.modelId ? current.name : payload.modelId;
    const duringSetup = Boolean(document.querySelector('.setup'));
    activeDialog?.close();
    activeDialog = null;
    if (payload.ok) {
      clearDownload();
      void refreshCatalog().finally(() => {
        if (!duringSetup) announceReady(payload.modelId, name);
      });
      return;
    }
    store.set('download', {
      modelId: payload.modelId,
      name,
      active: false,
      error: payload.error || 'Загрузка не удалась.',
      phase: 'error',
      bytes: 0,
      total: 0,
    });
    toast(payload.error || 'Загрузка не удалась.', 'error');
    paintDock();
  });
  paintDock();
}

function remember(model, payload) {
  store.set('download', {
    modelId: model.id,
    name: model.name || model.id,
    active: true,
    error: '',
    phase: payload.phase || 'starting',
    bytes: payload.bytes || 0,
    total: payload.total || model.sizeBytes || 0,
    speedBps: payload.speedBps || 0,
  });
  paintDock();
}

function clearDownload() {
  store.set('download', null);
  paintDock();
}

function paintDock() {
  const dock = document.getElementById('download-dock');
  if (!dock) return;
  const current = store.get('download');
  const hidden = !current || activeDialog || document.querySelector('.setup');
  dock.hidden = hidden;
  if (!current || hidden) return;
  const name = dock.querySelector('.download-dock__name');
  const meta = dock.querySelector('.download-dock__meta');
  if (name) name.textContent = current.name || 'Модель';
  if (meta) meta.textContent = current.error ? current.error : progressLabel(current);
  dock.classList.toggle('is-error', Boolean(current.error));
  dock.title = current.error ? 'Нажмите, чтобы скрыть сообщение' : 'Показать загрузку';
  const label = current.error
    ? `Ошибка загрузки ${current.name}`
    : `Загрузка ${current.name}: ${progressLabel(current)}`;
  dock.setAttribute('aria-label', label);
}

function announceReady(modelId, name) {
  if (finishedId === modelId || document.querySelector('.setup')) return;
  finishedId = modelId;
  const dialog = modal({
    title: 'Модель готова',
    subtitle: `«${name}» проверена по размеру и SHA-256. Можно переводить.`,
    actions: [
      { label: 'Остаться', onClick: () => dialog.close() },
      {
        label: 'Открыть диалог',
        variant: 'primary',
        onClick: () => {
          dialog.close();
          router.showPage('chat');
        },
      },
    ],
  });
}

function paintProgress(bar, status, cancelBtn, payload) {
  if (payload.phase === 'verifying') {
    bar.set(null);
    status.textContent = 'Проверка SHA-256. Отмена ещё доступна.';
    return;
  }
  if (payload.phase === 'activating') {
    bar.set(null);
    status.textContent = 'Активация файла. Отмена уже недоступна.';
    if (cancelBtn) cancelBtn.disabled = true;
    return;
  }
  const speed = formatSpeed(payload.speedBps);
  const eta = formatEta(payload.bytes || 0, payload.total || 0, payload.speedBps);
  if (payload.total > 0) {
    bar.set(payload.bytes / payload.total);
    const parts = [`${formatBytes(payload.bytes)} из ${formatBytes(payload.total)}`];
    if (speed) parts.push(speed);
    if (eta) parts.push(`ещё около ${eta}`);
    status.textContent = parts.join(' · ');
    return;
  }
  bar.set(null);
  status.textContent = [formatBytes(payload.bytes || 0), speed].filter(Boolean).join(' · ');
}

function progressLabel(current) {
  if (current.phase === 'verifying') return 'проверка';
  if (current.phase === 'activating') return 'активация';
  if (current.phase === 'starting') return 'подготовка';
  if (current.total > 0) return `${Math.round((current.bytes / current.total) * 100)}%`;
  return formatBytes(current.bytes || 0);
}

async function cancel(status) {
  try {
    const data = await call('cancelDownload');
    if (data && data.accepted === false) {
      status.textContent = 'Модель уже активируется. Отмена недоступна.';
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}
