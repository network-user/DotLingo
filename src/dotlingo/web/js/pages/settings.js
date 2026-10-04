/**
 * Страница «Настройки»: настройки активного проекта, устройство,
 * внешний вид и каталоги данных.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import {
  el,
  button,
  toast,
  spinner,
  helpMark,
} from '../components.js';
import { downloadFlow } from './models.js';
import {
  applyMetrics,
  clampPercent,
  SCALE_DEFAULT,
  TEXT_SCALE_MAX,
  TEXT_SCALE_MIN,
  UI_SCALE_MAX,
  UI_SCALE_MIN,
} from '../appearance.js';
import {
  formatGb,
  gpuSummary,
  placementBlocked,
  ramUsedFraction,
  recommendedChoice,
  refreshCatalog,
  threadNote,
} from '../device.js';

/** @type {HTMLElement|null} */
let pageHost = null;

/** @type {Array<() => void>} */
let unsubs = [];

/* -------------------------------------------------------------------------
 * Хелперы
 * ------------------------------------------------------------------------- */

/** Метка языка из store.languages, иначе код в верхнем регистре. */
function langLabel(code) {
  const languages = store.get('languages') || {};
  if (code === 'auto') return 'Авто';
  return languages[code] || code.toUpperCase();
}

/** Модели из store по id. */
function modelById(modelId) {
  return (store.get('models') || []).find((m) => m.id === modelId) ?? null;
}

/* -------------------------------------------------------------------------
 * Состояние формы проекта (unsaved-детект)
 * ------------------------------------------------------------------------- */

/** Текущие значения формы настроек проекта. */
const form = { modelId: '', sourceLang: 'auto', targetLangs: [], context: '', rules: '' };

/** Снимок формы на момент последнего сохранения/рендера. */
let snapshot = '';

/** Строка снимка формы для сравнения. */
function formSnapshot() {
  return [
    form.modelId,
    form.sourceLang,
    [...form.targetLangs].sort().join(','),
    form.context,
    form.rules,
  ].join('\u0000');
}

/** Включает/выключает unsaved-бар и кнопки «Сохранить». */
function markDirty() {
  const dirty = formSnapshot() !== snapshot;
  document.querySelectorAll('.st-unsaved').forEach((bar) => {
    bar.hidden = !dirty;
  });
  document.querySelectorAll('.st-save-btn').forEach((btn) => {
    btn.disabled = !dirty;
  });
}

/** Сохранение настроек проекта. */
async function saveProjectSettings() {
  if (form.targetLangs.length === 0) {
    toast('Выберите хотя бы один целевой язык.', 'error');
    return;
  }
  try {
    const data = await call('updateProjectSettings', {
      modelId: form.modelId,
      sourceLang: form.sourceLang,
      targetLangs: form.targetLangs,
      context: form.context,
      rules: form.rules,
    });
    toast('Настройки проекта сохранены', 'success');
    store.set('activeProject', data);
    snapshot = formSnapshot();
    markDirty();
  } catch (e) {
    toast(e.message, 'error');
  }
}

/** Кнопка «Сохранить» (в баре и в шапке). */
function saveButton(size) {
  const btn = button({
    label: 'Сохранить',
    variant: 'primary',
    size,
    disabled: true,
    onClick: () => saveProjectSettings(),
  });
  btn.classList.add('st-save-btn');
  return btn;
}

/* -------------------------------------------------------------------------
 * Рендер
 * ------------------------------------------------------------------------- */

function render(host) {
  pageHost = host;
  host.append(
    el('div', { class: 'st-page stack' }, [
      el('div', { class: 'page-loading' }, [spinner('lg')]),
    ])
  );
  void refresh(host);
}

