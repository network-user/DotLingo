/**
 * Первый запуск: один экран и одна кнопка.
 * Нажатие само проверяет устройство, выбирает модель и начинает загрузку.
 * Любая ошибка, пустой каталог или отказ сети всё равно открывают приложение.
 */

import { call } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { el, button, toast } from '../components.js';
import {
  formatGb,
  gpuSummary,
  hasInstalledModel,
  placementBlocked,
  recommendedChoice,
  refreshCatalog,
} from '../device.js';

/** @type {HTMLElement|null} */
let root = null;

/** @type {Array<() => void>} */
let unsubs = [];

let shownThisSession = false;
let closing = false;
let running = false;

const HARDWARE_WAIT_MS = 20000;
const CATALOG_WAIT_MS = 12000;

/**
 * Точка входа. Импорт страниц идёт после первого кадра.
 * Если вес уже стоит, экран не показываем.
 */
export async function setupWizard() {
  if (shownThisSession) return;
  shownThisSession = true;

  let prefs = null;
  try {
    prefs = await call('getPreferences');
  } catch {
    prefs = null;
  }
  if (prefs?.setup_seen) return;

  await withTimeout(refreshCatalog().catch(() => {}), 2500);
  if (hasInstalledModel()) {
    await call('setPreferences', { setup_seen: true }).catch(() => {});
    return;
  }
  open();
}

function open() {
  closeWizard();
  closing = false;
  running = false;
  root = el('div', {
    class: 'setup',
    role: 'dialog',
    ariaModal: 'true',
    ariaLabelledBy: 'setup-title',
  });
  document.body.appendChild(root);
  document.addEventListener('keydown', onKeydown, true);
  unsubs.push(
    store.on('hardware_detected', () => {
      if (running || closing) return;
      render();
    }),
    store.on('models_refreshed', () => {
      if (running || closing) return;
      render();
    }),
  );
  render();
}

function closeWizard() {
  unsubs.forEach((off) => off());
  unsubs = [];
  root?.remove();
  root = null;
  document.removeEventListener('keydown', onKeydown, true);
}

function onKeydown(event) {
  if (!root || running) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    void finish('Настройку можно пройти позже. Приложение открыто.');
  }
}

function render() {
  if (!root || closing) return;
  const frame = el('div', { class: 'setup__frame' }, [
    el('header', { class: 'setup__brand' }, [
      el('div', { class: 'brand__mark', ariaHidden: 'true' }),
      el('div', {}, [
        el('p', { class: 'setup__kicker', text: 'Первый запуск' }),
        el('p', { class: 'setup__product', text: 'Локальный перевод на этом компьютере' }),
      ]),
    ]),
    el('h1', { class: 'setup__title', id: 'setup-title', text: 'Можно переводить на этом компьютере' }),
    el('p', {
      class: 'setup__lead',
      text: running
        ? 'Проверяем устройство, выбираем модель и запускаем загрузку.'
        : previewText(),
    }),
    el('p', { class: 'setup__note', text: deviceLine() }),
    el('p', {
      class: 'setup__note',
      text: 'Если скачать не получится или подходящей модели нет, приложение всё равно откроется.',
    }),
    el('div', { class: 'setup__actions' }, [primaryButton()]),
  ]);
  root.replaceChildren(frame);
  root.setAttribute('aria-busy', running ? 'true' : 'false');
  frame.querySelector('button')?.focus();
}

function primaryButton() {
  const node = button({
    label: running ? 'Готовим…' : 'Начать',
    variant: 'primary',
    disabled: running,
    onClick: () => void configure(),
  });
  node.classList.add('setup__primary');
  return node;
}

