/**
 * Вкладка «История»: задачи и готовые файлы всех проектов.
 * Список читает те же данные, что очередь, и сам ничего не собирает на диске.
 */

import { tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import {
  el,
  button,
  badge,
  toast,
  emptyState,
  modal,
  spinner,
} from '../components.js';

const STATUS_LABELS = {
  queued: 'В очереди',
  running: 'Перевод',
  paused: 'Пауза',
  complete: 'Готово',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  interrupted: 'Прервано',
  saved: 'Файл',
};

const STATUS_TONES = {
  queued: 'muted',
  running: 'muted',
  paused: 'warning',
  complete: 'success',
  failed: 'error',
  cancelled: 'muted',
  interrupted: 'warning',
  saved: 'success',
};

const RESUME_STATUSES = ['paused', 'interrupted', 'failed', 'cancelled'];

/** @type {Set<object>} */
const mounts = new Set();
let wired = false;

function ensureWire() {
  if (wired) return;
  wired = true;
  store.on('task_event', (payload) => {
    if (payload?.exportError) toast(payload.exportError, 'error', 7000);
    else if (payload?.exportPath && payload?.task?.status === 'complete' && !payload.exportReused) {
      toast('Файл результата собран', 'success');
    }
    refreshAll();
  });
  store.on('exports_ready', (payload) => {
    const count = payload?.paths?.length || 0;
    if (count === 1) toast('Файл результата собран', 'success');
    else if (count > 1) toast(`Собрано файлов: ${count}`, 'success');
    else if (payload?.errors?.length) {
      toast(payload.errors[0].error || 'Файл не собрался', 'error', 7000);
    }
    refreshAll();
  });
  store.on('export_done', () => refreshAll());
}

function refreshAll() {
  for (const mount of mounts) void load(mount);
}

function langLabel(code) {
  if (!code) return '';
  if (code === 'auto') return 'Авто';
  const languages = store.get('languages') || {};
  return languages[code] || String(code).toUpperCase();
}

function formatWhen(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function dayOf(value) {
  return String(value || '').slice(0, 10);
}

function scopeId(mount, key) {
  const source = mount.options[key];
  if (typeof source === 'function') return String(source() || '');
  return String(source || '');
}

/**
 * Кнопки готового файла: проводник, программа по умолчанию, просмотр в окне.
 * @param {{path?: string, format?: string}} file
 * @returns {HTMLButtonElement[]}
 */
export function resultFileActions(file) {
  const path = file?.path;
  if (!path) return [];
  return [
    button({
      label: 'В папке',
      size: 'sm',
      title: 'Показать файл в проводнике',
      onClick: () => void openAction('revealPath', path),
    }),
    button({
      label: 'Открыть',
      size: 'sm',
      variant: 'primary',
      title: 'Открыть программой по умолчанию',
      onClick: () => void openAction('openPath', path),
    }),
    button({
      label: 'Смотреть',
      size: 'sm',
      title: 'Просмотр в окне DotLingo',
      onClick: () => void previewResult(path),
    }),
  ];
}

async function openAction(method, path) {
  const [, error] = await tryCall(method, path);
  if (error) toast(error.message, 'error');
}

/** Собрать файл для завершённой задачи или текущий текст документа. */
export async function assembleResult(row) {
  const taskId = row?.taskId && !String(row.taskId).startsWith('export:') ? row.taskId : '';
  const [data, error] = await tryCall(
    'publishTranslation',
    row.documentId,
    row.targetLang,
    row.projectId || '',
    taskId,
  );
  if (error || !data?.path) {
    toast(error?.message || 'Файл не собрался.', 'error');
    return null;
  }
  if (!data.reused) toast('Файл собран', 'success');
  return data;
}

export async function previewResult(path) {
  const [data, error] = await tryCall('previewExport', path);
  if (error || !data) {
    toast(error?.message || 'Файл не открылся.', 'error');
    return;
  }
  if (data.kind === 'text') {
    modal({
      title: data.name || 'Результат',
      panelClass: 'modal--preview',
      render: (body) => {
        body.append(el('pre', { class: 'result-preview', text: data.text || '' }));
      },
    });
    return;
  }
  if (data.kind === 'pdf' && data.base64) {
    const binary = atob(data.base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
    modal({
      title: data.name || 'Результат',
      panelClass: 'modal--preview',
      onClose: () => URL.revokeObjectURL(url),
      render: (body) => {
        body.append(el('iframe', {
          class: 'result-preview__frame',
          src: url,
          title: data.name || 'PDF',
        }));
      },
    });
    return;
  }
  const note = data.kind === 'too_large'
    ? 'Файл большой для окна. Его можно открыть отдельно.'
    : 'В окне читаются текст и PDF. Этот файл открывается своей программой.';
  const dialog = modal({
    title: data.name || 'Результат',
    subtitle: note,
    actions: [
      {
        label: 'Открыть',
        variant: 'primary',
        onClick: () => {
          void openAction('openPath', path);
          dialog.close();
        },
      },
      {
        label: 'В папке',
        onClick: () => {
          void openAction('revealPath', path);
          dialog.close();
        },
      },
    ],
  });
}

function blankFilters() {
  return { from: '', to: '', status: '', language: '', query: '' };
}

function filtersActive(filters) {
  return Boolean(filters.from || filters.to || filters.status || filters.language || filters.query);
}

function field(label, control) {
  return el('label', { class: 'results__field' }, [
    el('span', { class: 'results__label', text: label }),
    control,
  ]);
}

function toolbar(mount) {
  const from = el('input', {
    class: 'input',
    type: 'date',
    ariaLabel: 'Дата с',
    onChange: (event) => {
      mount.draft.from = event.target.value;
    },
  });
  const to = el('input', {
    class: 'input',
    type: 'date',
    ariaLabel: 'Дата по',
    onChange: (event) => {
      mount.draft.to = event.target.value;
    },
  });
  const status = el('select', {
    class: 'select',
    ariaLabel: 'Статус',
    onChange: (event) => {
      mount.draft.status = event.target.value;
    },
  }, [
    el('option', { value: '', text: 'Все статусы' }),
    ...Object.entries(STATUS_LABELS).map(([value, label]) => el('option', { value, text: label })),
  ]);
  const language = el('select', {
    class: 'select',
    ariaLabel: 'Язык',
    onChange: (event) => {
      mount.draft.language = event.target.value;
    },
  }, [el('option', { value: '', text: 'Все языки' })]);
  const query = el('input', {
    class: 'input',
    type: 'search',
    placeholder: 'Имя документа',
    ariaLabel: 'Имя документа',
    onInput: (event) => {
      mount.draft.query = event.target.value.trim().toLowerCase();
    },
  });
  mount.languageSelect = language;
  mount.draftFields = { from, to, status, language, query };
  return el('div', { class: 'results__toolbar' }, [
    field('С', from),
    field('По', to),
    field('Статус', status),
    field('Язык', language),
    field('Документ', query),
    el('div', { class: 'results__apply' }, [
      button({
        label: 'Показать',
        variant: 'primary',
        onClick: () => applyFilters(mount),
      }),
      button({
        label: 'Сбросить',
        onClick: () => resetFilters(mount),
      }),
    ]),
  ]);
}

function applyFilters(mount) {
  mount.filters = { ...mount.draft };
  paint(mount);
}

function resetFilters(mount) {
  mount.draft = blankFilters();
  mount.filters = blankFilters();
  const fields = mount.draftFields;
  if (fields) {
    fields.from.value = '';
    fields.to.value = '';
    fields.status.value = '';
    fields.language.value = '';
    fields.query.value = '';
  }
  paint(mount);
}

function fillLanguages(mount) {
  const select = mount.languageSelect;
  if (!select) return;
  const codes = [...new Set(mount.rows.map((row) => row.targetLang).filter(Boolean))].sort();
  const current = mount.draft.language;
  const listed = current && !codes.includes(current) ? [current, ...codes] : codes;
  select.replaceChildren(
    el('option', { value: '', text: 'Все языки' }),
    ...listed.map((code) => el('option', { value: code, text: langLabel(code) })),
  );
  select.value = current;
}

function matches(mount, row) {
  const projectId = scopeId(mount, 'projectId');
  const documentId = scopeId(mount, 'documentId');
  if (projectId && row.projectId !== projectId) return false;
  if (documentId && row.documentId !== documentId) return false;
  const filters = mount.filters;
  if (filters.status && row.status !== filters.status) return false;
  if (filters.language && row.targetLang !== filters.language) return false;
  if (filters.query && !(row.documentName || '').toLowerCase().includes(filters.query)) return false;
  const day = dayOf(row.updatedAt || row.createdAt);
  if ((filters.from || filters.to) && !day) return false;
  if (filters.from && day < filters.from) return false;
  if (filters.to && day > filters.to) return false;
  return true;
}

function resultCard(row, showProject) {
  const pair = [langLabel(row.sourceLang), langLabel(row.targetLang)].filter(Boolean).join(' → ');
  const when = formatWhen(row.updatedAt || row.createdAt);
  const progress = row.total > 0 ? `${row.completed}/${row.total}` : '';
  const meta = [showProject ? row.projectTitle : '', pair, when, progress].filter(Boolean).join(' · ');
  const files = Array.isArray(row.files) ? row.files : [];
  const canResume = row.kind !== 'file' && RESUME_STATUSES.includes(row.status);
  const needsFile = row.status === 'complete' && files.length === 0;
  return el('article', { class: 'result-card panel panel--flat' }, [
    el('div', { class: 'result-card__head' }, [
      el('p', { class: 'result-card__name', text: row.documentName || 'Документ' }),
      badge({
        label: STATUS_LABELS[row.status] || row.status || 'Задача',
        tone: STATUS_TONES[row.status] || 'muted',
      }),
    ]),
    meta ? el('p', { class: 'result-card__meta', text: meta }) : null,
    row.error ? el('p', { class: 'result-card__error', text: row.error }) : null,
    ...files.map((file) => el('div', { class: 'result-card__file' }, [
      el('p', { class: 'result-card__path', text: file.path || '' }),
      file.note ? el('p', { class: 'result-card__meta', text: file.note }) : null,
      el('div', { class: 'result-card__actions' }, resultFileActions(file)),
    ])),
    needsFile
      ? el('div', { class: 'result-card__actions' }, [
        button({
          label: 'Собрать файл',
          size: 'sm',
          variant: 'primary',
          onClick: () => void assembleResult(row),
        }),
      ])
      : null,
    canResume
      ? el('div', { class: 'result-card__actions' }, [
        button({
          label: 'Продолжить',
          size: 'sm',
          variant: 'primary',
          onClick: () => void resumeRow(row),
        }),
      ])
      : null,
  ]);
}

async function resumeRow(row) {
  const [, error] = await tryCall('resumeTask', row.taskId);
  if (error) toast(error.message, 'error');
}

function paint(mount) {
  const visible = mount.rows
    .filter((row) => matches(mount, row))
    .sort((a, b) => String(b.updatedAt || b.createdAt || '').localeCompare(
      String(a.updatedAt || a.createdAt || ''),
    ));
  if (!mount.list) return;
  const showProject = !scopeId(mount, 'projectId');
  if (visible.length === 0) {
    const narrowed = mount.rows.length > 0 && filtersActive(mount.filters);
    mount.list.replaceChildren(emptyState({
      iconName: 'clock',
      title: narrowed ? 'Ничего не подошло' : 'Результатов пока нет',
      text: narrowed
        ? 'Смените условия и снова нажмите «Показать».'
        : 'Готовый файл появится здесь, когда перевод завершится. Пауза, обрыв, ошибка и отмена продолжаются из этого списка.',
    }));
    return;
  }
  mount.list.replaceChildren(...visible.map((row) => resultCard(row, showProject)));
}

async function load(mount) {
  if (!mount.host.isConnected) {
    mounts.delete(mount);
    return;
  }
  const [rows, error] = await tryCall('listTasks');
  if (!mount.host.isConnected) {
    mounts.delete(mount);
    return;
  }
  if (error) {
    mount.list?.replaceChildren(emptyState({
      iconName: 'error',
      title: 'Не удалось загрузить результаты',
      text: error.message,
    }));
    return;
  }
  mount.rows = Array.isArray(rows) ? rows : [];
  fillLanguages(mount);
  paint(mount);
}

/**
 * @param {HTMLElement} host
 * @param {{projectId?: string|Function, documentId?: string|Function}} [options]
 */
export function mountResults(host, options = {}) {
  ensureWire();
  const mount = {
    host,
    options,
    rows: [],
    draft: blankFilters(),
    filters: blankFilters(),
    languageSelect: null,
    draftFields: null,
    list: null,
  };
  const list = el('div', { class: 'results__list stack' }, [
    el('div', { class: 'page-loading' }, [spinner()]),
  ]);
  const tools = toolbar(mount);
  mount.list = list;
  host.replaceChildren(el('section', { class: 'results results--page stack' }, [
    tools,
    el('p', {
      class: 'results__note',
      text: 'По дате, сначала новые. Фильтр включается кнопкой «Показать».',
    }),
    list,
  ]));
  mounts.add(mount);
  void load(mount);
  return {
    refresh() {
      void load(mount);
    },
    destroy() {
      mounts.delete(mount);
    },
  };
}

/** @type {{refresh: Function, destroy: Function}|null} */
let pageMount = null;

function renderPage(host) {
  pageMount?.destroy();
  pageMount = mountResults(host);
}

function destroyPage() {
  pageMount?.destroy();
  pageMount = null;
}

function pageActions() {
  return [
    button({
      label: 'Обновить',
      variant: 'ghost',
      iconName: 'refresh',
      onClick: () => pageMount?.refresh(),
    }),
  ];
}

router.registerPage('history', {
  title: 'История',
  subtitle: 'Сначала новые',
  help: 'Все переводы и готовые файлы, новые сверху. Дата, статус, язык и имя документа применяются кнопкой «Показать». «Продолжить» возвращает паузу, обрыв, ошибку и отмену. Файл можно показать в папке, открыть или просмотреть в окне.',
  render: renderPage,
  destroy: destroyPage,
  actions: pageActions,
});
