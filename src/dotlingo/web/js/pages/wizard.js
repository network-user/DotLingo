/**
 * Первый запуск: один экран вместо мастера из нескольких шагов.
 * Устройство определяется само, модель уже подобрана.
 * Скачивание начинается с кнопки, на которой написаны объём и лицензия:
 * это и есть согласие. Отдельная галочка не нужна.
 * Показывается один раз (preferences.setup_seen). Если вес уже стоит, экран пропускается.
 */

import { call } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { el, button, toast, progressBar, formatBytes } from '../components.js';
import {
  catalogModels,
  formatEta,
  formatGb,
  formatSpeed,
  gpuSummary,
  hasInstalledModel,
  placementBlocked,
  ramUsedFraction,
  recommendedChoice,
  refreshCatalog,
  threadNote,
} from '../device.js';

/** @type {HTMLElement|null} */
let root = null;

/** @type {Array<() => void>} */
let unsubs = [];

/** @type {{ root: HTMLElement, set: (value: number|null) => void }|null} */
let barApi = null;

/** plan | pick | working | error */
let phase = 'plan';

/** @type {object|null} модель текущей загрузки */
let activeModel = null;

/** @type {object|null} */
let progress = null;

let errorText = '';
let selectedId = '';
let shownThisSession = false;
let closing = false;

/**
 * Точка входа. Импорт из pages/index.js происходит после первой загрузки данных.
 */
export async function setupWizard() {
  if (shownThisSession) return;
  const [prefs] = await call('getPreferences')
    .then((data) => [data])
    .catch(() => [null]);
  if (!prefs || prefs.setup_seen) return;
  shownThisSession = true;

  if (hasInstalledModel()) {
    await call('setPreferences', { setup_seen: true }).catch(() => {});
    return;
  }

  phase = 'plan';
  open();
  if (!store.get('hardware')) {
    call('detectHardware').catch(() => {});
  } else if (store.get('recommendation') == null) {
    refreshCatalog().catch(() => {});
  }
}

function open() {
  closeWizard();
  closing = false;
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
      if (phase === 'working' || closing) return;
      render();
    }),
    store.on('models_refreshed', () => {
      if (phase === 'working' || closing) return;
      if (phase === 'plan') selectedId = recommendedChoice().model?.id || '';
      else if (!catalogModels().some((model) => model.id === selectedId)) {
        selectedId = recommendedChoice().model?.id || catalogModels()[0]?.id || '';
      }
      render();
    }),
  );
  render();
}

function closeWizard() {
  unsubs.forEach((off) => off());
  unsubs = [];
  barApi = null;
  root?.remove();
  root = null;
  document.removeEventListener('keydown', onKeydown, true);
}

function onKeydown(event) {
  if (!root) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    if (phase === 'working') return;
    if (phase === 'pick') {
      phase = 'plan';
      selectedId = recommendedChoice().model?.id || '';
      render();
      return;
    }
    void postpone();
    return;
  }
  if (event.key !== 'Tab') return;
  const nodes = [...root.querySelectorAll('button, a, input, select, textarea')].filter(
    (node) => !node.disabled && node.offsetParent !== null,
  );
  if (!nodes.length) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function stillDetecting() {
  if (phase !== 'plan') return false;
  if (!store.get('hardware')) return true;
  return store.get('recommendation') == null;
}

function chosenModel() {
  if (phase === 'pick') {
    return catalogModels().find((model) => model.id === selectedId) ?? null;
  }
  return recommendedChoice().model;
}

function render() {
  if (!root || closing) return;
  const previous = document.activeElement;
  barApi = null;
  const frame = el('div', { class: 'setup__frame' }, [
    el('header', { class: 'setup__brand' }, [
      el('div', { class: 'brand__mark', ariaHidden: 'true', text: 'D' }),
      el('div', {}, [
        el('p', { class: 'setup__kicker', text: 'Первый запуск' }),
        el('p', { class: 'setup__product', text: 'Локальный перевод на этом компьютере' }),
      ]),
    ]),
    el('div', { class: 'setup__grid' }, [deviceColumn(), choiceColumn()]),
  ]);
  root.replaceChildren(frame);
  root.setAttribute('aria-busy', phase === 'working' ? 'true' : 'false');
  if (phase === 'working' && progress) paintProgress(progress);
  if (previous && root.contains(previous)) return;
  if (phase === 'pick' && previous?.classList?.contains('setup__option')) {
    root.querySelector('.setup__option.is-selected')?.focus();
    return;
  }
  const target = [...root.querySelectorAll('button')].find((node) => !node.disabled);
  target?.focus();
}