/** Полная перерисовка страницы. */
async function refresh(host) {
  const wrap = host.querySelector('.st-page');
  if (!wrap) return;

  const project = store.get('activeProject');
  const parts = [];

  if (project) {
    parts.push(projectPanel(project), unsavedBar());
  } else {
    parts.push(noProjectPanel());
  }

  parts.push(devicePanel(), appearancePanel(), dataPanel());

  wrap.replaceChildren(...parts);

  if (!store.get('dataDirs')) {
    const [dirs, err] = await tryCall('getDataDirs');
    if (!wrap.isConnected || err || !dirs) return;
    store.set('dataDirs', dirs);
    const box = wrap.querySelector('.st-dirs');
    if (box) {
      box.replaceChildren(dirsRow('Проекты', dirs.projectsDir), dirsRow('Модели', dirs.modelsDir));
    }
  }
}

/* -------------------------------------------------------------------------
 * Секция проекта
 * ------------------------------------------------------------------------- */

/** Панель «Проект не выбран». */
function noProjectPanel() {
  return el('section', { class: 'panel' }, [
    el('div', { class: 'panel__header' }, [
      el('h2', { class: 'panel__title', text: 'Проект' }),
    ]),
    el('p', { class: 'st-muted', text: 'Проект не открыт. Языки, контекст и правила задаются у него.' }),
    el('div', { class: 'row' }, [
      button({
        label: 'К переводу',
        variant: 'ghost',
        onClick: () => router.showPage('chat'),
      }),
    ]),
  ]);
}

/** Unsaved-бар под панелью проекта. */
function unsavedBar() {
  const bar = el('div', { class: 'st-unsaved', hidden: true }, [
    el('span', { class: 'st-unsaved__text', text: 'Есть несохранённые изменения' }),
    saveButton('sm'),
  ]);
  return bar;
}

