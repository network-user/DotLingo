/**
 * Страница «Проекты»: сетка glass-карточек, создание/переименование/удаление.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { el, button, badge, chip, toast, emptyState, modal, confirmDialog, spinner } from '../components.js';

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

/** Короткая метка языка: название из store.languages или код. */
function langLabel(code) {
  const languages = store.get('languages') || {};
  return languages[code] || code.toUpperCase();
}

const MONTHS_SHORT = [
  'янв', 'фев', 'мар', 'апр', 'мая', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
];

/** Дата в формате «29 сент 2026, 14:05». */
function formatDate(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const month = MONTHS_SHORT[date.getMonth()];
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${date.getDate()} ${month} ${date.getFullYear()}, ${hh}:${mm}`;
}

/* -------------------------------------------------------------------------
 * Рендер
 * ------------------------------------------------------------------------- */

function render(host) {
  host.append(el('div', { class: 'project-grid' }));
  void refresh(host);
}

/** Загружает список и перерисовывает сетку. */
async function refresh(host) {
  const grid = host.querySelector('.project-grid');
  if (!grid) return;

  // Спиннер только если мост не ответил сразу. Иначе первый кадр - чёрное поле.
  const spinnerTimer = setTimeout(() => {
    if (grid.isConnected) {
      grid.replaceChildren(el('div', { class: 'page-loading' }, [spinner('lg')]));
    }
  }, 160);

  const [projects, err] = await tryCall('listProjects');
  clearTimeout(spinnerTimer);
  if (!grid.isConnected) return;
  if (err) {
    grid.replaceChildren(
      emptyState({ iconName: 'error', title: 'Не удалось загрузить проекты', text: err.message })
    );
    return;
  }

  store.set('projects', projects ?? []);

  if (!projects || projects.length === 0) {
    grid.replaceChildren(
      emptyState({
        iconName: 'folder',
        title: 'Создайте первый проект',
        text: 'Проект хранит документы, языки перевода и глоссарий.',
        action: button({
          label: 'Новый проект',
          variant: 'primary',
          iconName: 'plus',
          onClick: () => openCreateProjectModal(),
        }),
      })
    );
    return;
  }

  grid.replaceChildren(...projects.map((project) => projectCard(project, grid)));
}

/** Карточка проекта. */
function projectCard(project, grid) {
  /** Кнопка действия карточки: клик не должен открывать проект. */
  const cardAction = (iconName, title, onClick) =>
    button({
      iconName,
      variant: 'ghost',
      size: 'sm',
      title,
      onClick: (e) => {
        e.stopPropagation();
        onClick();
      },
    });

  const actionsBox = el('div', { class: 'card-actions' }, [
    cardAction('folder', 'Открыть', () => openProject(project)),
    cardAction('edit', 'Переименовать', () => openRenameModal(project, grid)),
    cardAction('trash', 'Удалить', () => deleteProjectFlow(project, grid)),
  ]);

  const meta = el('div', { class: 'project-card__langs row row--wrap' }, [
    chip({ label: langLabel(project.sourceLang) }),
    el('span', { class: 'project-card__arrow', text: '→' }),
    ...project.targetLangs.map((code) => chip({ label: langLabel(code) })),
  ]);

  const tasks = el('div', { class: 'project-card__tasks row row--wrap' },
    Object.entries(project.taskSummary || {}).map(([status, count]) =>
      badge({ label: `${TASK_LABELS[status] ?? status}: ${count}`, tone: TASK_TONES[status] ?? 'muted' })
    )
  );

  const card = el('article', {
    class: 'project-card panel',
    dataset: { id: project.id },
    onClick: () => openProject(project),
  }, [
    actionsBox,
    el('h3', { class: 'project-card__title', text: project.title }),
    meta,
    el('div', {
      class: 'project-card__docs text-secondary',
      text: `${project.documentCount ?? 0} ${plural(project.documentCount ?? 0)}`,
    }),
    tasks,
    project.updatedAt
      ? el('div', { class: 'project-card__date text-tertiary', text: formatDate(project.updatedAt) })
      : null,
  ]);

  return card;
}

/** Русская плюрализация для «документ». */
function plural(n) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'документ';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'документа';
  return 'документов';
}

/** Открыть проект и перейти к документам. */
async function openProject(project) {
  try {
    const data = await call('openProject', project.id);
    store.set('activeProject', data);
    router.showPage('documents');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* -------------------------------------------------------------------------
 * Действия
 * ------------------------------------------------------------------------- */

/** Кнопки для шапки. */
function actions(host) {
  return [
    button({
      label: 'Новый проект',
      variant: 'primary',
      iconName: 'plus',
      onClick: () => openCreateProjectModal(),
    }),
  ];
}

/** Переименование проекта. */
function openRenameModal(project, grid) {
  let input;

  const dialog = modal({
    title: 'Переименовать проект',
    render: (body) => {
      input = el('input', {
        class: 'input',
        type: 'text',
        value: project.title,
        placeholder: 'Название проекта',
      });
      body.append(
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Название' }),
          input,
        ])
      );
    },
    actions: [
      { label: 'Отмена' },
      {
        label: 'Сохранить',
        variant: 'primary',
        onClick: async () => {
          const title = input.value.trim();
          if (!title) {
            toast('Название проекта не может быть пустым.', 'error');
            return;
          }
          try {
            await call('renameProject', project.id, title);
            dialog.close();
            toast('Проект переименован', 'success');
            void refresh(grid.closest('.page') ?? grid);
          } catch (e) {
            toast(e.message, 'error');
          }
        },
      },
    ],
  });

  input?.focus();
  input?.select();
}

/** Удаление проекта с подтверждением. */
async function deleteProjectFlow(project, grid) {
  const confirmed = await confirmDialog({
    title: `Удалить проект «${project.title}»?`,
    text: 'Проект и все его переводы будут удалены.',
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!confirmed) return;
  try {
    await call('deleteProject', project.id);
    toast('Проект удалён', 'success');
    if (store.get('activeProject')?.id === project.id) store.set('activeProject', null);
    void refresh(grid.closest('.page') ?? grid);
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* -------------------------------------------------------------------------
 * Модалка создания проекта (переиспользуется страницей «Документы»)
 * ------------------------------------------------------------------------- */

/**
 * Открывает модалку создания проекта. Экспортируется для pages/documents.js.
 */
export function openCreateProjectModal() {
  /** @type {Array<object>} модели с installState installed/available */
  let models = [];
  /** @type {string} выбранная модель */
  let modelId = '';
  /** @type {Set<string>} выбранные целевые языки */
  const targets = new Set();
  /** @type {string} исходный язык ('auto' или код) */
  let sourceLang = 'auto';
  /** @type {string} поисковый фильтр целевых языков */
  let targetFilter = '';

  let modelList;
  let sourceSelect;
  let targetGrid;
  let counter;
  let errorText;
  let createBtn;
  let titleInput;

  const dialog = modal({
    title: 'Новый проект',
    dismissable: true,
    render: (body) => {
      modelList = el('div', { class: 'model-picker' });
      sourceSelect = el('select', { class: 'select' });
      targetGrid = el('div', { class: 'lang-grid' });
      counter = el('span', { class: 'text-secondary', text: '0 выбрано' });
      errorText = el('div', { class: 'field-error' });

      const targetSearch = el('input', {
        class: 'input',
        type: 'search',
        placeholder: 'Поиск языка…',
        onInput: (e) => {
          targetFilter = e.target.value.trim().toLowerCase();
          renderTargets();
        },
      });

      titleInput = el('input', { class: 'input', type: 'text', placeholder: 'Необязательно' });

      body.append(
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Название' }),
          titleInput,
        ]),
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Модель' }),
          modelList,
          el('p', { class: 'field__hint', text: 'Список языков зависит от модели.' }),
        ]),
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Исходный язык' }),
          sourceSelect,
        ]),
        el('div', { class: 'field' }, [
          el('div', { class: 'row row--between' }, [
            el('label', { class: 'field__label', text: 'Целевые языки' }),
            counter,
          ]),
          targetSearch,
          targetGrid,
        ]),
        errorText
      );

      sourceSelect.addEventListener('change', () => {
        sourceLang = sourceSelect.value;
        targets.delete(sourceLang);
        renderTargets();
        updateCounter();
      });
    },
    actions: [
      { label: 'Отмена' },
      {
        label: 'Создать',
        variant: 'primary',
        onClick: () => submit(),
      },
    ],
  });

  createBtn = dialog.root.querySelector('.modal__footer .btn--primary');

  /** Обновляет счётчик выбранных целевых языков. */
  function updateCounter() {
    if (counter) counter.textContent = `${targets.size} выбрано`;
  }

  /** Показать/скрыть inline-ошибку формы. */
  function showError(text) {
    if (errorText) {
      errorText.textContent = text ?? '';
      errorText.classList.toggle('is-visible', Boolean(text));
    }
  }

  /** Текущий объект модели. */
  function currentModel() {
    return models.find((m) => m.id === modelId) ?? null;
  }

  /** Перерисовывает список карточек моделей. */
  function renderModels() {
    modelList.replaceChildren(
      ...models.map((model) => {
        const selected = model.id === modelId;
        const card = el('button', {
          class: `model-option${selected ? ' is-selected' : ''}`,
          type: 'button',
          dataset: { id: model.id },
          onClick: () => {
            modelId = model.id;
            targets.clear();
            renderModels();
            renderSourceSelect();
            renderTargets();
            updateCounter();
          },
        }, [
          el('div', { class: 'model-option__main' }, [
            el('span', { class: 'model-option__name', text: model.name }),
            el('span', {
              class: 'model-option__meta text-tertiary',
              text: [model.sizeLabel, model.quantization].filter(Boolean).join(' · '),
            }),
          ]),
          badge({
            label: model.installState === 'installed' ? 'Установлена' : 'Доступна',
            tone: model.installState === 'installed' ? 'success' : 'muted',
          }),
        ]);
        return card;
      })
    );
  }

  /** Перерисовывает селект исходного языка. */
  function renderSourceSelect() {
    const model = currentModel();
    const codes = model ? model.languageCodes : [];
    sourceSelect.replaceChildren(
      el('option', { value: 'auto', text: 'Авто' }),
      ...codes.map((code) => el('option', { value: code, text: langLabel(code) }))
    );
    const keep = codes.includes(sourceLang) ? sourceLang : 'auto';
    sourceLang = keep;
    sourceSelect.value = keep;
  }

  /** Перерисовывает сетку целевых языков с фильтром. */
  function renderTargets() {
    const model = currentModel();
    const codes = model ? model.languageCodes.filter((code) => code !== sourceLang) : [];
    const needle = targetFilter;
    const filtered = needle
      ? codes.filter((code) => langLabel(code).toLowerCase().includes(needle) || code.includes(needle))
      : codes;

    targetGrid.replaceChildren(
      ...filtered.map((code) => {
        const selected = targets.has(code);
        return el('button', {
          class: `lang-chip${selected ? ' is-selected' : ''}`,
          type: 'button',
          dataset: { code },
          onClick: () => {
            if (targets.has(code)) targets.delete(code);
            else targets.add(code);
            renderTargets();
            updateCounter();
          },
        }, [
          el('span', {
            class: `lang-chip__dot${selected ? ' is-on' : ''}`,
          }),
          el('span', { class: 'lang-chip__label', text: langLabel(code) }),
          el('span', { class: 'lang-chip__code text-tertiary', text: code }),
        ]);
      }),
      filtered.length === 0
        ? el('div', { class: 'text-tertiary', text: 'Ничего не найдено.' })
        : null
    );
  }

  /** Валидация и создание проекта. */
  async function submit() {
    showError('');
    const title = titleInput ? titleInput.value.trim() : '';

    if (!modelId) {
      showError('Выберите модель.');
      return;
    }
    if (targets.size === 0) {
      showError('Выберите хотя бы один целевой язык.');
      return;
    }

    if (createBtn) createBtn.disabled = true;
    try {
      const data = await call('createProject', {
        title,
        modelId,
        sourceLang,
        targetLangs: [...targets],
      });
      dialog.close();
      store.set('activeProject', data);
      router.showPage('documents');
      toast('Проект создан', 'success');
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      if (createBtn) createBtn.disabled = false;
    }
  }

  /** Первичная загрузка списка моделей. */
  async function loadModels() {
    modelList.replaceChildren(
      el('div', { class: 'row', style: { padding: 'var(--sp-2) 0' } }, [spinner()])
    );
    const [data, err] = await tryCall('listModels');
    if (!modelList.isConnected) return;
    if (err) {
      modelList.replaceChildren(
        el('p', { class: 'field__hint field__hint--error', text: err.message })
      );
      return;
    }
    models = (data?.models ?? []).filter(
      (model) => model.installState === 'installed' || model.installState === 'available'
    );
    const recommendation = store.get('recommendation')?.id;
    modelId =
      (recommendation && models.some((m) => m.id === recommendation) && recommendation) ||
      models.find((m) => m.installState === 'installed')?.id ||
      models[0]?.id ||
      '';
    renderModels();
    renderSourceSelect();
    renderTargets();
    updateCounter();
    if (models.length === 0) {
      modelList.append(
        el('p', { class: 'field__hint field__hint--error', text: 'Нет доступных моделей.' })
      );
    }
  }

  void loadModels();
}

/* -------------------------------------------------------------------------
 * Регистрация
 * ------------------------------------------------------------------------- */

router.registerPage('projects', {
  title: 'Проекты',
  subtitle: 'Локальные проекты переводов',
  render,
  actions,
});
