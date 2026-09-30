/**
 * Страница «Документы»: список документов активного проекта, импорт,
 * мультивыбор, постановка задач на перевод.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { barcode } from '../docmap.js';
import {
  el,
  button,
  badge,
  toast,
  emptyState,
  modal,
  spinner,
} from '../components.js';
import { openCreateProjectModal } from './projects.js';

/** @type {Set<string>} выбранные id документов */
let selected = new Set();
/** @type {(() => void)|null} отписка от documents_imported */
let unsubImported = null;
/** @type {HTMLElement|null} футер с действиями над выбранным */
let footer = null;

/* -------------------------------------------------------------------------
 * Рендер
 * ------------------------------------------------------------------------- */

function render(host) {
  selected = new Set();
  const project = store.get('activeProject');

  if (!project) {
    host.append(noProjectState());
    return;
  }

  host.append(
    el('div', { class: 'doc-list stack' }, [el('div', { class: 'page-loading' }, [spinner('lg')])])
  );
  void refresh(host);
}

/** Пустое состояние «нет проекта». */
function noProjectState() {
  const actionRow = el('div', { class: 'row row--wrap', style: { justifyContent: 'center' } }, [
    button({ label: 'К проектам', variant: 'ghost', onClick: () => router.showPage('projects') }),
    button({
      label: 'Новый проект',
      variant: 'primary',
      iconName: 'plus',
      onClick: () => openCreateProjectModal(),
    }),
  ]);
  return emptyState({
    iconName: 'folder',
    title: 'Сначала выберите проект',
    text: 'Документы, переводы и экспорты хранятся внутри проекта.',
    action: actionRow,
  });
}

/** Перезагружает список документов и перерисовывает. */
async function refresh(host) {
  const list = host.querySelector('.doc-list');
  if (!list) return;

  const [documentsData, err] = await tryCall('listDocuments');
  if (!list.isConnected) return;
  if (err) {
    list.replaceChildren(
      emptyState({ iconName: 'error', title: 'Не удалось загрузить документы', text: err.message })
    );
    renderFooter(host);
    return;
  }

  const documents = documentsData ?? [];
  selected = new Set([...selected].filter((id) => documents.some((doc) => doc.id === id)));

  if (documents.length === 0) {
    list.replaceChildren(
      emptyState({
        iconName: 'document',
        title: 'Добавьте документы',
        text: 'TXT, Markdown, DOCX, EPUB, PDF (с текстовым слоем).',
        action: button({
          label: 'Выбрать файлы',
          variant: 'primary',
          iconName: 'upload',
          onClick: () => importFiles(host),
        }),
      })
    );
    renderFooter(host);
    return;
  }

  list.replaceChildren(...documents.map((doc) => documentRow(doc, host)));
  renderFooter(host);
}

/** Строка документа. */
function documentRow(doc, host) {
  const isSelected = selected.has(doc.id);
  rowWarnings.set(doc.id, doc.warnings ?? []);

  const checkbox = el('input', {
    class: 'doc-check',
    type: 'checkbox',
    checked: isSelected,
    onClick: (e) => e.stopPropagation(),
    onChange: (e) => {
      if (e.target.checked) selected.add(doc.id);
      else selected.delete(doc.id);
      row.classList.toggle('is-selected', e.target.checked);
      renderFooter(host);
    },
  });

  const nameBox = el('div', { class: 'doc-row__name' }, [
    checkbox,
    el('span', { class: 'doc-row__title ellipsis', text: doc.name }),
    badge({ label: (doc.format || '?').toUpperCase(), tone: 'muted', dot: false }),
    ...(doc.warnings || []).map((warning) =>
      el('span', { class: 'doc-row__warning', title: warning }, [badge({ label: '!', tone: 'warning' })])
    ),
    doc.detectedLanguage
      ? el('span', { class: 'text-tertiary doc-row__lang', text: langLabel(doc.detectedLanguage) })
      : null,
    doc.exportCount > 0
      ? el('span', { class: 'text-tertiary', text: `экспортов: ${doc.exportCount}` })
      : null,
  ]);

  const progressBox = el('div', { class: 'doc-row__progress' },
    Object.entries(doc.progressByTarget || {}).map(([lang, progress]) => {
      const strip = barcode((doc.spectrumByTarget || {})[lang] || []);
      return el('div', { class: 'doc-progress' }, [
        el('span', { class: 'doc-progress__lang text-secondary', text: lang }),
        strip,
        el('span', {
          class: 'doc-progress__nums text-tertiary',
          text: `${progress.done}/${progress.total}`,
        }),
      ]);
    })
  );

  const row = el('article', {
    class: `doc-row panel${isSelected ? ' is-selected' : ''}`,
    dataset: { id: doc.id, exports: String(doc.exportCount ?? 0) },
    onClick: (e) => {
      if (e.ctrlKey || e.metaKey) {
        toggleSelect(doc.id, host);
      } else {
        selectOnly(doc.id, host);
      }
    },
    onDblclick: () => {
      store.set('selectedDocId', doc.id);
      router.showPage('review');
    },
  }, [nameBox, progressBox]);

  return row;
}

