/**
 * Страница «Проекты»: четыре шага (проект, языки, модель, проверка).
 * Переход между шагами - короткий слайд, не переворот страницы.
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
  helpMark,
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
let localeHints = { keyboard: '', interface: '' };
/** @type {{ source: string, targets: string[], kind: string, reason: string }|null} */
let suggestion = null;
let stopStore = null;

/** Часовые пояса, по которым язык читается однозначно. Смешанные зоны не используем. */
const ZONE_LANGUAGE = {
  'Europe/Kaliningrad': 'ru',
  'Europe/Moscow': 'ru',
  'Europe/Simferopol': 'ru',
  'Europe/Kirov': 'ru',
  'Europe/Astrakhan': 'ru',
  'Europe/Volgograd': 'ru',
  'Europe/Saratov': 'ru',
  'Europe/Ulyanovsk': 'ru',
  'Europe/Samara': 'ru',
  'Asia/Yekaterinburg': 'ru',
  'Asia/Omsk': 'ru',
  'Asia/Novosibirsk': 'ru',
  'Asia/Barnaul': 'ru',
  'Asia/Tomsk': 'ru',
  'Asia/Novokuznetsk': 'ru',
  'Asia/Krasnoyarsk': 'ru',
  'Asia/Irkutsk': 'ru',
  'Asia/Chita': 'ru',
  'Asia/Yakutsk': 'ru',
  'Asia/Khandyga': 'ru',
  'Asia/Vladivostok': 'ru',
  'Asia/Ust-Nera': 'ru',
  'Asia/Magadan': 'ru',
  'Asia/Sakhalin': 'ru',
  'Asia/Srednekolymsk': 'ru',
  'Asia/Kamchatka': 'ru',
  'Asia/Anadyr': 'ru',
  'Asia/Tokyo': 'ja',
  'Asia/Seoul': 'ko',
};

const SLIDE_FALLBACK_MS = 420;

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
  if (code === 'auto') return 'Автоопределение';
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

function relativeDate(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const start = (value) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const day = Math.round((start(new Date()) - start(date)) / 86400000);
  if (day === 0) return 'сегодня';
  if (day === 1) return 'вчера';
  if (day > 1 && day < 7) return `${day} дн. назад`;
  return formatDate(iso);
}

function normalizeLocale(value) {
  const raw = String(value || '').trim().replace(/_/g, '-');
  if (!raw) return '';
  const lower = raw.toLowerCase();
  if (lower === 'zh-tw' || lower === 'zh-hk' || lower === 'zh-mo' || lower === 'zh-hant') {
    return 'zh-Hant';
  }
  if (lower === 'zh' || lower.startsWith('zh-')) return 'zh';
  if (lower === 'nb' || lower === 'nn' || lower === 'no'
    || lower.startsWith('nb-') || lower.startsWith('nn-') || lower.startsWith('no-')) {
    return 'no';
  }
  if (lower === 'fil' || lower === 'tl' || lower.startsWith('fil-') || lower.startsWith('tl-')) {
    return 'tl';
  }
  return lower.split('-')[0];
}

function timezoneLanguage() {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    return ZONE_LANGUAGE[zone] || '';
  } catch {
    return '';
  }
}

function readingLanguage(allowed) {
  const keyboard = normalizeLocale(localeHints.keyboard);
  const ui = normalizeLocale(localeHints.interface) || normalizeLocale(navigator.language || '');
  const zone = timezoneLanguage();
  const take = (code, kind) => (code && allowed.has(code) ? { code, kind } : null);
  if (keyboard && keyboard !== 'en') {
    const found = take(keyboard, 'keyboard');
    if (found) return found;
  }
  if (ui && ui !== 'en') {
    const found = take(ui, 'interface');
    if (found) return found;
  }
  if (keyboard) {
    const found = take(keyboard, 'keyboard');
    if (found) return found;
  }
  if (ui) {
    const found = take(ui, 'interface');
    if (found) return found;
  }
  return take(zone, 'timezone');
}

