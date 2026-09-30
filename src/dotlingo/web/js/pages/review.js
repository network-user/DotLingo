/**
 * Страница «Проверка»: дерево блоков документа + редактор перевода
 * с несохранёнными правками, автосохранением и экспортом.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import {
  el,
  button,
  toast,
  emptyState,
  confirmDialog,
  spinner,
  debounce,
} from '../components.js';

/* -------------------------------------------------------------------------
 * Состояние страницы (сбрасывается при каждом render)
 * ------------------------------------------------------------------------- */

let docId = null;
let docName = '';
let docFormat = '';
let blocks = [];
/** @type {Object<string, Object<string, string>>} translations[lang][order] = text */
let translations = {};
let reviewLang = '';
/** @type {string|null} порядок текущего блока (как строка-ключ) */
let currentOrder = null;
let searchFilter = '';
let dirty = false;
let unsubExternal = null;
/** @type {HTMLElement|null} корень страницы текущего рендера */
let root = null;

/* -------------------------------------------------------------------------
 * Рендер
 * ------------------------------------------------------------------------- */

async function render(host) {
  resetState();
  root = host;

  const project = store.get('activeProject');
  if (!project) {
    host.append(noProjectState());
    return;
  }

  host.append(el('div', { class: 'page-loading' }, [spinner('lg')]));

  // Документ: выбранный ранее или первый в проекте.
  let targetId = store.get('selectedDocId');
  if (!targetId) {
    const [documents, listErr] = await tryCall('listDocuments');
    if (!host.isConnected) return;
    if (listErr) {
      host.replaceChildren(
        emptyState({ iconName: 'error', title: 'Не удалось загрузить документы', text: listErr.message })
      );
      return;
    }
    const first = (documents ?? [])[0];
    targetId = first?.id ?? null;
    if (targetId) store.set('selectedDocId', targetId);
  }

  if (!targetId) {
    host.replaceChildren(noDocumentsState());
    return;
  }

  docId = targetId;
  const [doc, err] = await tryCall('getDocument', docId);
  if (!host.isConnected) return;
  if (err) {
    host.replaceChildren(
      emptyState({ iconName: 'error', title: 'Не удалось открыть документ', text: err.message })
    );
    return;
  }

  blocks = doc?.blocks ?? [];
  translations = doc?.translations ?? {};
  docName = doc?.name ?? 'Документ';
  docFormat = doc?.format ?? '';

  const langs = project.targetLangs ?? [];
  reviewLang = langs.includes(store.get('reviewTargetLang')) ? store.get('reviewTargetLang') : langs[0] ?? '';

  buildLayout(host);
}

/** Сброс состояния страницы. */
function resetState() {
  docId = null;
  docName = '';
  docFormat = '';
  blocks = [];
  translations = {};
  reviewLang = '';
  currentOrder = null;
  searchFilter = '';
  dirty = false;
  unsubExternal = null;
}

/** Пустое состояние «нет проекта». */
function noProjectState() {
  return emptyState({
    iconName: 'folder',
    title: 'Сначала выберите проект',
    text: 'Проверка перевода работает внутри проекта.',
    action: button({ label: 'К проектам', variant: 'ghost', onClick: () => router.showPage('projects') }),
  });
}

/** Пустое состояние «нет документов». */
function noDocumentsState() {
  return emptyState({
    iconName: 'document',
    title: 'Добавьте файлы',
    text: 'Проверять можно только переведённые документы проекта.',
    action: button({
      label: 'К документам',
      variant: 'ghost',
      onClick: () => router.showPage('documents'),
    }),
  });
}

/** Метка языка. */
function langLabel(code) {
  const languages = store.get('languages') || {};
  return languages[code] || code.toUpperCase();
}

/* -------------------------------------------------------------------------
 * Каркас: левая панель + правый редактор
 * ------------------------------------------------------------------------- */

