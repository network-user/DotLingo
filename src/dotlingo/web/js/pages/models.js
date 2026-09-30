/**
 * Каталог моделей: рекомендация по RAM и диску, загрузка с согласием, свой GGUF.
 */

import { call } from '../bridge.js';
import { confirmDialog, formatBytes, modal, progressBar, spinner } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';
import {
  allLanguageOptions,
  button,
  el,
  emptyState,
  field,
  installBadge,
  languageChecks,
  loadLanguages,
  loadModels,
  reloadProjects,
  run,
  selectBox,
  toast,
} from './common.js';

const CONTEXT_SIZES = [2048, 4096, 8192, 16384, 32768];

let generation = 0;
let unlisten = () => {};

router.registerPage('models', {
  title: 'Модели',
  subtitle: 'Вес скачивается только после отдельного согласия',
  layout: 'wide',
  actions: () => [
    button({
      label: 'Свой GGUF',
      iconName: 'upload',
      onClick: () => openImport(),
    }),
  ],
  render(host) {
    const ticket = ++generation;
    unlisten();
    unlisten = () => {};
    const offModels = store.on('models_changed', () => {
      if (ticket === generation) paint(host, ticket);
    });
    const offDownload = store.watch('download', (payload) => {
      if (ticket === generation) updateDownloadSlot(payload);
    });
    unlisten = () => {
      offModels();
      offDownload();
    };
    paint(host, ticket);
  },
  destroy() {
    generation += 1;
    unlisten();
    unlisten = () => {};
  },
});

async function paint(host, ticket) {
  host.replaceChildren(spinner());
  const data = await run(() => loadModels());
  if (ticket !== generation || !host.isConnected) return;
  if (!data) {
    host.replaceChildren(emptyState({ iconName: 'error', title: 'Каталог моделей не открылся' }));
    return;
  }
  const models = data.models || [];
  const recommendation = data.recommendation;
  const blocks = [];
  if (recommendation?.reason) {
    const name = models.find((item) => item.id === recommendation.id)?.name;
    blocks.push(el('div', {
      class: 'note',
      text: name
        ? `По расчётной памяти и месту на диске ближе всего «${name}». ${recommendation.reason}`
        : recommendation.reason,
    }));
  }
  const grid = el('div', { class: 'model-grid' });
  for (const model of models) grid.append(card(model));
  blocks.push(grid);
  host.replaceChildren(...blocks);
  updateDownloadSlot(store.get('download'));
}

function card(model) {
  const facts = [
    ['Размер', model.sizeLabel || 'неизвестно'],
    ['Память', model.estimatedRamGb ? `${model.estimatedRamGb} ГБ, ориентир` : 'нет ориентира'],
    ['Квантование', model.quantization || 'не указано'],
    ['Лицензия', model.license || 'Не указана'],
  ];
  const compat = model.compatibility?.reason
    ? el('p', { class: model.compatibility.verdict === 'no' ? 'note note--error' : 'note', text: model.compatibility.reason })
    : null;
  return el('article', { class: 'panel', 'data-model': model.id }, [
    el('div', { class: 'model-card__top' }, [
      el('div', {}, [
        el('div', { class: 'model-card__name', text: model.name }),
        model.uiDescription ? el('p', { class: 'muted', text: model.uiDescription }) : null,
      ]),
      installBadge(model.installState),
    ]),
    el('div', { class: 'stats' }, facts.map(([label, value]) => el('div', { class: 'stat' }, [
      el('div', { class: 'stat__label', text: label }),
      el('div', { class: 'stat__value', text: value }),
    ]))),
    model.revision ? el('p', { class: 'tiny', text: `Ревизия ${model.revision}` }) : null,
    model.testedOnWindows ? null : el('p', { class: 'tiny', text: 'Запуск на Windows этой записью не подтверждён.' }),
    compat,
    model.uiDetails ? el('p', { class: 'tiny', text: model.uiDetails }) : null,
    el('div', { class: 'download-slot' }),
    el('div', { class: 'row row--wrap' }, actionsFor(model)),
  ]);
}

function actionsFor(model) {
  const nodes = [];
  if (model.cardUrl) {
    nodes.push(el('a', {
      class: 'btn',
      href: model.cardUrl,
      target: '_blank',
      rel: 'noreferrer',
      text: 'Карточка',
    }));
  }
  if (model.installState === 'available') {
    nodes.push(button({
      label: 'Загрузить',
      iconName: 'download',
      variant: 'primary',
      onClick: () => confirmDownload(model),
    }));
  }
  if (model.installed) {
    nodes.push(button({
      label: 'Проверить файл',
      iconName: 'check',
      onClick: () => verify(model),
    }));
    nodes.push(button({
      label: 'Выбрать в проекте',
      iconName: 'arrow-right',
      variant: 'primary',
      onClick: () => selectForProject(model),
    }));
  }
  if (model.installState === 'unverified') {
    nodes.push(el('p', { class: 'tiny', text: 'Загрузка закрыта: запись ещё не проверена.' }));
  }
  return nodes;
}