/** Панель настроек активного проекта. */
function projectPanel(project) {
  const models = (store.get('models') || []).filter((m) => m.installed);

  form.modelId = project.modelId || '';
  form.sourceLang = project.sourceLang || 'auto';
  form.targetLangs = [...(project.targetLangs || [])];
  form.context = project.context || '';
  form.rules = project.rules || '';

  const modelSelect = el('select', {
    class: 'select',
    onChange: (e) => {
      form.modelId = e.target.value;
      resyncLanguages();
      markDirty();
    },
  });

  const sourceSelect = el('select', {
    class: 'select',
    onChange: (e) => {
      form.sourceLang = e.target.value;
      form.targetLangs = form.targetLangs.filter((c) => c !== form.sourceLang);
      renderTargets();
      markDirty();
    },
  });

  const counter = el('span', { class: 'text-secondary', text: '0 выбрано' });
  const targetsBox = el('div', { class: 'lang-grid' });

  const contextInput = el('textarea', {
    class: 'textarea',
    rows: 4,
    placeholder: 'Необязательно: предметная область, тон, терминология…',
    onInput: (e) => {
      form.context = e.target.value;
      markDirty();
    },
  });
  contextInput.value = form.context;

  const rulesInput = el('textarea', {
    class: 'textarea',
    rows: 6,
    placeholder: 'Необязательно: правила, которых должен придерживаться перевод…',
    onInput: (e) => {
      form.rules = e.target.value;
      markDirty();
    },
  });
  rulesInput.value = form.rules;

  /** Языковые коды выбранной модели. */
  const codesOf = () => modelById(form.modelId)?.languageCodes ?? [];

  /** Приводит исходный/целевые языки к кодам выбранной модели. */
  function resyncLanguages() {
    const codes = codesOf();
    if (!codes.includes(form.sourceLang)) form.sourceLang = 'auto';
    const kept = form.targetLangs.filter((c) => codes.includes(c) && c !== form.sourceLang);
    if (kept.length === 0) {
      const first = codes.find((c) => c !== form.sourceLang);
      form.targetLangs = first ? [first] : [];
    } else {
      form.targetLangs = kept;
    }
    renderSourceOptions();
    renderTargets();
  }

  /** Заполняет селект исходного языка по кодам модели. */
  function renderSourceOptions() {
    const codes = codesOf();
    sourceSelect.replaceChildren(
      el('option', { value: 'auto', text: 'Авто' }),
      ...codes.map((code) => el('option', { value: code, text: langLabel(code) }))
    );
    sourceSelect.value = codes.includes(form.sourceLang) ? form.sourceLang : 'auto';
  }

  /** Перерисовывает чипы целевых языков. */
  function renderTargets() {
    const codes = codesOf().filter((c) => c !== form.sourceLang);
    counter.textContent = `${form.targetLangs.length} выбрано`;
    targetsBox.replaceChildren(
      ...codes.map((code) => {
        const selected = form.targetLangs.includes(code);
        return el('button', {
          class: `lang-chip${selected ? ' is-selected' : ''}`,
          type: 'button',
          dataset: { code },
          onClick: () => {
            if (selected) {
              form.targetLangs = form.targetLangs.filter((c) => c !== code);
            } else {
              form.targetLangs = [...form.targetLangs, code];
            }
            renderTargets();
            markDirty();
          },
        }, [
          el('span', { class: `lang-chip__dot${selected ? ' is-on' : ''}` }),
          el('span', { class: 'lang-chip__label', text: langLabel(code) }),
          el('span', { class: 'lang-chip__code text-tertiary', text: code }),
        ]);
      }),
      codes.length === 0
        ? el('div', { class: 'st-muted', text: 'У модели нет языков, кроме исходного.' })
        : null
    );
  }

  // Селект моделей: только установленные; текущая модель проекта,
  // если она не установлена, не показывается (селект уходит на первую).
  modelSelect.replaceChildren(
    ...models.map((model) => el('option', { value: model.id, text: model.name }))
  );
  const hasCurrent = models.some((m) => m.id === form.modelId);
  modelSelect.value = hasCurrent ? form.modelId : (models[0]?.id ?? '');
  if (!hasCurrent && models.length > 0) form.modelId = models[0].id;

  // Нормализация языков под выбранную модель (no-op при консистентных данных).
  renderSourceOptions();
  renderTargets();
  if (!hasCurrent && models.length > 0) resyncLanguages();

  snapshot = formSnapshot();

  return el('section', { class: 'panel' }, [
    el('header', { class: 'panel__header' }, [
      el('div', {}, [el('h2', { class: 'panel__title', text: `Проект · ${project.title}` })]),
    ]),
    el('div', { class: 'stack stack--lg' }, [
      el('div', { class: 'field' }, [
        el('label', { class: 'field__label', text: 'Локальная модель' }),
        models.length > 0
          ? modelSelect
          : el('p', { class: 'st-muted', text: 'Установите модель на странице «Модели».' }),
      ]),
      models.length > 0
        ? el('div', { class: 'field' }, [
            el('span', { class: 'field__label field__label--with-help' }, [
              'Исходный язык',
              helpMark('«Авто» не фиксирует язык. Перед переводом модель смотрит образец документа и выбирает код из своего списка.'),
            ]),
            sourceSelect,
          ])
        : null,
      models.length > 0
        ? el('div', { class: 'field' }, [
            el('div', { class: 'row row--between' }, [
              el('label', { class: 'field__label', text: 'Целевые языки' }),
              counter,
            ]),
            targetsBox,
          ])
        : null,
      el('div', { class: 'field' }, [
        el('span', { class: 'field__label field__label--with-help' }, [
          'Контекст проекта',
          helpMark('О чём текст и каким тоном писать. Попадает в запрос перевода, до 800 знаков.'),
        ]),
        contextInput,
      ]),
      el('div', { class: 'field' }, [
        el('span', { class: 'field__label field__label--with-help' }, [
          'Правила перевода',
          helpMark('Что соблюдать: имена, формы, чего избегать. Попадает в тот же запрос, до 800 знаков.'),
        ]),
        rulesInput,
      ]),
    ]),
  ]);
}

