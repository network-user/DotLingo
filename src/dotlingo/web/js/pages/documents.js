/**
 * Документы открытого проекта: импорт, прогресс, запуск перевода, экспорт.
 */

import { call } from '../bridge.js';
import { badge, confirmDialog, modal, progressBar, spinner } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';
import {
  button,
  el,
  emptyState,
  langDisplay,
  requireProject,
  run,
  selectBox,
  toast,
} from './common.js';

const EXPORT_EXT = ['.txt', '.md', '.docx', '.epub'];

let generation = 0;
let session = null;
let unlisten = () => {};

router.registerPage('documents', {
  title: 'Документы',
  subtitle: 'Копия оригинала, перевод по языкам и отдельный экспорт',
  layout: 'wide',
  actions: () => [
    button({ label: 'Импорт', iconName: 'download', onClick: () => doImport() }),
    button({
      label: 'Перевести выбранные',
      iconName: 'play',
      variant: 'primary',
      onClick: () => doTranslate(),
    }),
  ],
  render(host) {
    generation += 1;
    session = { selected: new Set(), docs: [] };
    const ticket = generation;
    unlisten();
    const off = store.on('documents_changed', () => {
      if (ticket === generation) paint(host, ticket);
    });
    const offTask = store.on('task_event', (payload) => {
      const status = payload?.task?.status;
      if (ticket === generation && (status === 'complete' || status === 'failed')) paint(host, ticket);
    });
    unlisten = () => {
      off();
      offTask();
    };
    paint(host, ticket);
  },
  destroy() {
    generation += 1;
    unlisten();
    unlisten = () => {};
    session = null;
  },
});

async function paint(host, ticket) {
  host.replaceChildren(spinner());
  const project = store.get('activeProject');
  if (!project) {
    host.replaceChildren();
    requireProject(host, 'Импорт и перевод идут внутри открытого проекта.');
    return;
  }
  const docs = await run(() => call('listDocuments'));
  if (ticket !== generation || !host.isConnected) return;
  if (docs === undefined) {
    host.replaceChildren(emptyState({ iconName: 'error', title: 'Не удалось прочитать документы' }));
    return;
  }
  session.docs = docs;
  if (!docs.length) {
    host.replaceChildren(
      emptyState({
        iconName: 'document',
        title: 'В проекте нет документов',
        text: 'TXT, Markdown, DOCX, EPUB и PDF с текстовым слоем. Скан и запись PDF не поддерживаются.',
        action: button({ label: 'Импорт', variant: 'primary', iconName: 'download', onClick: () => doImport() }),
      }),
    );
    return;
  }

  const body = el('tbody');
  for (const doc of docs) {
    const checked = session.selected.has(doc.id);
    const box = el('input', {
      type: 'checkbox',
      checked,
      onChange: () => {
        if (box.checked) session.selected.add(doc.id);
        else session.selected.delete(doc.id);
      },
    });
    body.append(
      el('tr', {}, [
        el('td', {}, [box]),
        el('td', {}, [
          el('div', { class: 'truncate', text: doc.name }),
          doc.warnings?.length
            ? el('div', { class: 'tiny', text: doc.warnings[0] })
            : null,
        ]),
        el('td', {}, [badge({ label: doc.format || 'файл', tone: 'muted', dot: false })]),
        el('td', {}, [progressCell(doc)]),
        el('td', {}, [
          el('div', { class: 'row-actions' }, [
            button({
              label: 'Проверка',
              iconName: 'search',
              size: 'sm',
              onClick: () => openReview(doc.id),
            }),
            button({
              label: 'Экспорт',
              iconName: 'export',
              size: 'sm',
              onClick: () => doExport(doc),
            }),
          ]),
        ]),
      ]),
    );
  }

  host.replaceChildren(
    el('div', { class: 'table-wrap' }, [
      el('table', { class: 'table' }, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: '' }),
            el('th', { text: 'Документ' }),
            el('th', { text: 'Формат' }),
            el('th', { text: 'Перевод' }),
            el('th', { text: '' }),
          ]),
        ]),
        body,
      ]),
    ]),
  );
}