function buildLayout(host) {
  host.replaceChildren();

  const searchInput = el('input', {
    class: 'input review-search',
    type: 'search',
    placeholder: 'Поиск по тексту…',
    onInput: debounce((e) => {
      const next = e.target.value.trim().toLowerCase();
      // Поиск при несохранённой правке: автосохраняем молча перед сменой фильтра.
      if (next !== searchFilter && dirty) void autoSave();
      searchFilter = next;
      renderTree();
    }, 180),
  });

  const tree = el('div', { class: 'review-tree' });
  const header = el('div', { class: 'review-editor__header' });
  const body = el('div', { class: 'review-editor__body' });

  const nav = el('aside', { class: 'review-nav panel' }, [
    el('div', { class: 'review-nav__search' }, [searchInput]),
    tree,
  ]);

  const editor = el('section', { class: 'review-editor' }, [header, body]);

  host.append(el('div', { class: 'review-layout' }, [nav, editor]));

  renderTree();
  const first = visibleBlocks()[0];
  selectBlock(first ? String(first.order) : null);
}

/* -------------------------------------------------------------------------
 * Дерево блоков
 * ------------------------------------------------------------------------- */

/** Блоки с учётом поискового фильтра. */
function visibleBlocks() {
  if (!searchFilter) return blocks;
  const needle = searchFilter;
  return blocks.filter((block) => {
    const original = (block.text || '').toLowerCase();
    const translated = (translations[reviewLang]?.[String(block.order)] || '').toLowerCase();
    return original.includes(needle) || translated.includes(needle);
  });
}

