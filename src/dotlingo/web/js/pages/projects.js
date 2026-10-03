/**
 * Страница «Проекты»: четыре листа (проект, языки, модель, проверка).
 * Листание вперёд и назад повторяет переворот страницы с заставки.
 * openCreateProjectModal остаётся точкой входа с экрана документов.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import {
  el,
  button,
  badge,
  chip,
  toast,
  emptyState,
  confirmDialog,
  spinner,
} from '../components.js';

const STEPS = [
  { id: 'project', label: 'Проект' },
  { id: 'languages', label: 'Языки' },
  { id: 'model', label: 'Модель' },
  { id: 'review', label: 'Проверка' },
];

const TASK_LABELS = {
  queued: 'В очереди',
  running: 'Перевод',
  paused: 'Пауза',
  complete: 'Готово',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  interrupted: 'Прервано',
};

const TASK_TONES = {
  queued: 'muted',
  running: 'muted',
  paused: 'warning',
  complete: 'success',
  failed: 'error',
  cancelled: 'muted',
  interrupted: 'warning',
};

const MONTHS_SHORT = [
  'янв', 'фев', 'мар', 'апр', 'мая', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
];

/** @type {HTMLElement|null} */
let hostEl = null;
/** @type {object[]} */
let projects = [];
/** @type {object[]} */
let models = [];
let modelsError = '';
let recommendationId = '';
let projectFilter = '';
let targetFilter = '';
let step = 0;
let ready = false;
let turning = false;
let busy = false;
let loadGeneration = 0;
/** 'active' подставляет последний проект, 'new' начинает пустой черновик. */
let intent = 'active';
let wantNew = false;

let draft = emptyDraft();

function emptyDraft() {
  return {
    mode: 'new',
    projectId: '',
    title: '',
    sourceLang: 'auto',
    targetLangs: [],
    modelId: '',
    documentCount: 0,
    updatedAt: '',
    taskSummary: {},
    context: '',
    rules: '',
  };
}

function langLabel(code) {
  if (code === 'auto') return 'Авто';
  const languages = store.get('languages') || {};
  return languages[code] || String(code || '').toUpperCase();
}