function deviceColumn() {
  const hw = store.get('hardware');
  const used = ramUsedFraction(hw);
  const metrics = el('div', { class: 'setup__metrics' }, [
    metric(
      'RAM свободно',
      hw ? formatGb(hw.ramAvailableGb) : '…',
      hw && Number.isFinite(hw.ramTotalGb) ? `из ${formatGb(hw.ramTotalGb)}` : '',
      used,
    ),
    metric('Диск свободен', hw ? formatGb(hw.diskFreeGb) : '…', 'каталог моделей'),
    metric(
      'CPU',
      hw && hw.cpuThreads ? String(hw.cpuThreads) : '…',
      hw ? 'логических процессоров' : '',
    ),
  ]);

  const lines = [];
  if (hw) {
    lines.push(fact('GPU', gpuSummary(hw)));
    if (!(hw.gpuNames ?? []).length) {
      lines.push(el('p', { class: 'setup__note', text: 'Проверка смотрит только NVIDIA.' }));
    }
    lines.push(
      fact(
        'Runtime',
        hw.llamaRuntimeAvailable ? 'llama.cpp доступен' : 'llama.cpp не установлен',
      ),
    );
    lines.push(el('p', { class: 'setup__note', text: threadNote() }));
  } else {
    lines.push(el('p', { class: 'setup__note', text: 'Считываем память, диск и runtime. Сеть для этого не нужна.' }));
  }

  return el('section', { class: 'setup__device', ariaLabel: 'Устройство' }, [
    el('h2', { class: 'setup__device-title', text: 'Устройство' }),
    metrics,
    ...lines,
  ]);
}

function metric(label, value, sub, fraction) {
  return el('div', { class: 'setup__metric' }, [
    el('span', { class: 'setup__metric-label', text: label }),
    el('span', { class: 'setup__metric-value', text: value }),
    sub ? el('span', { class: 'setup__metric-sub', text: sub }) : null,
    fraction == null
      ? null
      : el('div', { class: 'setup__meter', ariaHidden: 'true' }, [
          el('div', {
            class: 'setup__meter-fill',
            style: { width: `${Math.round(fraction * 100)}%` },
          }),
        ]),
    fraction == null
      ? null
      : el('span', { class: 'setup__metric-sub', text: `занято ${Math.round(fraction * 100)}%` }),
  ]);
}

function fact(label, value) {
  return el('div', { class: 'setup__fact' }, [
    el('span', { class: 'setup__fact-label', text: label }),
    el('span', { class: 'setup__fact-value', text: value }),
  ]);
}

function choiceColumn() {
  if (phase === 'working') return workingColumn();
  if (phase === 'error') return errorColumn();
  if (phase === 'pick') return pickColumn();
  return planColumn();
}

function planColumn() {
  if (stillDetecting()) {
    return column(
      'Смотрим устройство',
      'Память, диск и runtime считываются сами. Модель появится здесь, отдельный шаг для этого не нужен.',
      [],
      [
        button({ label: 'Подбираем модель', variant: 'primary', disabled: true }),
        laterButton(),
      ],
    );
  }

  const { model, reason } = recommendedChoice();
  if (!model) {
    const go = button({
      label: 'Перейти к проектам',
      variant: 'primary',
      onClick: () => void postpone(),
    });
    go.classList.add('setup__primary');
    return column(
      'Модель не подобрана',
      reason || 'В каталоге нет модели с известным размером и оценкой памяти.',
      [],
      [go],
    );
  }

  const blocked = placementBlocked(model);
  const lead = blocked
    ? model.compatibility?.reason || reason
    : 'Оценка памяти и места на диске позволяет поставить эту модель. Качество перевода эта оценка не измеряет.';
  const primary = button({
    label: blocked ? 'Не помещается' : `Скачать ${model.sizeLabel}`,
    variant: 'primary',
    disabled: blocked,
    title: blocked ? model.compatibility?.reason || '' : '',
    onClick: () => begin(model),
  });
  primary.classList.add('setup__primary');

  return column(model.name, lead, modelMeta(model, reason), [
    primary,
    button({ label: 'Другая модель', onClick: openPicker }),
    laterButton(),
  ]);
}

