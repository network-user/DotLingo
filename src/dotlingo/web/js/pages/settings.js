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
  formatBytes,
} from '../components.js';

/** @type {(() => void)|null} отписка от hardware_detected */
let unsubHardware = null;

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
  unsubHardware?.();
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
    el('p', { class: 'st-muted', text: 'Проект не выбран.' }),
    el('div', { class: 'row' }, [
      button({
        label: 'К проектам',
        variant: 'ghost',
        onClick: () => router.showPage('projects'),
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
            el('label', { class: 'field__label', text: 'Исходный язык' }),
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
        el('label', { class: 'field__label', text: 'Контекст проекта' }),
        contextInput,
      ]),
      el('div', { class: 'field' }, [
        el('label', { class: 'field__label', text: 'Правила перевода' }),
        rulesInput,
      ]),
    ]),
  ]);
}

/* -------------------------------------------------------------------------
 * Панель устройства
 * ------------------------------------------------------------------------- */

/** Строка «метка — значение» в панели устройства. */
function infoRow(label, value) {
  return el('div', { class: 'st-info-row' }, [
    el('span', { class: 'st-info-row__label text-secondary', text: label }),
    el('span', { class: 'st-info-row__value', text: value }),
  ]);
}

/** Панель «Устройство» из store.hardware. */
function devicePanel() {
  const hw = store.get('hardware');

  const lines = [];
  if (!hw) {
    lines.push(el('p', { class: 'st-muted', text: 'Устройство ещё не проверено.' }));
  } else {
    const vramList = (hw.gpuVramGb ?? []).filter((v) => Number.isFinite(v) && v > 0);
    lines.push(infoRow('Профиль', hw.profile || '—'));
    lines.push(infoRow('CPU', `${hw.cpuThreads} потоков`));
    lines.push(
      infoRow(
        'RAM',
        `${formatBytes(hw.ramTotalGb * 1024 ** 3)} всего · доступно ${formatBytes(hw.ramAvailableGb * 1024 ** 3)}`
      )
    );
    lines.push(infoRow('Диск', `свободно ${formatBytes(hw.diskFreeGb * 1024 ** 3)}`));
    lines.push(
      infoRow(
        'GPU',
        hw.gpuNames?.length
          ? `${hw.gpuNames.join(', ')} · VRAM ${formatBytes(vramList.reduce((a, b) => a + b, 0) * 1024 ** 3)}`
          : 'не найден'
      )
    );
    lines.push(
      infoRow('Runtime llama.cpp', hw.llamaRuntimeAvailable ? 'доступен' : 'не найден')
    );
    lines.push(infoRow('GPU offload', 'выключен'));
  }

  return el('section', { class: 'panel' }, [
    el('header', { class: 'panel__header' }, [
      el('div', {}, [el('h2', { class: 'panel__title', text: 'Устройство' })]),
      el('div', { class: 'panel__actions' }, [
        button({
          label: 'Проверить устройство',
          variant: 'ghost',
          iconName: 'refresh',
          onClick: async () => {
            try {
              await call('detectHardware');
              toast('Проверка устройства запущена', 'info');
            } catch (e) {
              toast(e.message, 'error');
            }
          },
        }),
      ]),
    ]),
    el('div', { class: 'st-info-list' }, lines),
  ]);
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

  const reduceMotion = el('input', {
    class: 'st-checkbox',
    type: 'checkbox',
    checked: Boolean(store.get('reduceMotion')),
    onChange: (e) => {
      store.set('reduceMotion', e.target.checked);
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
    ]),
  ]);
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
      text: 'Оригиналы остаются неизменяемыми; переводы и экспорты хранятся отдельно.',
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
  unsubHardware?.();
  unsubHardware = null;
}

/** Подписка на завершение проверки устройства; навешивается после render. */
function wireHardwareEvents(host) {
  unsubHardware?.();
  unsubHardware = store.on('hardware_detected', () => {
    if (!host.isConnected) return;
    void refresh(host);
  });
}

router.registerPage('settings', {
  title: 'Настройки',
  subtitle: 'Проект и приложение',
  render: (host) => {
    render(host);
    wireHardwareEvents(host);
  },
  destroy,
  actions,
});