/* -------------------------------------------------------------------------
 * Панель устройства
 * ------------------------------------------------------------------------- */

/** Строка «метка / значение» в панели устройства. */
function infoRow(label, value) {
  return el('div', { class: 'st-info-row' }, [
    el('span', { class: 'st-info-row__label text-secondary', text: label }),
    el('span', { class: 'st-info-row__value', text: value }),
  ]);
}

/** Перерисовать только блок устройства, не сбрасывая форму проекта. */
function repaintDevice() {
  const current = pageHost?.querySelector('.st-device');
  if (!current || !pageHost.isConnected) return;
  current.replaceWith(devicePanel());
}

/** Идёт ли повторная проверка устройства. */
let hwChecking = false;

/** Форматирование даты проверки: «29 сент 2026 г., 14:05». */
function formatDateTime(iso) {
  if (!iso) return '';
  try {
    return new Intl.DateTimeFormat('ru-RU', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return '';
  }
}

/** Горизонтальный измеритель (доля 0..1). */
function meter(fraction) {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0));
  return el('div', { class: 'st-meter' }, [
    el('div', { class: 'st-meter__track' }, [
      el('div', { class: 'st-meter__fill', style: { width: `${Math.round(clamped * 100)}%` } }),
    ]),
  ]);
}

/** Запуск повторной проверки устройства. */
async function rerunDetection() {
  if (hwChecking) return;
  hwChecking = true;
  repaintDevice();
  try {
    await call('detectHardware');
  } catch (e) {
    hwChecking = false;
    toast(e.message, 'error');
    repaintDevice();
  }
}

/** Одна цифра замера: подпись, значение, пояснение, необязательный измеритель. */
function metric(label, value, sub, fraction) {
  return el('div', { class: 'st-metric' }, [
    el('span', { class: 'st-metric__label', text: label }),
    el('span', { class: 'st-metric__value', text: value }),
    sub ? el('span', { class: 'st-metric__sub', text: sub }) : null,
    fraction == null ? null : meter(fraction),
  ]);
}

/** Рекомендованная модель и действие: скачать, открыть каталог или отметить, что вес уже стоит. */
function fitBlock() {
  const { model, reason } = recommendedChoice();
  const title = el('h3', { class: 'st-fit__title', text: 'Модель для этого устройства' });
  if (!store.get('hardware')) {
    return el('div', { class: 'st-fit' }, [
      title,
      el('p', { class: 'st-fit__reason', text: 'Сначала нужна проверка устройства.' }),
    ]);
  }
  if (store.get('recommendation') == null) {
    return el('div', { class: 'st-fit' }, [
      title,
      el('p', { class: 'st-fit__reason', text: 'Сопоставляем каталог с памятью и диском.' }),
    ]);
  }
  if (!model) {
    return el('div', { class: 'st-fit' }, [
      title,
      el('p', {
        class: 'st-fit__reason',
        text: reason || 'В каталоге нет модели с известным размером и оценкой памяти.',
      }),
      button({
        label: 'Открыть каталог',
        variant: 'ghost',
        onClick: () => router.showPage('models'),
      }),
    ]);
  }

  const blocked = placementBlocked(model) && !model.installed;
  const nodes = [
    title,
    el('p', { class: 'st-fit__name', text: model.name }),
    el('p', {
      class: 'st-fit__reason',
      text: blocked
        ? model.compatibility?.reason || reason
        : reason || `Лицензия: ${model.license}.`,
    }),
  ];
  if (model.installed) {
    nodes.push(el('p', { class: 'st-fit__status', text: 'Уже на диске.' }));
  } else if (blocked) {
    nodes.push(
      button({
        label: 'Открыть каталог',
        variant: 'ghost',
        onClick: () => router.showPage('models'),
      }),
    );
  } else {
    const download = button({
      label: `Скачать ${model.sizeLabel}`,
      variant: 'primary',
      onClick: () => void downloadFlow(model),
    });
    nodes.push(download);
    nodes.push(
      el('p', {
        class: 'st-fit__status',
        text: `Лицензия: ${model.license}. Файл проверяется по размеру и SHA-256.`,
      }),
    );
  }
  return el('div', { class: 'st-fit' }, nodes);
}