/** Группировка видимых блоков по секциям. */
function renderTree() {
  const tree = root?.querySelector('.review-tree');
  if (!tree) return;

  const visible = visibleBlocks();
  const groups = new Map();
  for (const block of visible) {
    const key = block.sectionTitle || `Секция ${block.section}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(block);
  }

  if (visible.length === 0) {
    tree.replaceChildren(
      el('p', { class: 'text-tertiary', text: 'Ничего не найдено.' })
    );
    return;
  }

  const nodes = [];
  for (const [section, sectionBlocks] of groups) {
    nodes.push(el('div', { class: 'review-tree__section-title', text: section }));
    for (const block of sectionBlocks) {
      nodes.push(blockRow(block));
    }
  }
  tree.replaceChildren(...nodes);
}

/** Строка блока в дереве. */
function blockRow(block) {
  const key = String(block.order);
  const translated = Boolean((translations[reviewLang]?.[key] || '').trim());
  const preview = (block.text || '').replace(/\s+/g, ' ').slice(0, 60);

  return el('button', {
    class: `review-tree__row${currentOrder === key ? ' is-active' : ''}`,
    type: 'button',
    dataset: { order: key },
    onClick: () => selectBlock(key),
  }, [
    el('span', { class: `review-tree__dot${translated ? ' is-translated' : ''}` }),
    el('span', { class: 'review-tree__preview ellipsis', text: preview || '(пусто)' }),
  ]);
}

/* -------------------------------------------------------------------------
 * Редактор блока
 * ------------------------------------------------------------------------- */

/** Выбрать блок (с автосохранением предыдущего при dirty). */
async function selectBlock(order) {
  if (order === currentOrder && order != null) return;
  if (dirty) await autoSave();
  currentOrder = order;
  dirty = false;

  root?.querySelectorAll('.review-tree__row').forEach((row) => {
    row.classList.toggle('is-active', row.dataset.order === order);
  });

  renderEditor();
}

/** Найти блок по строковому ключу порядка. */
function blockByKey(order) {
  return blocks.find((block) => String(block.order) === order) ?? null;
}

/** Полный рендер правой части. */
function renderEditor() {
  const header = root?.querySelector('.review-editor__header');
  const body = root?.querySelector('.review-editor__body');
  if (!header || !body) return;

  const project = store.get('activeProject');
  const langs = project?.targetLangs ?? [];
  const block = currentOrder != null ? blockByKey(currentOrder) : null;

  if (!block) {
    header.replaceChildren();
    body.replaceChildren(
      emptyState({
        iconName: 'search',
        title: 'Выберите блок',
        text: 'Слева - список блоков документа.',
      })
    );
    updateStatusBar();
    return;
  }

  // Шапка редактора: селектор языка, статус, кнопки.
  const langSelect = el('select', {
    class: 'select review-lang-select',
    onChange: async (e) => {
      if (dirty) await autoSave();
      reviewLang = e.target.value;
      store.set('reviewTargetLang', reviewLang);
      dirty = false;
      renderTree();
      renderEditor();
    },
  },
    langs.map((code) =>
      el('option', { value: code, text: langLabel(code), selected: code === reviewLang })
    )
  );

  const saveBtn = button({
    label: 'Сохранить',
    variant: 'ghost',
    iconName: 'check',
    disabled: !dirty,
    onClick: () => void saveEdit(true),
  });
  saveBtn.classList.add('review-save-btn');
  const exportBtn = button({
    label: 'Экспорт',
    variant: 'primary',
    iconName: 'export',
    onClick: () => void exportFlow(),
  });

  header.replaceChildren(
    el('div', { class: 'row row--wrap' }, [
      el('div', { class: 'field' }, [
        el('label', { class: 'field__label', text: 'Язык перевода' }),
        langSelect,
      ]),
      el('div', { class: 'review-status', text: '' }),
    ]),
    el('div', { class: 'row' }, [saveBtn, exportBtn])
  );

  // Тело: оригинал + перевод.
  const translation = translations[reviewLang]?.[String(block.order)] ?? '';
  const textarea = el('textarea', {
    class: 'textarea review-translation',
    spellcheck: false,
    placeholder: block.translatable ? 'Перевод появится после перевода или введите вручную…' : '',
  });
  textarea.value = translation;
  textarea.disabled = !block.translatable;
  textarea.addEventListener('input', () => {
    dirty = true;
    updateUnsavedBar(textarea);
    const btn = root?.querySelector('.review-save-btn');
    if (btn) btn.disabled = false;
  });
  textarea.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      void saveEdit(true);
    }
  });

  const kindLabel = [block.kind, block.sectionTitle].filter(Boolean).join(' · ');

  body.replaceChildren(
    el('div', { class: 'review-block-title' }, [
      el('span', { class: 'review-block-title__kind', text: kindLabel || 'Блок' }),
      el('span', { class: 'review-block-title__name ellipsis', text: docName }),
    ]),
    el('div', { class: 'review-columns' }, [
      el('div', { class: 'review-col' }, [
        el('div', { class: 'review-col__label', text: 'Оригинал' }),
        el('div', { class: 'review-original', text: block.text || '' }),
      ]),
      el('div', { class: 'review-col' }, [
        el('div', { class: 'review-col__label', text: 'Перевод' }),
        textarea,
        buildUnsavedBar(textarea),
      ]),
    ])
  );

  updateStatusBar();
  updateUnsavedBar(textarea);
}

/** Статус «Переведено блоков: X/Y». */
function updateStatusBar() {
  const status = root?.querySelector('.review-status');
  if (!status) return;
  const translatable = blocks.filter((block) => block.translatable);
  const done = translatable.filter(
    (block) => (translations[reviewLang]?.[String(block.order)] || '').trim()
  ).length;
  status.textContent = `Переведено блоков: ${done}/${translatable.length}`;
}

/** Unsaved-бар (создаётся заново при каждом рендере редактора). */
function buildUnsavedBar(textarea) {
  const bar = el('div', { class: 'review-unsaved', hidden: true }, [
    el('span', { class: 'review-unsaved__text', text: 'Есть несохранённые изменения' }),
    el('div', { class: 'row' }, [
      button({
        label: 'Сохранить',
        variant: 'primary',
        size: 'sm',
        onClick: () => void saveEdit(true),
      }),
      button({
        label: 'Отменить правку',
        variant: 'ghost',
        size: 'sm',
        onClick: () => {
          const block = currentOrder != null ? blockByKey(currentOrder) : null;
          if (!block) return;
          textarea.value = translations[reviewLang]?.[String(block.order)] ?? '';
          dirty = false;
          updateUnsavedBar(textarea);
          const btn = root?.querySelector('.review-save-btn');
          if (btn) btn.disabled = true;
        },
      }),
    ]),
  ]);
  return bar;
}

/** Показать/скрыть unsaved-бар текущего блока. */
function updateUnsavedBar(textarea) {
  const bar = textarea.closest('.review-col')?.querySelector('.review-unsaved');
  if (bar) bar.hidden = !dirty;
}

/* -------------------------------------------------------------------------
 * Сохранение
 * ------------------------------------------------------------------------- */

/** Сохранение с UI-фидбеком (кнопка/тост). */
async function saveEdit(withToast) {
  const block = currentOrder != null ? blockByKey(currentOrder) : null;
  const textarea = root?.querySelector('.review-translation');
  if (!block || !textarea || !reviewLang) return;

  try {
    await call('saveEdit', docId, block.order, textarea.value, reviewLang);
    if (!translations[reviewLang]) translations[reviewLang] = {};
    translations[reviewLang][String(block.order)] = textarea.value;
    dirty = false;
    updateUnsavedBar(textarea);
    const btn = root?.querySelector('.review-save-btn');
    if (btn) btn.disabled = true;
    renderTree();
    updateStatusBar();
    if (withToast) toast('Правка сохранена', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/** Молчаливое автосохранение (смена блока/языка/поиска, destroy). */
async function autoSave() {
  const block = currentOrder != null ? blockByKey(currentOrder) : null;
  const textarea = root?.querySelector('.review-translation');
  if (!block || !textarea || !reviewLang) return;
  try {
    await call('saveEdit', docId, block.order, textarea.value, reviewLang);
    if (!translations[reviewLang]) translations[reviewLang] = {};
    translations[reviewLang][String(block.order)] = textarea.value;
    dirty = false;
    updateUnsavedBar(textarea);
  } catch {
    // Тихо: пользователь не просил сохранять.
  }
}

/* -------------------------------------------------------------------------
 * Экспорт
 * ------------------------------------------------------------------------- */

/** Полный флоу экспорта текущего документа. */
async function exportFlow() {
  if (!docId || !reviewLang) return;
  if (dirty) await autoSave();

  const [extensions, extErr] = await tryCall('getExportExtensions', docId);
  if (extErr) {
    toast(extErr.message, 'error');
    return;
  }

  const stem = docName.replace(/\.[^.]+$/, '');
  const ext = (extensions ?? ['.txt'])[0] ?? '.txt';
  const defaultName = `${stem}.translated-${reviewLang}${ext}`;

  const [path] = await tryCall('resolveExportPath', defaultName, extensions ?? ['.txt']);
  if (!path) return;

  // Предупреждение о непереведённых блоках.
  const translatable = blocks.filter((block) => block.translatable);
  const untranslated = translatable.filter(
    (block) => !(translations[reviewLang]?.[String(block.order)] || '').trim()
  );
  if (untranslated.length > 0) {
    const confirmed = await confirmDialog({
      title: 'Часть блоков не переведена',
      text: 'Экспортёр оставит их на исходном языке. Продолжить?',
      confirmLabel: 'Экспортировать',
    });
    if (!confirmed) return;
  }

  try {
    await call('exportDocument', docId, reviewLang, path);
    toast(`Файл сохранён: ${path}`, 'success', 7000);
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* -------------------------------------------------------------------------
 * Действия шапки
 * ------------------------------------------------------------------------- */

function actions(host) {
  return [
    button({
      label: 'К документам',
      variant: 'ghost',
      onClick: () => router.showPage('documents'),
    }),
  ];
}

/* -------------------------------------------------------------------------
 * Жизненный цикл
 * ------------------------------------------------------------------------- */

function destroy() {
  // Автосохранение без UI: страница уходит, диалог невозможен.
  if (dirty) void autoSave();
  unsubExternal?.();
  unsubExternal = null;
  resetState();
  root = null;
}

router.registerPage('review', {
  title: 'Проверка',
  subtitle: 'Правка переводов и экспорт',
  render,
  destroy,
  actions,
});