function formatDate(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const month = MONTHS_SHORT[date.getMonth()];
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${date.getDate()} ${month} ${date.getFullYear()}, ${hh}:${mm}`;
}

function plural(n) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'документ';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'документа';
  return 'документов';
}

function motionOff() {
  return document.documentElement.dataset.reduceMotion === 'true'
    || window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function currentModel() {
  return models.find((model) => model.id === draft.modelId) ?? null;
}

function defaultModelId() {
  if (recommendationId && models.some((model) => model.id === recommendationId)) {
    return recommendationId;
  }
  return models.find((model) => model.installState === 'installed')?.id
    || models[0]?.id
    || '';
}

function languageCodes() {
  const model = currentModel();
  if (model) return model.languageCodes || [];
  return Object.keys(store.get('languages') || {});
}

function savedTitle() {
  const title = draft.title.trim();
  if (title) return title;
  return draft.mode === 'new' ? 'Новый проект' : '';
}

function unsupportedCodes() {
  const model = currentModel();
  if (!model) return [];
  const codes = new Set(model.languageCodes || []);
  const picked = draft.targetLangs.filter((code) => !codes.has(code));
  if (draft.sourceLang !== 'auto' && !codes.has(draft.sourceLang)) {
    picked.unshift(draft.sourceLang);
  }
  return [...new Set(picked)];
}

/** Пустая строка значит, что шаг можно покинуть. */
function validate(index) {
  if (index === 0) {
    if (draft.mode === 'existing' && !draft.projectId) return 'Выберите проект.';
    if (draft.mode === 'existing' && !draft.title.trim()) {
      return 'Название проекта не может быть пустым.';
    }
    return '';
  }
  if (index === 1) {
    if (draft.targetLangs.length === 0) return 'Выберите хотя бы один целевой язык.';
    if (draft.sourceLang !== 'auto' && draft.targetLangs.includes(draft.sourceLang)) {
      return 'Исходный язык не может совпадать с целевым.';
    }
    return '';
  }
  if (index === 2) {
    if (!draft.modelId) return 'Выберите модель.';
    if (!currentModel()) return 'Выбранная модель не найдена.';
    const unsupported = unsupportedCodes();
    if (unsupported.length) {
      const names = unsupported.map((code) => langLabel(code)).join(', ');
      return `Модель не перечисляет поддержку: ${names}.`;
    }
    return '';
  }
  return '';
}

function furthestValid() {
  let reached = 0;
  for (let index = 0; index < 3; index += 1) {
    if (validate(index)) return reached;
    reached = index + 1;
  }
  return 3;
}

function projectById(id) {
  return projects.find((project) => project.id === id) ?? null;
}

function settingsDirty(project) {
  if (!project) return true;
  const sameTargets = [...(project.targetLangs || [])].sort().join('\u0000')
    === [...draft.targetLangs].sort().join('\u0000');
  return project.sourceLang !== draft.sourceLang
    || project.modelId !== draft.modelId
    || !sameTargets;
}

function titleDirty(project) {
  return Boolean(project) && project.title !== draft.title.trim();
}

function applyProject(project) {
  draft = {
    mode: 'existing',
    projectId: project.id,
    title: project.title || '',
    sourceLang: project.sourceLang || 'auto',
    targetLangs: [...(project.targetLangs || [])],
    modelId: project.modelId || defaultModelId(),
    documentCount: project.documentCount ?? 0,
    updatedAt: project.updatedAt || '',
    taskSummary: project.taskSummary || {},
    context: project.context || '',
    rules: project.rules || '',
  };
}

function applyIntent() {
  if (intent === 'new' || projects.length === 0) {
    const title = intent === 'new' ? draft.title : '';
    draft = emptyDraft();
    draft.title = title;
    draft.modelId = defaultModelId();
    step = 0;
    return;
  }
  const activeId = store.get('activeProject')?.id;
  applyProject(projectById(activeId) || projects[0]);
  step = 0;
}

function showError(text) {
  const node = hostEl?.querySelector('.deck__error');
  if (node) node.textContent = text || '';
}

/* -------------------------------------------------------------------------
 * Каркас
 * ------------------------------------------------------------------------- */

function render(host) {
  hostEl = host;
  intent = wantNew ? 'new' : 'active';
  wantNew = false;
  ready = false;
  turning = false;
  busy = false;
  projectFilter = '';
  targetFilter = '';
  draft = emptyDraft();
  step = 0;
  host.append(shell());
  void load();
}

function destroy() {
  loadGeneration += 1;
  turning = false;
  busy = false;
  hostEl = null;
}

function shell() {
  return el('div', {
    class: 'deck',
    tabIndex: 0,
    onKeydown: onDeckKey,
  }, [
    el('div', { class: 'deck__rail', role: 'tablist', ariaLabel: 'Шаги проекта' }),
    el('div', { class: 'deck__stage' }, [
      el('div', { class: 'deck__page' }, [
        el('div', { class: 'page-loading' }, [spinner('lg')]),
      ]),
    ]),
    el('div', { class: 'deck__bar' }, [
      el('p', { class: 'deck__error', role: 'alert' }),
      el('div', { class: 'deck__nav' }),
    ]),
  ]);
}

function onDeckKey(event) {
  const tag = event.target?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (event.target?.closest?.('.deck__list, .deck__tray, .deck__chosen, .model-picker')) return;
  if (event.key === 'ArrowRight') {
    event.preventDefault();
    goTo(step + 1);
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault();
    goTo(step - 1);
  }
}

async function load() {
  const generation = ++loadGeneration;
  ready = false;
  const stage = hostEl?.querySelector('.deck__stage');
  if (stage && !stage.querySelector('.page-loading')) {
    stage.replaceChildren(el('div', { class: 'deck__page' }, [
      el('div', { class: 'page-loading' }, [spinner('lg')]),
    ]));
  }
  paintChrome();

  const [projectData, projectErr] = await tryCall('listProjects');
  const [modelData, modelErr] = await tryCall('listModels');
  if (generation !== loadGeneration || !hostEl?.isConnected) return;
  if (projectErr) {
    ready = true;
    models = [];
    showLoadError(projectErr.message);
    return;
  }

  projects = projectData ?? [];
  store.set('projects', projects);
  modelsError = modelErr?.message || '';
  const keepId = store.get('activeProject')?.modelId || '';
  models = modelErr
    ? []
    : (modelData?.models ?? []).filter((model) =>
      model.installState === 'installed'
      || model.installState === 'available'
      || model.id === keepId);
  recommendationId = modelData?.recommendation?.id || store.get('recommendation')?.id || '';
  ready = true;
  applyIntent();
  showError('');
  turn('none');
}

function showLoadError(message) {
  const stage = hostEl?.querySelector('.deck__stage');
  if (!stage) return;
  stage.replaceChildren(el('div', { class: 'deck__page' }, [
    emptyState({
      iconName: 'error',
      title: 'Не удалось загрузить проекты',
      text: message,
      action: button({
        label: 'Повторить',
        variant: 'primary',
        onClick: () => { void load(); },
      }),
    }),
  ]));
  paintChrome();
}

function paintChrome() {
  const rail = hostEl?.querySelector('.deck__rail');
  const nav = hostEl?.querySelector('.deck__nav');
  if (!rail || !nav) return;
  const limit = ready ? furthestValid() : 0;
  rail.replaceChildren(...STEPS.map((item, index) => el('button', {
    class: `deck__step${index === step ? ' is-current' : ''}`,
    type: 'button',
    role: 'tab',
    ariaSelected: index === step ? 'true' : 'false',
    disabled: !ready || busy || turning || index > limit,
    onClick: () => goTo(index),
  }, [
    el('span', { class: 'deck__index', text: String(index + 1) }),
    el('span', { class: 'deck__step-label', text: item.label }),
  ])));

  const showNext = ready && step < 3;
  const showOpen = ready && (step === 3 || (draft.mode === 'existing' && furthestValid() === 3));
  nav.replaceChildren(
    button({
      label: 'Назад',
      variant: 'ghost',
      disabled: !ready || busy || turning || step === 0,
      onClick: () => goTo(step - 1),
    }),
    showNext
      ? button({
        label: 'Далее',
        variant: showOpen ? 'ghost' : 'primary',
        disabled: busy || turning,
        onClick: () => goTo(step + 1),
      })
      : null,
    showOpen
      ? button({
        label: openLabel(),
        variant: 'primary',
        disabled: busy || turning,
        onClick: () => { void finish(); },
      })
      : null,
  );
}

function openLabel() {
  if (busy) return 'Сохраняем…';
  if (draft.mode === 'new') return 'Создать и открыть документы';
  const project = projectById(draft.projectId);
  if (titleDirty(project) || settingsDirty(project)) return 'Сохранить и открыть документы';
  return 'Открыть документы';
}

function goTo(index) {
  if (!ready || busy || turning) return;
  if (index < 0 || index > 3 || index === step) return;
  if (index > step) {
    for (let cursor = step; cursor < index; cursor += 1) {
      const problem = validate(cursor);
      if (problem) {
        showError(problem);
        return;
      }
    }
  }
  showError('');
  const direction = index > step ? 'forward' : 'back';
  step = index;
  turn(direction);
}

function turn(direction) {
  const stage = hostEl?.querySelector('.deck__stage');
  if (!stage) return;
  const next = buildPage();
  const current = stage.querySelector('.deck__page');
  if (!current || direction === 'none' || motionOff()) {
    turning = false;
    stage.replaceChildren(next);
    paintChrome();
    return;
  }

  turning = true;
  paintChrome();
  if (direction === 'forward') {
    stage.insertBefore(next, current);
    current.classList.add('is-turn-forward');
  } else {
    next.classList.add('is-turn-back');
    stage.append(next);
  }
  const moving = direction === 'forward' ? current : next;

  const finishTurn = () => {
    if (!turning) return;
    turning = false;
    if (!hostEl?.isConnected) return;
    current.remove();
    next.classList.remove('is-turn-back');
    paintChrome();
    if (direction !== 'none') {
      const title = next.querySelector('h2');
      title?.setAttribute('tabindex', '-1');
      title?.focus({ preventScroll: true });
    }
  };
  moving.addEventListener('animationend', finishTurn, { once: true });
  setTimeout(finishTurn, 560);
}

function buildPage() {
  const page = el('div', { class: 'deck__page' });
  if (step === 0) page.append(projectSlide());
  else if (step === 1) page.append(languageSlide());
  else if (step === 2) page.append(modelSlide());
  else page.append(reviewSlide());
  return page;
}

function slideHead(title, lead) {
  return el('div', {}, [
    el('h2', { class: 'panel__title', text: title }),
    el('p', { class: 'deck__lead', text: lead }),
  ]);
}

/* -------------------------------------------------------------------------
 * Лист 1. Проект
 * ------------------------------------------------------------------------- */

function projectSlide() {
  const titleInput = el('input', {
    class: 'input deck-title',
    id: 'deck-title',
    type: 'text',
    value: draft.title,
    placeholder: draft.mode === 'new' ? 'Например, договор поставки' : 'Название проекта',
    autocomplete: 'off',
    onInput: (event) => {
      draft.title = event.target.value;
      paintChrome();
    },
  });

  const filter = projects.length > 5
    ? el('input', {
      class: 'input',
      type: 'search',
      value: projectFilter,
      placeholder: 'Поиск проекта…',
      autocomplete: 'off',
      ariaLabel: 'Поиск проекта',
      onInput: (event) => {
        projectFilter = event.target.value.trim().toLowerCase();
        const list = event.target.closest('.deck__page')?.querySelector('.deck__list');
        if (list) list.replaceChildren(...projectRows());
      },
    })
    : null;

  return el('div', { class: 'stack stack--lg' }, [
    slideHead(
      'Проект',
      'Выберите существующий или начните новый. Языки и модель на следующих листах.',
    ),
    el('div', { class: 'field' }, [
      el('label', { class: 'field__label', htmlFor: 'deck-title', text: 'Название' }),
      titleInput,
    ]),
    filter,
    el('div', { class: 'deck__list' }, projectRows()),
    el('div', { class: 'deck__tools' }, [
      button({
        label: 'Удалить',
        variant: 'danger',
        size: 'sm',
        disabled: draft.mode !== 'existing',
        onClick: () => { void deleteSelected(); },
      }),
    ]),
  ]);
}

function projectRows() {
  const needle = projectFilter;
  const rows = [
    pickButton({
      selected: draft.mode === 'new',
      title: 'Новый проект',
      meta: 'Пустой проект, без документов',
      onClick: () => selectNew({ keepTitle: true }),
    }),
  ];
  projects.forEach((project) => {
    const title = project.title || 'Проект';
    if (needle && !title.toLowerCase().includes(needle)) return;
    const targets = (project.targetLangs || []).map((code) => langLabel(code)).join(', ');
    const docs = `${project.documentCount ?? 0} ${plural(project.documentCount ?? 0)}`;
    rows.push(pickButton({
      selected: draft.mode === 'existing' && draft.projectId === project.id,
      title,
      meta: `${langLabel(project.sourceLang || 'auto')} → ${targets || 'языки не заданы'} · ${docs}`,
      onClick: () => selectExisting(project),
    }));
  });
  if (rows.length === 1 && needle) {
    rows.push(el('p', { class: 'deck__note', text: 'Ничего не найдено.' }));
  }
  return rows;
}

function pickButton({ selected, title, meta, onClick }) {
  return el('button', {
    class: `pick${selected ? ' is-selected' : ''}`,
    type: 'button',
    ariaPressed: selected ? 'true' : 'false',
    onClick,
  }, [
    el('span', { class: 'pick__main' }, [
      el('span', { class: 'pick__title', text: title }),
      el('span', { class: 'pick__meta', text: meta }),
    ]),
    selected ? badge({ label: 'Выбран', tone: 'muted' }) : null,
  ]);
}

function selectNew({ keepTitle = false } = {}) {
  const title = keepTitle && draft.mode === 'new' ? draft.title : '';
  draft = emptyDraft();
  draft.title = title;
  draft.modelId = defaultModelId();
  step = 0;
  targetFilter = '';
  showError('');
  turn('none');
  hostEl?.querySelector('.deck-title')?.focus();
}

function selectExisting(project) {
  applyProject(project);
  targetFilter = '';
  showError('');
  turn('none');
}

async function deleteSelected() {
  const project = projectById(draft.projectId);
  if (!project) return;
  const confirmed = await confirmDialog({
    title: `Удалить проект «${project.title}»?`,
    text: 'Проект и все его переводы будут удалены.',
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!confirmed || !hostEl?.isConnected) return;
  try {
    await call('deleteProject', project.id);
    toast('Проект удалён', 'success');
    if (store.get('activeProject')?.id === project.id) store.set('activeProject', null);
    projects = projects.filter((item) => item.id !== project.id);
    store.set('projects', projects);
    intent = projects.length ? 'active' : 'new';
    applyIntent();
    showError('');
    turn('none');
  } catch (error) {
    showError(error.message);
    toast(error.message, 'error');
  }
}

/* -------------------------------------------------------------------------
 * Лист 2. Языки
 * ------------------------------------------------------------------------- */

function languageSlide() {
  const model = currentModel();
  const lead = model
    ? `Языки модели «${model.name}». Саму модель можно сменить на следующем листе.`
    : 'Список языков общий: модель ещё не выбрана.';
  const codes = languageCodes();
  const sourceOptions = [
    el('option', { value: 'auto', text: 'Авто' }),
    ...codes.map((code) => el('option', { value: code, text: langLabel(code) })),
  ];
  if (draft.sourceLang !== 'auto' && !codes.includes(draft.sourceLang)) {
    sourceOptions.push(el('option', {
      value: draft.sourceLang,
      text: `${langLabel(draft.sourceLang)} (вне модели)`,
    }));
  }
  const source = el('select', {
    class: 'select',
    id: 'deck-source',
    ariaLabel: 'Исходный язык',
    onChange: (event) => {
      draft.sourceLang = event.target.value;
      draft.targetLangs = draft.targetLangs.filter((code) => code !== draft.sourceLang);
      refreshLanguageLists();
    },
  }, sourceOptions);
  source.value = draft.sourceLang;

  return el('div', { class: 'stack stack--lg' }, [
    slideHead('Языки', lead),
    el('div', { class: 'field' }, [
      el('label', { class: 'field__label', htmlFor: 'deck-source', text: 'Исходный язык' }),
      source,
    ]),
    el('div', { class: 'field' }, [
      el('div', { class: 'row row--between' }, [
        el('label', { class: 'field__label', text: 'Целевые языки' }),
        el('span', { class: 'text-secondary deck__count', text: countLabel() }),
      ]),
      el('div', { class: 'deck__chosen' }, chosenNodes()),
      el('input', {
        class: 'input',
        type: 'search',
        value: targetFilter,
        placeholder: 'Поиск языка…',
        autocomplete: 'off',
        ariaLabel: 'Поиск языка',
        onInput: (event) => {
          targetFilter = event.target.value.trim().toLowerCase();
          const tray = event.target.closest('.deck__page')?.querySelector('.deck__tray');
          if (tray) tray.replaceChildren(...targetChips());
        },
      }),
      el('div', { class: 'deck__tray' }, targetChips()),
    ]),
  ]);
}

function countLabel() {
  return `${draft.targetLangs.length} выбрано`;
}

function chosenNodes() {
  if (draft.targetLangs.length === 0) {
    return [el('span', { class: 'text-tertiary', text: 'Пока ничего не выбрано' })];
  }
  return draft.targetLangs.map((code) => chip({
    label: langLabel(code),
    onRemove: () => toggleTarget(code),
  }));
}

function targetChips() {
  const codes = languageCodes().filter((code) => code !== draft.sourceLang);
  const needle = targetFilter;
  const filtered = needle
    ? codes.filter((code) => langLabel(code).toLowerCase().includes(needle) || code.includes(needle))
    : codes;
  if (filtered.length === 0) {
    const text = needle ? 'Ничего не найдено.' : 'У этой модели нет списка языков.';
    return [el('span', { class: 'text-tertiary', text })];
  }
  return filtered.map((code) => {
    const selected = draft.targetLangs.includes(code);
    return el('button', {
      class: `lang-chip${selected ? ' is-selected' : ''}`,
      type: 'button',
      ariaPressed: selected ? 'true' : 'false',
      onClick: () => toggleTarget(code),
    }, [
      el('span', { class: 'lang-chip__dot' }),
      el('span', { class: 'lang-chip__label', text: langLabel(code) }),
      el('span', { class: 'lang-chip__code text-tertiary', text: code }),
    ]);
  });
}

function toggleTarget(code) {
  if (draft.targetLangs.includes(code)) {
    draft.targetLangs = draft.targetLangs.filter((item) => item !== code);
  } else {
    draft.targetLangs = [...draft.targetLangs, code];
  }
  refreshLanguageLists();
}

function refreshLanguageLists() {
  const page = hostEl?.querySelector('.deck__page:not(.is-turn-forward)');
  if (!page) return;
  const chosen = page.querySelector('.deck__chosen');
  const tray = page.querySelector('.deck__tray');
  const count = page.querySelector('.deck__count');
  if (chosen) chosen.replaceChildren(...chosenNodes());
  if (tray) tray.replaceChildren(...targetChips());
  if (count) count.textContent = countLabel();
  showError('');
  paintChrome();
}

/* -------------------------------------------------------------------------
 * Лист 3. Модель
 * ------------------------------------------------------------------------- */

function modelSlide() {
  const unsupported = unsupportedCodes();
  const body = [slideHead(
    'Модель',
    'От неё зависит, какие языки можно сохранить в проекте.',
  )];
  if (modelsError) {
    body.push(el('p', { class: 'field__hint field__hint--error', text: modelsError }));
  }
  if (models.length === 0) {
    body.push(emptyState({
      iconName: 'chip',
      title: 'Нет доступных моделей',
      text: 'Скачайте вес в библиотеке моделей, затем вернитесь к проекту.',
      action: button({
        label: 'К моделям',
        variant: 'primary',
        onClick: () => router.showPage('models'),
      }),
    }));
  } else {
    body.push(el('div', { class: 'model-picker' }, models.map((model) => modelOption(model))));
  }
  if (unsupported.length) {
    const names = unsupported.map((code) => langLabel(code)).join(', ');
    body.push(el('p', {
      class: 'deck__note',
      text: `Эта модель не перечисляет: ${names}. Уберите их на листе языков или выберите другую модель.`,
    }));
  }
  const verdict = currentModel()?.compatibility;
  if (verdict?.verdict === 'no' && verdict.reason) {
    body.push(el('p', { class: 'deck__note', text: verdict.reason }));
  }
  return el('div', { class: 'stack stack--lg' }, body);
}

function modelOption(model) {
  const selected = model.id === draft.modelId;
  const meta = [model.sizeLabel, model.quantization].filter(Boolean).join(' · ');
  return el('button', {
    class: `model-option${selected ? ' is-selected' : ''}`,
    type: 'button',
    ariaPressed: selected ? 'true' : 'false',
    onClick: () => {
      draft.modelId = model.id;
      showError('');
      turn('none');
    },
  }, [
    el('div', { class: 'model-option__main' }, [
      el('span', { class: 'model-option__name', text: model.name }),
      meta ? el('span', { class: 'model-option__meta text-tertiary', text: meta }) : null,
    ]),
    badge({
      label: model.installState === 'installed' ? 'Установлена' : 'Доступна',
      tone: model.installState === 'installed' ? 'success' : 'muted',
    }),
  ]);
}

/* -------------------------------------------------------------------------
 * Лист 4. Проверка
 * ------------------------------------------------------------------------- */

function reviewSlide() {
  const model = currentModel();
  const project = projectById(draft.projectId);
  const rows = [
    reviewRow('Название', savedTitle()),
    reviewRow('Исходный', langLabel(draft.sourceLang)),
    reviewNode(
      'Перевод',
      el('div', { class: 'row row--wrap' }, draft.targetLangs.map((code) => chip({ label: langLabel(code) }))),
    ),
    reviewRow('Модель', model ? model.name : (draft.modelId || 'Не выбрана')),
  ];
  if (model) {
    const meta = [model.sizeLabel, model.quantization].filter(Boolean).join(' · ');
    if (meta) rows.push(reviewRow('Вес', meta));
    rows.push(reviewRow(
      'Файл',
      model.installState === 'installed' ? 'Установлен' : 'Ещё не скачан',
    ));
  }
  if (draft.mode === 'existing') {
    rows.push(reviewRow('Документы', `${draft.documentCount} ${plural(draft.documentCount)}`));
    if (draft.updatedAt) rows.push(reviewRow('Изменён', formatDate(draft.updatedAt)));
    const tasks = Object.entries(draft.taskSummary || {});
    if (tasks.length) {
      rows.push(reviewNode(
        'Задачи',
        el('div', { class: 'row row--wrap' }, tasks.map(([status, count]) => badge({
          label: `${TASK_LABELS[status] ?? status}: ${count}`,
          tone: TASK_TONES[status] ?? 'muted',
        }))),
      ));
    }
  }
  const notes = [
    'Контекст и правила перевода остаются в настройках проекта.',
  ];
  if (project && !titleDirty(project) && !settingsDirty(project)) {
    notes.unshift('Настройки совпадают с сохранёнными.');
  }
  return el('div', { class: 'stack stack--lg' }, [
    slideHead('Проверка', 'После этого откроются документы проекта.'),
    el('div', { class: 'deck__review' }, rows),
    ...notes.map((text) => el('p', { class: 'deck__note', text })),
  ]);
}

function reviewRow(label, value) {
  return reviewNode(label, el('div', { text: value }));
}

function reviewNode(label, value) {
  return el('div', { class: 'deck__review-row' }, [
    el('div', { class: 'deck__review-label', text: label }),
    value,
  ]);
}

/* -------------------------------------------------------------------------
 * Сохранение
 * ------------------------------------------------------------------------- */

async function finish() {
  if (!ready || busy || turning) return;
  for (let index = 0; index < 3; index += 1) {
    const problem = validate(index);
    if (problem) {
      showError(problem);
      if (index !== step) {
        step = index;
        turn('none');
      }
      return;
    }
  }
  busy = true;
  paintChrome();
  try {
    if (draft.mode === 'new') {
      const data = await call('createProject', {
        title: draft.title.trim(),
        modelId: draft.modelId,
        sourceLang: draft.sourceLang,
        targetLangs: [...draft.targetLangs],
      });
      store.set('activeProject', data);
      toast('Проект создан', 'success');
    } else {
      const project = projectById(draft.projectId);
      let current = await call('openProject', draft.projectId);
      if (titleDirty(project)) {
        current = await call('renameProject', draft.projectId, draft.title.trim());
      }
      if (settingsDirty(project)) {
        current = await call('updateProjectSettings', {
          modelId: draft.modelId,
          sourceLang: draft.sourceLang,
          targetLangs: [...draft.targetLangs],
          context: project?.context || '',
          rules: project?.rules || '',
        });
      }
      store.set('activeProject', current);
      if (titleDirty(project) || settingsDirty(project)) toast('Проект сохранён', 'success');
    }
    router.showPage('documents');
  } catch (error) {
    showError(error.message);
    toast(error.message, 'error');
  } finally {
    busy = false;
    if (hostEl?.isConnected) paintChrome();
  }
}

function actions() {
  return [
    button({
      label: 'Новый проект',
      variant: 'primary',
      iconName: 'plus',
      onClick: () => openCreateProjectModal(),
    }),
  ];
}

/**
 * Открывает колоду проектов на пустом черновике.
 * Имя сохранено: его вызывает страница документов.
 */
export function openCreateProjectModal() {
  wantNew = true;
  if (router.currentPage() === 'projects' && hostEl?.isConnected) {
    intent = 'new';
    wantNew = false;
    selectNew();
    return;
  }
  router.showPage('projects');
}

router.registerPage('projects', {
  title: 'Проекты',
  subtitle: 'Четыре листа: проект, языки, модель, проверка',
  render,
  destroy,
  actions,
});