/** Панель «Устройство»: замеры, runtime и модель, которую можно поставить. */
function devicePanel() {
  const hw = store.get('hardware');
  const rerunButton = button({
    label: hwChecking ? 'Проверяем…' : hw ? 'Проверить снова' : 'Проверить устройство',
    variant: 'ghost',
    iconName: 'refresh',
    disabled: hwChecking,
    onClick: () => void rerunDetection(),
  });

  const header = el('header', { class: 'panel__header' }, [
    el('div', {}, [
      el('h2', { class: 'panel__title', text: 'Устройство' }),
      hw?.detectedAt
        ? el('span', {
            class: 'st-hw__stamp text-tertiary',
            text: `проверено ${formatDateTime(hw.detectedAt)}`,
          })
        : null,
    ]),
    el('div', { class: 'panel__actions' }, [rerunButton]),
  ]);

  let body;
  if (hwChecking) {
    body = el('div', { class: 'st-hw-checking' }, [
      spinner(),
      el('span', { class: 'text-secondary', text: 'Считываем память, диск и runtime.' }),
    ]);
  } else if (!hw) {
    body = el('div', { class: 'st-hw-body st-hw-body--empty' }, [
      el('p', {
        class: 'st-muted',
        text: 'Проверка локальная: RAM, диск, процессор, NVIDIA и runtime llama.cpp.',
      }),
    ]);
  } else {
    const used = ramUsedFraction(hw);
    body = el('div', { class: 'st-hw-body' }, [
      el('div', { class: 'st-metrics' }, [
        metric(
          'RAM свободно',
          formatGb(hw.ramAvailableGb),
          Number.isFinite(hw.ramTotalGb)
            ? `из ${formatGb(hw.ramTotalGb)}${used == null ? '' : `, занято ${Math.round(used * 100)}%`}`
            : '',
          used,
        ),
        metric('Диск свободен', formatGb(hw.diskFreeGb), 'каталог моделей'),
        metric(
          'CPU',
          hw.cpuThreads ? String(hw.cpuThreads) : '—',
          'логических процессоров',
        ),
      ]),
      infoRow('GPU', gpuSummary(hw)),
      !(hw.gpuNames ?? []).length
        ? el('p', { class: 'st-hw__note text-tertiary', text: 'Проверка смотрит только NVIDIA.' })
        : null,
      infoRow(
        'Runtime',
        hw.llamaRuntimeAvailable ? 'llama.cpp доступен' : 'llama.cpp не установлен',
      ),
      !hw.llamaRuntimeAvailable
        ? el('p', {
            class: 'st-hw__hint',
            text: 'Перевод запустится после установки runtime llama.cpp. Вес модели можно хранить и без него.',
          })
        : null,
      el('p', { class: 'st-hw__note text-tertiary', text: threadNote() }),
      fitBlock(),
    ]);
  }

  return el('section', { class: 'panel st-device' }, [header, body]);
}

/* -------------------------------------------------------------------------
 * Панель внешнего вида
 * ------------------------------------------------------------------------- */