function suggestionText() {
  if (!suggestion || !suggestionMatches()) return '';
  if (suggestion.kind === 'history') return suggestion.reason;
  const name = langLabel(suggestion.targets[0] || '');
  if (suggestion.kind === 'keyboard') return `${name} по раскладке клавиатуры`;
  if (suggestion.kind === 'interface') return `${name} по языку системы`;
  if (suggestion.kind === 'timezone') return `${name} по часовому поясу`;
  return '';
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

function suggestionMatches() {
  if (!suggestion) return false;
  if (draft.sourceLang !== suggestion.source) return false;
  if (draft.targetLangs.length !== suggestion.targets.length) return false;
  return draft.targetLangs.every((code, index) => code === suggestion.targets[index]);
}

function suggestForNew() {
  const allowed = new Set(languageCodes());
  const recent = projects.find((project) => (project.targetLangs || []).length > 0);
  if (recent) {
    let source = recent.sourceLang || 'auto';
    if (source !== 'auto' && !allowed.has(source)) source = 'auto';
    const targets = [];
    (recent.targetLangs || []).forEach((code) => {
      if (allowed.has(code) && code !== source && !targets.includes(code)) targets.push(code);
    });
    if (targets.length) {
      draft.sourceLang = source;
      draft.targetLangs = targets;
      suggestion = {
        source,
        targets: [...targets],
        kind: 'history',
        reason: `Как в проекте «${recent.title || 'без названия'}»`,
      };
      return;
    }
  }
  const reading = readingLanguage(allowed);
  if (reading) {
    draft.sourceLang = 'auto';
    draft.targetLangs = [reading.code];
    suggestion = { source: 'auto', targets: [reading.code], kind: reading.kind, reason: '' };
    return;
  }
  draft.sourceLang = 'auto';
  draft.targetLangs = [];
  suggestion = null;
}

function applyNewDraft(title) {
  draft = emptyDraft();
  draft.title = title;
  draft.modelId = defaultModelId();
  suggestForNew();
}

function alignLanguagesToModel() {
  const allowed = new Set(languageCodes());
  if (draft.sourceLang !== 'auto' && !allowed.has(draft.sourceLang)) draft.sourceLang = 'auto';
  draft.targetLangs = draft.targetLangs.filter(
    (code) => allowed.has(code) && code !== draft.sourceLang,
  );
  if (draft.mode === 'new' && draft.targetLangs.length === 0) suggestForNew();
}

function applyIntent() {
  if (intent === 'new' || projects.length === 0) {
    const title = intent === 'new' ? draft.title : '';
    applyNewDraft(title);
    step = 0;
    return;
  }
  const activeId = store.get('activeProject')?.id;
  applyProject(projectById(activeId) || projects[0]);
  suggestion = null;
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
  localeHints = { keyboard: '', interface: '' };
  suggestion = null;
  draft = emptyDraft();
  step = 0;
  stopStore?.();
  stopStore = store.subscribe((key, value, prev) => {
    if (key !== 'languages' || !ready || !hostEl?.isConnected || turning || busy) return;
    const wasEmpty = !prev || Object.keys(prev).length === 0;
    const hasNames = value && Object.keys(value).length > 0;
    if (!wasEmpty || !hasNames) return;
    const typing = document.activeElement;
    const caret = typing?.classList?.contains('deck-title') ? typing.selectionStart : null;
    turn('none');
    if (caret == null) return;
    const input = hostEl?.querySelector('.deck-title');
    input?.focus();
    input?.setSelectionRange(caret, caret);
  });
  host.append(shell());
  void load();
}

function destroy() {
  loadGeneration += 1;
  turning = false;
  busy = false;
  stopStore?.();
  stopStore = null;
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

  const [
    [projectData, projectErr],
    [modelData, modelErr],
    [hintData],
  ] = await Promise.all([
    tryCall('listProjects'),
    tryCall('listModels'),
    tryCall('getLocaleHints'),
  ]);
  if (generation !== loadGeneration || !hostEl?.isConnected) return;
  localeHints = {
    keyboard: typeof hintData?.keyboard === 'string' ? hintData.keyboard : '',
    interface: typeof hintData?.interface === 'string' ? hintData.interface : '',
  };
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
  const controls = [
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
  ].filter(Boolean);
  nav.replaceChildren(...controls);
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
  const forward = direction === 'forward';
  const leaveName = forward ? 'deck-leave-forward' : 'deck-leave-back';
  stage.append(next);
  const height = Math.max(current.offsetHeight, next.offsetHeight);
  if (height > 0) stage.style.height = `${height}px`;
  stage.classList.add('is-sliding');
  current.classList.add(forward ? 'is-leave-forward' : 'is-leave-back');
  next.classList.add(forward ? 'is-enter-forward' : 'is-enter-back');

  const finishTurn = () => {
    if (!turning) return;
    turning = false;
    stage.classList.remove('is-sliding');
    stage.style.height = '';
    if (!hostEl?.isConnected) return;
    current.remove();
    next.classList.remove('is-enter-forward', 'is-enter-back');
    paintChrome();
    const title = next.querySelector('h2');
    title?.setAttribute('tabindex', '-1');
    title?.focus({ preventScroll: true });
  };
  current.addEventListener('animationend', (event) => {
    if (event.target !== current || event.animationName !== leaveName) return;
    finishTurn();
  });
  setTimeout(finishTurn, SLIDE_FALLBACK_MS);
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
 * Шаг 1. Проект
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

  const hint = suggestionText();
  return el('div', { class: 'stack stack--lg' }, [
    slideHead(
      'Проект',
      'Выберите существующий или начните новый. Дальше языки и модель.',
    ),
    el('div', { class: 'field' }, [
      el('label', { class: 'field__label', htmlFor: 'deck-title', text: 'Название' }),
      titleInput,
    ]),
    filter,
    el('div', { class: 'deck__list' }, projectRows()),
    el('p', { class: 'deck__hint', text: hint }),
    draft.mode === 'existing'
      ? el('div', { class: 'deck__tools' }, [
        button({
          label: 'Удалить',
          variant: 'danger',
          size: 'sm',
          onClick: () => { void deleteSelected(); },
        }),
      ])
      : null,
  ]);
}

function projectRows() {
  const needle = projectFilter;
  const rows = [
    pickButton({
      selected: draft.mode === 'new',
      title: 'Новый проект',
      meta: newProjectMeta(),
      onClick: () => selectNew({ keepTitle: true }),
    }),
  ];
  projects.forEach((project) => {
    const title = project.title || 'Проект';
    if (needle && !title.toLowerCase().includes(needle)) return;
    const targets = (project.targetLangs || []).map((code) => langLabel(code)).join(', ');
    const docs = `${project.documentCount ?? 0} ${plural(project.documentCount ?? 0)}`;
    const when = relativeDate(project.updatedAt);
    const pair = `${langLabel(project.sourceLang || 'auto')} → ${targets || 'языки не заданы'}`;
    rows.push(pickButton({
      selected: draft.mode === 'existing' && draft.projectId === project.id,
      title,
      meta: [pair, docs, when].filter(Boolean).join(' · '),
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

function newProjectMeta() {
  if (!suggestionMatches() || draft.targetLangs.length === 0) {
    return 'Пустой проект, без документов';
  }
  const targets = draft.targetLangs.map((code) => langLabel(code)).join(', ');
  return `${langLabel(draft.sourceLang)} → ${targets}`;
}

function selectNew({ keepTitle = false } = {}) {
  const title = keepTitle && draft.mode === 'new' ? draft.title : '';
  applyNewDraft(title);
  step = 0;
  targetFilter = '';
  showError('');
  turn('none');
  hostEl?.querySelector('.deck-title')?.focus();
}

function selectExisting(project) {
  applyProject(project);
  suggestion = null;
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
 * Шаг 2. Языки
 * ------------------------------------------------------------------------- */

function languageSlide() {
  const model = currentModel();
  const lead = model
    ? `Языки из списка «${model.name}». Сменить модель можно на следующем шаге.`
    : 'Модель ещё не выбрана, поэтому список общий.';
  const codes = languageCodes();
  const sourceOptions = [
    el('option', { value: 'auto', text: 'Автоопределение' }),
    ...sortedCodes(codes).map((code) => el('option', { value: code, text: langLabel(code) })),
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
    el('p', {
      class: 'deck__hint',
      text: suggestionText(),
    }),
    el('div', { class: 'field' }, [
      el('label', { class: 'field__label field__label--with-help', htmlFor: 'deck-source' }, [
        'Исходный язык',
        helpMark('«Автоопределение» не фиксирует язык. Перед переводом модель смотрит образец документа и выбирает код из своего списка. Если язык известен, укажите его сами.'),
      ]),
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

function sortedCodes(codes) {
  return [...codes].sort((left, right) => langLabel(left).localeCompare(langLabel(right), 'ru'));
}

function matchesLanguage(code, needle) {
  if (!needle) return true;
  return langLabel(code).toLowerCase().includes(needle) || code.toLowerCase().includes(needle);
}

function frequentCodes(codes) {
  const allowed = new Set(codes);
  const picked = [];
  const add = (code) => {
    if (!code || code === draft.sourceLang || !allowed.has(code) || picked.includes(code)) return;
    if (picked.length >= 8) return;
    picked.push(code);
  };
  (suggestion?.targets || []).forEach(add);
  projects.forEach((project) => {
    (project.targetLangs || []).forEach(add);
  });
  add(readingLanguage(allowed)?.code);
  return picked;
}

function languageChip(code) {
  const selected = draft.targetLangs.includes(code);
  return el('button', {
    class: `lang-chip${selected ? ' is-selected' : ''}`,
    type: 'button',
    ariaPressed: selected ? 'true' : 'false',
    onClick: () => toggleTarget(code),
  }, [
    el('span', { class: 'lang-chip__dot' }),
    el('span', { class: 'lang-chip__label', text: langLabel(code) }),
    el('span', { class: 'lang-chip__code', text: code }),
  ]);
}

function targetChips() {
  const codes = languageCodes().filter((code) => code !== draft.sourceLang);
  const needle = targetFilter;
  if (needle) {
    const filtered = sortedCodes(codes.filter((code) => matchesLanguage(code, needle)));
    if (filtered.length === 0) {
      return [el('span', { class: 'text-tertiary', text: 'Ничего не найдено.' })];
    }
    return filtered.map(languageChip);
  }
  if (codes.length === 0) {
    return [el('span', { class: 'text-tertiary', text: 'У этой модели нет списка языков.' })];
  }
  const sorted = sortedCodes(codes);
  const frequent = sorted.length > 12 ? frequentCodes(codes) : [];
  if (frequent.length === 0) return sorted.map(languageChip);
  const rest = sorted.filter((code) => !frequent.includes(code));
  return [
    el('span', { class: 'deck__group', text: 'Частые' }),
    ...frequent.map(languageChip),
    el('span', { class: 'deck__group', text: 'Все языки' }),
    ...rest.map(languageChip),
  ];
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
  const page = hostEl?.querySelector('.deck__page:not(.is-leave-forward):not(.is-leave-back)');
  if (!page) return;
  const chosen = page.querySelector('.deck__chosen');
  const tray = page.querySelector('.deck__tray');
  const count = page.querySelector('.deck__count');
  const hint = page.querySelector('.deck__hint');
  if (chosen) chosen.replaceChildren(...chosenNodes());
  if (tray) tray.replaceChildren(...targetChips());
  if (count) count.textContent = countLabel();
  if (hint) hint.textContent = suggestionText();
  showError('');
  paintChrome();
}

/* -------------------------------------------------------------------------
 * Шаг 3. Модель
 * ------------------------------------------------------------------------- */

function modelSlide() {
  const unsupported = unsupportedCodes();
  const body = [slideHead(
    'Модель',
    'От списка модели зависит, какие языки можно сохранить.',
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
      text: `Эта модель не перечисляет: ${names}. Уберите их на шаге «Языки» или выберите другую модель.`,
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
  const meta = [
    model.sizeLabel,
    model.quantization,
    model.id === recommendationId ? 'рекомендуется' : '',
  ].filter(Boolean).join(' · ');
  return el('button', {
    class: `model-option${selected ? ' is-selected' : ''}`,
    type: 'button',
    ariaPressed: selected ? 'true' : 'false',
    onClick: () => {
      draft.modelId = model.id;
      alignLanguagesToModel();
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
 * Шаг 4. Проверка
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
    if (draft.updatedAt) rows.push(reviewRow('Изменён', relativeDate(draft.updatedAt)));
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
  subtitle: 'Открыть проект или собрать новый',
  help: 'Четыре шага: название, языки, модель и проверка перед сохранением. Уже существующий проект можно открыть сразу. Список языков берётся из выбранной модели.',
  render,
  destroy,
  actions,
});