function confirmDownload(model) {
  const hardware = store.get('hardware');
  const disk = hardware?.diskFreeGb != null ? `Свободно на диске около ${hardware.diskFreeGb} ГБ.` : '';
  const box = el('input', { type: 'checkbox' });
  const dialog = modal({
    title: model.name,
    subtitle: 'Файл большой. Скачивание начнётся только после этой отметки.',
    render: (body) => {
      body.append(el('div', { class: 'stack stack--lg' }, [
        el('p', { class: 'muted', text: `${model.sizeLabel || 'Размер неизвестен'}. ${disk}` }),
        el('p', { class: 'tiny', text: `Лицензия: ${model.license || 'Не указана'}` }),
        model.revision ? el('p', { class: 'tiny', text: `Закреплённая ревизия: ${model.revision}` }) : null,
        model.licenseUrl ? el('a', {
          class: 'linkish',
          href: model.licenseUrl,
          target: '_blank',
          rel: 'noreferrer',
          text: 'Текст лицензии',
        }) : null,
        el('label', { class: 'check' }, [
          box,
          el('span', { text: 'Я выбираю эту модель и разрешаю загрузку файла' }),
        ]),
      ]));
    },
    actions: [
      { label: 'Отмена', onClick: () => dialog.close() },
      {
        label: 'Загрузить',
        variant: 'primary',
        onClick: async () => {
          if (!box.checked) {
            toast('Нужна отметка согласия', 'warning');
            return;
          }
          const started = await run(() => call('downloadModel', model.id));
          if (started === undefined) return;
          dialog.close();
          toast('Загрузка начата', 'info');
        },
      },
    ],
  });
}

function updateDownloadSlot(payload) {
  document.querySelectorAll('.download-slot').forEach((slot) => slot.replaceChildren());
  if (!payload?.modelId) return;
  const slot = document.querySelector(`[data-model="${CSS.escape(payload.modelId)}"] .download-slot`);
  if (!slot) return;
  const ratio = Number(payload.ratio);
  const bar = progressBar(Number.isFinite(ratio) ? ratio : null);
  const label = payload.total
    ? `${formatBytes(payload.bytes || 0)} / ${formatBytes(payload.total)}`
    : (payload.phase || 'Загрузка');
  slot.append(
    el('div', { class: 'stack' }, [
      el('div', { class: 'tiny', text: label }),
      bar.root,
      button({
        label: 'Отменить загрузку',
        size: 'sm',
        onClick: () => run(() => call('cancelDownload')),
      }),
    ]),
  );
}

async function verify(model) {
  const started = await run(() => call('verifyModel', model.id));
  if (started === undefined) return;
  toast('Проверяю размер и SHA-256', 'info');
}

async function selectForProject(model) {
  if (!store.get('activeProject')) {
    toast('Сначала откройте проект', 'warning');
    router.showPage('projects');
    return;
  }
  const yes = await confirmDialog({
    title: `Использовать «${model.name}»?`,
    text: 'Модель запишется в открытый проект. Языки проекта должны входить в список карточки.',
    confirmLabel: 'Выбрать',
  });
  if (!yes) return;
  const saved = await run(async () => {
    await call('selectModelForProject', model.id);
    return reloadProjects();
  });
  if (saved === undefined) return;
  toast('Модель выбрана в проекте', 'success');
}

async function openImport() {
  if (!store.get('languages')) await run(() => loadLanguages());
  const name = el('input', { class: 'input', placeholder: 'Как показывать в списке' });
  const license = el('input', { class: 'input', value: 'Не указана' });
  const context = selectBox(
    CONTEXT_SIZES.map((size) => ({ value: String(size), label: String(size) })),
    '4096',
  );
  const picker = languageChecks(
    allLanguageOptions().map((item) => ({ code: item.code, label: `${item.label} · ${item.code}` })),
    new Set(['en', 'ru']),
  );
  const pathLine = el('p', { class: 'tiny', text: 'Файл ещё не выбран' });
  let sourcePath = '';

  const dialog = modal({
    title: 'Свой GGUF',
    subtitle: 'Файл копируется в каталог моделей. Исходник на диске не меняется.',
    render: (body) => {
      body.append(el('div', { class: 'stack stack--lg' }, [
        field('Название', name),
        field('Лицензия, как вы её указываете', license),
        field('Контекст', context, 'Заявленный размер, не проверка шаблона чата'),
        el('div', { class: 'field' }, [
          el('span', { class: 'field__label', text: 'Языки карточки' }),
          el('span', { class: 'field__hint', text: 'Это ваш список, не измеренное качество пар' }),
          picker.element,
        ]),
        el('div', { class: 'row' }, [
          button({
            label: 'Выбрать файл',
            iconName: 'document',
            onClick: async () => {
              const picked = await run(() => call('resolveModelPath'));
              if (!picked) return;
              sourcePath = picked;
              pathLine.textContent = picked;
            },
          }),
        ]),
        pathLine,
      ]));
    },
    actions: [
      { label: 'Отмена', onClick: () => dialog.close() },
      {
        label: 'Скопировать в каталог',
        variant: 'primary',
        onClick: async () => {
          const codes = picker.value();
          if (!name.value.trim() || !sourcePath || !codes.length) {
            toast('Нужны название, файл .gguf и хотя бы один язык', 'warning');
            return;
          }
          const started = await run(() => call('importCustomModel', {
            sourcePath,
            name: name.value.trim(),
            languageCodes: codes,
            contextSize: Number(context.value),
            licenseName: license.value.trim() || 'Не указана',
          }));
          if (started === undefined) return;
          dialog.close();
          toast('Копирую и проверяю файл', 'info');
        },
      },
    ],
  });
}