function previewText() {
  if (hasInstalledModel()) {
    return 'Подходящая модель уже на диске. Кнопка просто откроет приложение.';
  }
  const { model, reason } = recommendedChoice();
  if (model && !placementBlocked(model)) {
    const license = model.license ? ` Лицензия: ${model.license}.` : '';
    return `Кнопка проверит компьютер, выберет «${model.name}» (${model.sizeLabel}) и начнёт загрузку.${license} Потоки и видеокарта настроятся сами.`;
  }
  if (reason) {
    return `${reason} Кнопка всё равно завершит настройку и откроет приложение.`;
  }
  return 'Одна кнопка проверяет память и диск, выбирает модель и начинает загрузку. Потоки и видеокарта настроятся сами.';
}

function deviceLine() {
  const hw = store.get('hardware');
  if (!hw) return 'Устройство ещё проверяется. Кнопку можно нажать сразу.';
  const parts = [];
  if (hw.cpuThreads) parts.push(`${hw.cpuThreads} потоков`);
  if (Number.isFinite(hw.ramTotalGb)) parts.push(`${formatGb(hw.ramTotalGb)} RAM`);
  if (Number.isFinite(hw.diskFreeGb)) parts.push(`${formatGb(hw.diskFreeGb)} свободно`);
  const gpu = gpuSummary(hw);
  if (gpu && gpu !== 'не обнаружен') parts.push(gpu);
  if (!hw.llamaRuntimeAvailable) parts.push('runtime llama.cpp пока не найден');
  return parts.join(' · ') || 'Устройство определено.';
}

async function configure() {
  if (running || closing) return;
  running = true;
  render();
  let note = 'Приложение открыто. Модель можно скачать позже в разделе «Модели».';
  try {
    await ensureHardware();
    await withTimeout(refreshCatalog().catch(() => {}), CATALOG_WAIT_MS);
    const plan = normalizePlan(await call('planSetup').catch(() => null));
    if (plan.action === 'ready') {
      note = plan.reason || `«${plan.name || 'Модель'}» уже на диске.`;
    } else if (plan.action === 'download' && plan.modelId) {
      note = await startDownload(plan);
    } else {
      note = plan.reason || note;
    }
  } catch (error) {
    note = error?.message
      ? `${error.message} Приложение всё равно открыто.`
      : note;
  }
  await finish(note);
}

async function startDownload(plan) {
  const label = plan.name || 'модель';
  const size = plan.sizeLabel ? ` (${plan.sizeLabel})` : '';
  try {
    await call('downloadModel', plan.modelId);
    return `Скачиваем «${label}»${size}. Прогресс внизу панели, работать можно сразу.`;
  } catch (error) {
    if (error?.code === 'busy') {
      return 'Загрузка уже идёт. Прогресс внизу панели.';
    }
    const message = error?.message || 'Скачать модель не удалось.';
    return `${message} Приложение открыто без новой модели.`;
  }
}

function normalizePlan(plan) {
  const action = plan?.action;
  if ((action === 'ready' || action === 'download') && plan.modelId) return plan;
  if (action === 'skip') return plan;
  return {
    action: 'skip',
    modelId: null,
    reason: plan?.reason || 'Не удалось составить план. Приложение откроется без загрузки.',
    name: '',
    sizeLabel: '',
    license: '',
  };
}

function ensureHardware() {
  if (store.get('hardware')) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      off();
      resolve();
    };
    const off = store.on('hardware_detected', done);
    call('detectHardware').catch(done);
    setTimeout(done, HARDWARE_WAIT_MS);
  });
}

function withTimeout(promise, ms) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    Promise.resolve(promise).then(done, () => done(null));
    setTimeout(() => done(null), ms);
  });
}

async function finish(note) {
  if (closing) return;
  closing = true;
  running = false;
  closeWizard();
  await call('setPreferences', { setup_seen: true }).catch(() => {});
  void refreshCatalog().catch(() => {});
  if (note) toast(note, 'info');
  try {
    router.showPage('chat');
  } catch (error) {
    console.error('[setup] не удалось открыть проекты', error);
  }
}

setupWizard().catch((error) => console.error('[setup]', error));