/** Метка языка (дублирует projects.js, чтобы не плодить связность). */
function langLabel(code) {
  const languages = store.get('languages') || {};
  return languages[code] || code.toUpperCase();
}

/** Переключить выбор документа. */
function toggleSelect(id, host) {
  if (selected.has(id)) selected.delete(id);
  else selected.add(id);
  syncRowStates(host);
  renderFooter(host);
}

/** Выбрать только один документ. */
function selectOnly(id, host) {
  selected = new Set([id]);
  syncRowStates(host);
  renderFooter(host);
}

/** Синхронизирует классы строк с set выбранных. */
function syncRowStates(host) {
  host.querySelectorAll('.doc-row').forEach((row) => {
    const id = row.dataset.id;
    row.classList.toggle('is-selected', selected.has(id));
    const check = row.querySelector('.doc-check');
    if (check) check.checked = selected.has(id);
  });
}

/* -------------------------------------------------------------------------
 * Footer с действиями над выбранными
 * ------------------------------------------------------------------------- */

/** Перерисовывает футер-панель по текущему выбору. */
function renderFooter(host) {
  if (!footer) {
    footer = el('footer', { class: 'doc-footer panel panel--raised' });
    host.append(footer);
  }

  if (selected.size === 0) {
    footer.hidden = true;
    footer.replaceChildren();
    return;
  }

  const project = store.get('activeProject');
  const ids = [...selected];
  const single = ids.length === 1;

  footer.hidden = false;
  footer.replaceChildren(
    el('span', { class: 'doc-footer__count', text: `Выбрано: ${ids.length}` }),
    el('div', { class: 'doc-footer__actions' }, [
      button({
        label: 'Перевести',
        variant: 'primary',
        iconName: 'play',
        onClick: () => openTranslateModal(ids, host),
      }),
      single
        ? button({
            label: 'Проверить',
            variant: 'ghost',
            iconName: 'search',
            onClick: () => {
              store.set('selectedDocId', ids[0]);
              router.showPage('review');
            },
          })
        : null,
      single && exportCountOf(host, ids[0]) > 0
        ? button({
            label: 'Открыть результат',
            variant: 'ghost',
            iconName: 'export',
            onClick: () => revealLatestExport(ids[0]),
          })
        : null,
    ])
  );
}

/** exportCount документа по данным из DOM-кэша страницы. */
function exportCountOf(host, id) {
  const row = host.querySelector(`.doc-row[data-id="${CSS.escape(id)}"]`);
  if (!row) return 0;
  return Number(row.dataset.exports ?? 0);
}