function pickColumn() {
  const models = catalogModels();
  const selected = models.find((model) => model.id === selectedId) ?? null;
  const blocked = selected ? placementBlocked(selected) && !selected.installed : false;
  const list = el('div', { class: 'setup__options', role: 'group', ariaLabel: 'Модели' });
  const recommendedId = recommendedChoice().model?.id;

  for (const model of models) {
    const on = model.id === selectedId;
    const bits = [model.sizeLabel];
    if (Number.isFinite(model.estimatedRamGb)) bits.push(`ориентир ${model.estimatedRamGb} ГБ RAM`);
    if (model.installed) bits.push('уже на диске');
    else if (placementBlocked(model)) bits.push('не помещается');
    if (model.id === recommendedId) bits.push('подходит по памяти');
    list.append(
      el('button', {
        type: 'button',
        class: `setup__option${on ? ' is-selected' : ''}`,
        ariaPressed: on ? 'true' : 'false',
        onClick: () => {
          selectedId = model.id;
          render();
        },
      }, [
        el('span', { class: 'setup__option-name', text: model.name }),
        el('span', { class: 'setup__option-meta', text: bits.join(' · ') }),
      ]),
    );
  }

  let primaryLabel = 'Перейти к проектам';
  if (selected?.installed) primaryLabel = 'Продолжить';
  else if (selected && !blocked) primaryLabel = `Скачать ${selected.sizeLabel}`;
  else if (blocked) primaryLabel = 'Не помещается';

  const primary = button({
    label: primaryLabel,
    variant: 'primary',
    disabled: blocked,
    onClick: () => {
      if (!selected || blocked) {
        void postpone();
        return;
      }
      begin(selected);
    },
  });
  primary.classList.add('setup__primary');

  return column(
    'Другая модель',
    'Список ограничен моделями, которые можно скачать или которые уже стоят на диске.',
    [list],
    [
      primary,
      button({
        label: 'К рекомендации',
        onClick: () => {
          phase = 'plan';
          selectedId = recommendedChoice().model?.id || '';
          render();
        },
      }),
      laterButton(),
    ],
  );
}

function workingColumn() {
  const model = activeModel;
  barApi = progressBar(null);
  barApi.root.setAttribute('role', 'progressbar');
  barApi.root.setAttribute('aria-valuemin', '0');
  barApi.root.setAttribute('aria-valuemax', '100');
  barApi.root.setAttribute('aria-label', 'Загрузка модели');
  const status = el('p', {
    class: 'setup__status',
    role: 'status',
    ariaLive: 'polite',
    text: 'Подготовка загрузки…',
  });
  const cancel = button({
    label: 'Отменить загрузку',
    onClick: () => void cancelDownload(),
  });
  cancel.classList.add('setup__cancel');
  return column(
    model?.name || 'Загрузка',
    'Файл сверяется по размеру и SHA-256 до того, как станет рабочей моделью.',
    [barApi.root, status],
    [cancel],
  );
}

function errorColumn() {
  const cancelled = /отмен/i.test(errorText);
  const retry = button({
    label: 'Повторить',
    variant: 'primary',
    disabled: !activeModel,
    onClick: () => {
      if (activeModel) begin(activeModel);
    },
  });
  retry.classList.add('setup__primary');
  return column(
    cancelled ? 'Загрузка остановлена' : 'Не удалось скачать модель',
    errorText || 'Загрузка не удалась.',
    [],
    [retry, laterButton()],
  );
}

function column(title, lead, extra, actions) {
  const actionRow = el('div', { class: 'setup__actions' }, actions);
  const primary = actionRow.querySelector('.btn--primary');
  primary?.classList.add('setup__primary');
  return el('section', { class: 'setup__choice' }, [
    el('h1', { class: 'setup__title', id: 'setup-title', text: title }),
    el('p', { class: 'setup__lead', text: lead }),
    ...extra,
    actionRow,
  ]);
}

function modelMeta(model, reason) {
  const hw = store.get('hardware');
  const nodes = [
    el('p', { class: 'setup__meta', text: metaLine(model) }),
    el('p', {
      class: 'setup__consent',
      id: 'setup-consent',
      text: `Лицензия: ${model.license}. Кнопка скачивает закреплённую ревизию. Перед включением сверяются размер и SHA-256.`,
    }),
  ];
  if (reason && !placementBlocked(model)) {
    nodes.push(el('p', { class: 'setup__reason', text: reason }));
  }
  if (hw && !hw.llamaRuntimeAvailable && !model.installed) {
    nodes.push(
      el('p', {
        class: 'setup__note',
        text: 'Вес можно скачать сейчас. Перевод запустится после установки runtime llama.cpp.',
      }),
    );
  }
  return nodes;
}