/** Панель «Внешний вид»: тема и уменьшение движения. */
function appearancePanel() {
  const segment = el('div', { class: 'st-segment', role: 'radiogroup', 'aria-label': 'Тема' });

  for (const value of ['dark', 'light']) {
    const isActive = (store.get('theme') ?? 'dark') === value;
    segment.append(
      el('button', {
        class: `st-segment__btn${isActive ? ' is-selected' : ''}`,
        type: 'button',
        dataset: { theme: value },
        text: value === 'dark' ? 'Тёмная' : 'Светлая',
        onClick: () => {
          store.set('theme', value);
          document.documentElement.dataset.theme = value;
          segment.querySelectorAll('.st-segment__btn').forEach((b) => {
            b.classList.toggle('is-selected', b.dataset.theme === value);
          });
          call('setPreferences', { theme: value }).catch(() => {});
        },
      })
    );
  }

  const scaleMetric = metricField(
    'Масштаб',
    'Увеличивает или уменьшает всё окно: кнопки, поля, панель и буквы. 100% это обычный размер.',
    'uiScale',
    'ui_scale',
    UI_SCALE_MIN,
    UI_SCALE_MAX,
  );
  const textMetric = metricField(
    'Размер текста',
    'Меняет только буквы. Кнопки и отступы остаются такими, как задал масштаб.',
    'textScale',
    'text_scale',
    TEXT_SCALE_MIN,
    TEXT_SCALE_MAX,
  );

  const reduceMotion = el('input', {
    class: 'st-checkbox',
    type: 'checkbox',
    checked: Boolean(store.get('reduceMotion')),
    onChange: (e) => {
      store.set('reduceMotion', e.target.checked);
      document.documentElement.dataset.reduceMotion = e.target.checked ? 'true' : 'false';
      call('setPreferences', { reduce_motion: e.target.checked }).catch(() => {});
    },
  });

  return el('section', { class: 'panel' }, [
    el('header', { class: 'panel__header' }, [
      el('div', {}, [el('h2', { class: 'panel__title', text: 'Внешний вид' })]),
    ]),
    el('div', { class: 'stack' }, [
      el('div', { class: 'field' }, [
        el('label', { class: 'field__label', text: 'Тема' }),
        segment,
      ]),
      el('label', { class: 'st-check-row' }, [
        reduceMotion,
        el('span', { class: 'st-check-row__label', text: 'Уменьшить движение и переходы' }),
      ]),
      scaleMetric.element,
      textMetric.element,
      el('div', { class: 'st-metric__reset' }, [
        button({
          label: 'Сбросить',
          variant: 'ghost',
          size: 'sm',
          onClick: () => resetMetrics(scaleMetric, textMetric),
        }),
        el('span', {
          class: 'st-metric__reset-note',
          text: 'Вернёт масштаб и размер текста к 100%.',
        }),
      ]),
    ]),
  ]);
}

/** Оба регулятора сразу к 100% и одна запись в настройках. */
function resetMetrics(scaleMetric, textMetric) {
  scaleMetric.setValue(SCALE_DEFAULT, false);
  textMetric.setValue(SCALE_DEFAULT, false);
  applyMetrics(SCALE_DEFAULT, SCALE_DEFAULT);
  call('setPreferences', { ui_scale: SCALE_DEFAULT, text_scale: SCALE_DEFAULT }).catch(() => {});
}

/** Ползунок и число: шаг 1%, значение сразу применяется и сохраняется. */
function metricField(label, help, storeKey, prefKey, min, max) {
  const current = clampPercent(store.get(storeKey), min, max, SCALE_DEFAULT);
  const readout = el('span', { class: 'st-metric__readout', text: `${current}%` });
  const range = el('input', {
    class: 'st-metric__range',
    type: 'range',
    min: String(min),
    max: String(max),
    step: '1',
    value: String(current),
    ariaLabel: label,
  });
  const number = el('input', {
    class: 'input st-metric__number',
    type: 'number',
    min: String(min),
    max: String(max),
    step: '1',
    value: String(current),
    ariaLabel: `${label}, проценты`,
  });

  const publish = (raw, save = true) => {
    const next = clampPercent(raw, min, max, current);
    range.value = String(next);
    number.value = String(next);
    readout.textContent = `${next}%`;
    store.set(storeKey, next);
    if (!save) return;
    applyMetrics(
      clampPercent(store.get('uiScale'), UI_SCALE_MIN, UI_SCALE_MAX, SCALE_DEFAULT),
      clampPercent(store.get('textScale'), TEXT_SCALE_MIN, TEXT_SCALE_MAX, SCALE_DEFAULT),
    );
    call('setPreferences', { [prefKey]: next }).catch(() => {});
  };

  range.addEventListener('input', () => publish(range.value));
  number.addEventListener('change', () => publish(number.value));

  return {
    setValue: (value, save = true) => publish(value, save),
    element: el('div', { class: 'field' }, [
    el('div', { class: 'st-metric__head' }, [
      el('span', { class: 'field__label field__label--with-help' }, [
        label,
        helpMark(help),
      ]),
      readout,
    ]),
    el('div', { class: 'st-metric__controls' }, [range, number]),
  ]),
  };
}