/** Открыть последний экспорт документа в проводнике. */
async function revealLatestExport(docId) {
  try {
    const doc = await call('getDocument', docId);
    const exports = doc?.exports ?? [];
    const last = exports[exports.length - 1];
    if (!last?.path) {
      toast('У документа нет экспортов.', 'warning');
      return;
    }
    await call('revealPath', last.path);
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* -------------------------------------------------------------------------
 * Импорт файлов
 * ------------------------------------------------------------------------- */

/** Диалог выбора файлов и запуск импорта. */
async function importFiles(host) {
  const [paths] = await tryCall('resolveImportPaths');
  if (!paths || paths.length === 0) return;
  try {
    await call('importDocuments', paths);
    toast('Импорт запущен', 'info');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/** Обработка push-события documents_imported. */
function onImported(payload, host) {
  if (!payload) return;
  const imported = payload.imported ?? [];
  const errors = payload.errors ?? [];
  for (const item of imported) {
    toast(`Импортирован: ${item.name}`, 'success');
  }
  for (const item of errors) {
    toast(`${item.name}: ${item.error}`, 'error', 6000);
  }
  void refresh(host);
}

/* -------------------------------------------------------------------------
 * Модалка перевода
 * ------------------------------------------------------------------------- */

/** Сводная модалка перед постановкой задач перевода. */
function openTranslateModal(documentIds, host) {
  const project = store.get('activeProject');
  const targets = project?.targetLangs ?? [];
  const documents = currentDocuments(host).filter((doc) => documentIds.includes(doc.id));
  const warnings = [...new Set(documents.flatMap((doc) => doc.warnings ?? []))];

  const summary = el('ul', { class: 'summary-list' }, [
    el('li', {}, [
      el('span', { class: 'text-secondary', text: 'Документы: ' }),
      el('span', { text: String(documents.length) }),
    ]),
    el('li', {}, [
      el('span', { class: 'text-secondary', text: 'Направление: ' }),
      el('span', {
        text: `${langLabel(project?.sourceLang || 'auto')} → ${targets.map(langLabel).join(', ')}`,
      }),
    ]),
    el('li', {}, [
      el('span', { class: 'text-secondary', text: 'Задач будет создано: ' }),
      el('span', { text: String(documents.length * targets.length) }),
    ]),
    el('li', { class: 'text-tertiary', text: 'ETA не рассчитывается.' }),
    ...warnings.map((warning) =>
      el('li', { class: 'summary-list__warning' }, [
        badge({ label: 'Предупреждение', tone: 'warning' }),
        el('span', { text: warning }),
      ])
    ),
  ]);

  const dialog = modal({
    title: 'Поставить перевод',
    subtitle: 'Проверьте сводку перед запуском.',
    body: [summary],
    actions: [
      { label: 'Отмена' },
      {
        label: 'Перевести',
        variant: 'primary',
        onClick: async () => {
          try {
            await call('enqueueTranslation', {
              documentIds,
              targetLangs: targets,
            });
            dialog.close();
            toast('Задачи поставлены в очередь', 'success');
            router.showPage('queue');
          } catch (e) {
            toast(e.message, 'error', 6000);
          }
        },
      },
    ],
  });
}

/** Кэш документов страницы: берём из строк (имя и warnings не нужны здесь). */
function currentDocuments(host) {
  const rows = [...host.querySelectorAll('.doc-row')];
  return rows.map((row) => {
    const id = row.dataset.id;
    const title = row.querySelector('.doc-row__title')?.textContent ?? '';
    return { id, name: title, warnings: rowWarnings.get(id) ?? [] };
  });
}

/** Кэш warnings по id (заполняется при рендере строк). */
const rowWarnings = new Map();

/* -------------------------------------------------------------------------
 * Действия шапки
 * ------------------------------------------------------------------------- */

function actions(host) {
  return [
    button({
      label: 'Добавить файлы',
      variant: 'primary',
      iconName: 'upload',
      onClick: () => importFiles(host),
    }),
  ];
}

/* -------------------------------------------------------------------------
 * Жизненный цикл
 * ------------------------------------------------------------------------- */

function destroy() {
  unsubImported?.();
  unsubImported = null;
  footer = null;
  selected = new Set();
  rowWarnings.clear();
}

/** Подписка на завершение импорта - навешивается при первом render. */
function wireImportEvents(host) {
  unsubImported?.();
  unsubImported = store.on('documents_imported', (payload) => onImported(payload, host));
}

router.registerPage('documents', {
  title: 'Документы',
  subtitle: 'Документы активного проекта',
  render: (host) => {
    render(host);
    wireImportEvents(host);
  },
  destroy,
  actions,
});


/* Экспорт для тестов/отладки не требуется. */