function metaLine(model) {
  const bits = [model.sizeLabel];
  if (Number.isFinite(model.estimatedRamGb)) bits.push(`ориентир ${model.estimatedRamGb} ГБ RAM`);
  if (model.quantization) bits.push(model.quantization);
  return bits.join(' · ');
}

function laterButton(label = 'Позже') {
  return button({ label, onClick: () => void postpone() });
}

function openPicker() {
  const { model, models } = recommendedChoice();
  selectedId = model?.id || models[0]?.id || '';
  phase = 'pick';
  render();
}

function begin(model) {
  if (model.installed) {
    void finish(model.name, true);
    return;
  }
  if (placementBlocked(model)) return;
  void startDownload(model);
}

async function startDownload(model) {
  phase = 'working';
  activeModel = model;
  progress = null;
  errorText = '';
  render();

  const offProgress = store.on('download_progress', (payload) => {
    if (payload?.modelId !== model.id) return;
    progress = payload;
    paintProgress(payload);
  });
  const offDone = store.on('download_done', (payload) => {
    if (payload?.modelId !== model.id) return;
    offProgress();
    offDone();
    if (payload.ok) {
      void finish(model.name);
      return;
    }
    phase = 'error';
    errorText = payload.error || 'Загрузка не удалась.';
    render();
  });
  unsubs.push(offProgress, offDone);

  try {
    await call('downloadModel', model.id);
  } catch (error) {
    offProgress();
    offDone();
    phase = 'error';
    errorText = error.message || 'Загрузка не удалась.';
    render();
  }
}

function paintProgress(payload) {
  if (!barApi || !root) return;
  const status = root.querySelector('.setup__status');
  const cancel = root.querySelector('.setup__cancel');
  const indeterminate = payload.phase === 'verifying'
    || payload.phase === 'activating'
    || !(payload.total > 0);
  if (indeterminate) {
    barApi.set(null);
    barApi.root.removeAttribute('aria-valuenow');
  } else {
    const ratio = payload.bytes / payload.total;
    barApi.set(ratio);
    barApi.root.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
  }
  if (cancel) cancel.disabled = payload.phase === 'activating';
  if (status) status.textContent = progressText(payload);
}

function progressText(payload) {
  if (payload.phase === 'verifying') return 'Проверяем размер и SHA-256. Отмена ещё доступна.';
  if (payload.phase === 'activating') return 'Активируем проверенный файл. Отмена уже недоступна.';
  const got = formatBytes(payload.bytes || 0);
  const speed = formatSpeed(payload.speedBps);
  const eta = formatEta(payload.bytes || 0, payload.total || 0, payload.speedBps);
  if (payload.total > 0) {
    const parts = [`Получено ${got} из ${formatBytes(payload.total)}`];
    if (speed) parts.push(speed);
    if (eta) parts.push(`ещё около ${eta}`);
    return parts.join(' · ');
  }
  return [got === '—' ? 'Получаем файл' : `Получено ${got}`, speed].filter(Boolean).join(' · ');
}

async function cancelDownload() {
  try {
    const data = await call('cancelDownload');
    if (data && data.accepted === false) {
      toast('Модель уже активируется. Дождитесь завершения.', 'warning');
      const cancel = root?.querySelector('.setup__cancel');
      if (cancel) cancel.disabled = true;
    }
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function finish(modelName, alreadyInstalled = false) {
  if (closing) return;
  closing = true;
  closeWizard();
  await call('setPreferences', { setup_seen: true }).catch(() => {});
  await refreshCatalog().catch(() => {});
  if (modelName && alreadyInstalled) toast(`Модель «${modelName}» уже на диске.`, 'success');
  else if (modelName) toast(`Модель «${modelName}» проверена и готова.`, 'success');
  router.showPage('projects');
}

async function postpone() {
  if (closing || phase === 'working') return;
  closing = true;
  closeWizard();
  await call('setPreferences', { setup_seen: true }).catch(() => {});
  toast('Модель можно скачать позже в настройках.', 'info');
  router.showPage('projects');
}

setupWizard().catch((error) => console.error('[setup]', error));