/* -------------------------------------------------------------------------
 * Панель данных
 * ------------------------------------------------------------------------- */

/** Панель «Данные»: каталоги и кнопки «Открыть». */
function dataPanel() {
  const dirs = store.get('dataDirs');
  const box = el('div', { class: 'st-dirs stack' });

  if (dirs) {
    box.append(dirsRow('Проекты', dirs.projectsDir), dirsRow('Модели', dirs.modelsDir));
  } else {
    box.append(el('div', { class: 'page-loading' }, [spinner()]));
  }

  return el('section', { class: 'panel' }, [
    el('header', { class: 'panel__header' }, [
      el('div', {}, [el('h2', { class: 'panel__title', text: 'Данные' })]),
    ]),
    box,
    el('p', {
      class: 'st-data-note text-tertiary',
      text: 'Исходный файл не перезаписывается. Переводы и экспорты лежат отдельно.',
    }),
  ]);
}

/** Строка каталога с кнопкой «Открыть». */
function dirsRow(label, path) {
  return el('div', { class: 'st-dir-row' }, [
    el('span', { class: 'st-dir-row__label text-secondary', text: label }),
    el('code', { class: 'st-dir-row__path', text: path || '—' }),
    button({
      label: 'Открыть',
      variant: 'ghost',
      size: 'sm',
      disabled: !path,
      onClick: async () => {
        try {
          await call('revealPath', path);
        } catch (e) {
          toast(e.message, 'error');
        }
      },
    }),
  ]);
}

/* -------------------------------------------------------------------------
 * Действия шапки
 * ------------------------------------------------------------------------- */

function actions() {
  if (!store.get('activeProject')) return [];
  return [saveButton()];
}

/* -------------------------------------------------------------------------
 * Жизненный цикл
 * ------------------------------------------------------------------------- */

function destroy() {
  unsubs.forEach((off) => off());
  unsubs = [];
  pageHost = null;
  hwChecking = false;
}

/** Подписки на устройство и каталог. Форму проекта не перерисовывают. */
function wireHardwareEvents(host) {
  unsubs.forEach((off) => off());
  unsubs = [
    store.on('hardware_detected', () => {
      hwChecking = false;
      if (!host.isConnected) return;
      repaintDevice();
    }),
    store.on('models_refreshed', () => {
      if (!host.isConnected || hwChecking) return;
      repaintDevice();
    }),
    store.on('download_done', () => {
      if (!host.isConnected) return;
      void refreshCatalog();
    }),
  ];
}

router.registerPage('settings', {
  title: 'Настройки',
  subtitle: 'Языки проекта, вид и папки',
  help: 'Контекст и правила уходят в запрос перевода. «Авто» не фиксирует исходный язык: перед переводом модель смотрит образец документа. Проверка устройства смотрит память, диск, процессор, NVIDIA и llama.cpp. Исходные файлы не перезаписываются.',
  render: (host) => {
    render(host);
    wireHardwareEvents(host);
  },
  destroy,
  actions,
});