function progressCell(doc) {
  const wrap = el('div', { class: 'stack' });
  const entries = Object.entries(doc.progressByTarget || {});
  if (!entries.length) {
    wrap.append(el('span', { class: 'tiny', text: 'Нет целевого языка' }));
    return wrap;
  }
  for (const [lang, progress] of entries) {
    const total = progress.total || 0;
    const done = progress.done || 0;
    const bar = progressBar(total ? done / total : 0);
    wrap.append(
      el('div', { class: 'progress-line' }, [
        el('div', { class: 'progress-line__label', text: `${langDisplay(lang)} · ${done}/${total}` }),
        bar.root,
      ]),
    );
  }
  return wrap;
}

function openReview(docId) {
  store.set('reviewDocId', docId);
  router.showPage('review');
}

async function doImport() {
  if (!store.get('activeProject')) {
    toast('Сначала откройте проект', 'warning');
    router.showPage('projects');
    return;
  }
  const paths = await run(() => call('resolveImportPaths'));
  if (!paths?.length) return;
  const started = await run(() => call('importDocuments', paths));
  if (started === undefined) return;
  toast('Копирую документы в проект', 'info');
}

async function doTranslate() {
  const project = store.get('activeProject');
  if (!project) {
    toast('Сначала откройте проект', 'warning');
    return;
  }
  const ids = [...(session?.selected || [])];
  if (!ids.length) {
    toast('Отметьте один или несколько документов', 'warning');
    return;
  }
  if (!project.modelId) {
    toast('Сначала выберите модель в настройках проекта', 'warning');
    router.showPage('projects');
    return;
  }
  const yes = await confirmDialog({
    title: 'Поставить перевод в очередь?',
    text: `Документов: ${ids.length}. Языки: ${(project.targetLangs || []).map(langDisplay).join(', ')}. Срок не оценивается. Уже переведённые фрагменты останутся, если задача продолжится.`,
    confirmLabel: 'В очередь',
  });
  if (!yes) return;
  const result = await run(() => call('enqueueTranslation', {
    documentIds: ids,
    targetLangs: project.targetLangs || [],
  }));
  if (result === undefined) return;
  const count = result.taskIds?.length || 0;
  toast(count ? `В очереди задач: ${count}` : 'Задачи не созданы', count ? 'success' : 'warning');
  for (const warning of result.warnings || []) {
    toast(`${warning.document}: ${warning.text}`, 'warning', 8000);
  }
  if (count) router.showPage('queue');
}

async function doExport(doc) {
  const project = store.get('activeProject');
  const targets = project?.targetLangs || [];
  if (!targets.length) {
    toast('У проекта нет целевого языка', 'warning');
    return;
  }
  const lang = targets.length === 1 ? targets[0] : await pickLanguage(targets);
  if (!lang) return;
  const destination = await run(() => call('resolveExportPath', defaultName(doc, lang), EXPORT_EXT));
  if (!destination) return;
  const saved = await run(() => call('exportDocument', doc.id, lang, destination));
  if (saved === undefined) return;
  toast(`Файл записан: ${saved.path || destination}`, 'success', 6000);
}

function pickLanguage(targets) {
  return new Promise((resolve) => {
    const select = selectBox(
      targets.map((code) => ({ value: code, label: langDisplay(code) })),
      targets[0],
    );
    let chosen = null;
    const dialog = modal({
      title: 'Язык экспорта',
      subtitle: 'Каждый язык пишется в свой файл',
      render: (body) => body.append(select),
      actions: [
        { label: 'Отмена', onClick: () => dialog.close() },
        {
          label: 'Дальше',
          variant: 'primary',
          onClick: () => {
            chosen = select.value;
            dialog.close();
          },
        },
      ],
      onClose: () => resolve(chosen),
    });
  });
}

function defaultName(doc, lang) {
  const name = doc.name || 'export.txt';
  const dot = name.lastIndexOf('.');
  const base = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot).toLowerCase() : '.txt';
  const usable = ['.txt', '.md', '.markdown', '.docx', '.epub'].includes(ext) ? ext : '.md';
  const suffix = usable === '.markdown' ? '.md' : usable;
  return `${base}.${lang}${suffix}`;
}
