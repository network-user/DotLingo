/**
 * Страница «Проверка»: дерево блоков документа + редактор перевода
 * с несохранёнными правками, автосохранением и экспортом.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { spectrum } from '../docmap.js';
import {
  el,
  button,
  toast,
  emptyState,
  confirmDialog,
  spinner,
  debounce,
  helpMark,
  closeHelpMarks,
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
/** @type {Object<string, Object<string, boolean>>} */
let editedFlags = {};
/** @type {Object<string, Object<string, string>>} */
let machineDrafts = {};
/** @type {{id?: number, source: string, target: string}[]} */
let glossaryTerms = [];
/** @type {{ terms: Array<object>, entries: Array<object>, sourceRe: RegExp|null, targetRe: RegExp|null }|null} */
let glossaryCache = null;
/** @type {'all'|'empty'|'machine'|'edited'} */
let statusFilter = 'all';
let reviewLang = '';
/** @type {string|null} порядок текущего блока (как строка-ключ) */
let currentOrder = null;
let searchFilter = '';
let dirty = false;
let unsubExternal = null;
/** @type {HTMLElement|null} корень страницы текущего рендера */
let root = null;
/** @type {{root: HTMLElement, setCurrent: Function, update: Function}|null} карта перевода */
let docMap = null;

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
  editedFlags = doc?.editedFlags ?? {};
  machineDrafts = doc?.machineDrafts ?? {};
  docName = doc?.name ?? 'Документ';
  docFormat = doc?.format ?? '';

  const langs = project.targetLangs ?? [];
  reviewLang = langs.includes(store.get('reviewTargetLang')) ? store.get('reviewTargetLang') : langs[0] ?? '';
  await loadGlossary(reviewLang);
  if (!host.isConnected) return;

  buildLayout(host);
}

/** Сброс состояния страницы. */
function resetState() {
  docId = null;
  docName = '';
  docFormat = '';
  blocks = [];
  translations = {};
  editedFlags = {};
  machineDrafts = {};
  glossaryTerms = [];
  glossaryCache = null;
  statusFilter = 'all';
  reviewLang = '';
  currentOrder = null;
  searchFilter = '';
  dirty = false;
  unsubExternal = null;
  docMap = null;
}

/** Пустое состояние «нет проекта». */
function noProjectState() {
  return emptyState({
    iconName: 'folder',
    title: 'Сначала откройте проект',
    text: 'Правка идёт по переведённым фрагментам открытого проекта.',
    action: button({ label: 'К переводу', variant: 'ghost', onClick: () => router.showPage('chat') }),
  });
}

/** Пустое состояние «нет документов». */
function noDocumentsState() {
  return emptyState({
    iconName: 'document',
    title: 'Добавьте файлы',
    text: 'Сначала положите документы в проект и запустите перевод. Здесь правят уже готовые фрагменты.',
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
  closeHelpMarks();
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
    el('div', { class: 'review-nav__search' }, [
      el('div', { class: 'review-nav__tools' }, [
        el('div', { class: 'review-filters' }),
        helpMark('«Пустые» ещё без перевода. «Черновик» - ответ модели, его ещё не правили. «Правка» - текст, который вы уже меняли. На карте справа одна полоска - один фрагмент. Чем она ярче, тем перевод уже есть.'),
      ]),
      button({
        label: 'Следующий пустой',
        size: 'sm',
        title: 'Alt+стрелка вниз',
        onClick: () => void goNextEmpty(),
      }),
      searchInput,
    ]),
    tree,
  ]);

  docMap = spectrum({
    blocks,
    translations,
    lang: reviewLang,
    orientation: 'v',
    onNavigate: (block) => selectBlock(String(block.order)),
  });
  const mapAside = el('aside', { class: 'review-map panel' }, [
    el('div', { class: 'review-map__label', text: 'Карта' }),
    docMap.root,
  ]);

  const editor = el('section', { class: 'review-editor' }, [header, body]);

  host.append(el('div', { class: 'review-layout' }, [nav, mapAside, editor]));
  host.addEventListener('keydown', (event) => {
    if (event.altKey && event.key === 'ArrowDown') {
      event.preventDefault();
      void goNextEmpty();
    }
  });

  paintFilters();
  renderTree();
  const first = visibleBlocks()[0];
  selectBlock(first ? String(first.order) : null);
}

/* -------------------------------------------------------------------------
 * Дерево блоков
 * ------------------------------------------------------------------------- */

const STATUS_FILTERS = [
  ['all', 'Все'],
  ['empty', 'Пустые'],
  ['machine', 'Черновик'],
  ['edited', 'Правка'],
];

/** all | empty | machine | edited | skip */
function blockState(block) {
  if (!block?.translatable) return 'skip';
  const text = (translations[reviewLang]?.[String(block.order)] || '').trim();
  if (!text) return 'empty';
  if (editedFlags[reviewLang]?.[String(block.order)]) return 'edited';
  return 'machine';
}

function paintFilters() {
  const box = root?.querySelector('.review-filters');
  if (!box) return;
  box.replaceChildren(
    ...STATUS_FILTERS.map(([value, label]) => button({
      label,
      size: 'sm',
      variant: statusFilter === value ? 'primary' : 'ghost',
      onClick: () => {
        statusFilter = value;
        paintFilters();
        renderTree();
      },
    }))
  );
}

async function loadGlossary(lang) {
  if (!lang) {
    glossaryTerms = [];
    return;
  }
  const [terms, err] = await tryCall('listGlossary', lang);
  glossaryTerms = err || !Array.isArray(terms) ? [] : terms;
  glossaryCache = null;
}

function escapeReg(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function termPattern(value) {
  return new RegExp(`(?<![\\p{L}\\p{N}])${escapeReg(value)}(?![\\p{L}\\p{N}])`, 'iu');
}

/** Скомпилировать шаблоны один раз на текущий список терминов. */
function glossaryPatterns() {
  if (glossaryCache?.terms === glossaryTerms) return glossaryCache;
  const entries = [];
  const sourceKeys = [];
  const targetKeys = [];
  for (const term of glossaryTerms) {
    const source = String(term?.source || '').trim();
    const target = String(term?.target || '').trim();
    if (!source || !target) continue;
    entries.push({ term, source: termPattern(source), target: termPattern(target) });
    sourceKeys.push(source);
    targetKeys.push(target);
  }
  sourceKeys.sort((a, b) => b.length - a.length);
  targetKeys.sort((a, b) => b.length - a.length);
  glossaryCache = {
    terms: glossaryTerms,
    entries,
    sourceRe: sourceKeys.length ? new RegExp(sourceKeys.map(escapeReg).join('|'), 'giu') : null,
    targetRe: targetKeys.length ? new RegExp(targetKeys.map(escapeReg).join('|'), 'giu') : null,
  };
  return glossaryCache;
}

/** Подсветка терминов глоссария. Возвращает фрагмент, без innerHTML. */
function highlightTerms(text, field) {
  const frag = document.createDocumentFragment();
  const pattern = field === 'source' ? glossaryPatterns().sourceRe : glossaryPatterns().targetRe;
  if (!text || !pattern) {
    frag.append(text || '');
    return frag;
  }
  pattern.lastIndex = 0;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) frag.append(text.slice(last, index));
    frag.append(el('mark', { class: 'review-term', text: match[0] }));
    last = index + match[0].length;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}

function missingGlossary(original, translation) {
  return glossaryPatterns().entries.filter((entry) => (
    entry.source.test(original) && !entry.target.test(translation)
  )).map((entry) => entry.term);
}

/** Блоки с учётом поиска и фильтра состояния. */
function visibleBlocks() {
  return blocks.filter((block) => {
    if (statusFilter !== 'all' && blockState(block) !== statusFilter) return false;
    if (!searchFilter) return true;
    const original = (block.text || '').toLowerCase();
    const translated = (translations[reviewLang]?.[String(block.order)] || '').toLowerCase();
    return original.includes(searchFilter) || translated.includes(searchFilter);
  });
}

async function goNextEmpty() {
  const translatable = blocks.filter((block) => block.translatable);
  const start = translatable.findIndex((block) => String(block.order) === currentOrder);
  const from = start < 0 ? 0 : start + 1;
  const ordered = translatable.slice(from).concat(translatable.slice(0, from));
  const next = ordered.find((block) => blockState(block) === 'empty');
  if (!next) {
    toast('Пустых блоков не осталось', 'info');
    return;
  }
  if (statusFilter === 'machine' || statusFilter === 'edited') statusFilter = 'all';
  const original = (next.text || '').toLowerCase();
  const translated = (translations[reviewLang]?.[String(next.order)] || '').toLowerCase();
  if (searchFilter && !original.includes(searchFilter) && !translated.includes(searchFilter)) {
    searchFilter = '';
    const search = root?.querySelector('.review-search');
    if (search) search.value = '';
  }
  paintFilters();
  renderTree();
  await selectBlock(String(next.order));
  root?.querySelector(`.review-tree__row[data-order="${CSS.escape(String(next.order))}"]`)
    ?.scrollIntoView({ block: 'nearest' });
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
  const state = blockState(block);
  const preview = (block.text || '').replace(/\s+/g, ' ').slice(0, 60);

  return el('button', {
    class: `review-tree__row${currentOrder === key ? ' is-active' : ''}`,
    type: 'button',
    dataset: { order: key },
    onClick: () => selectBlock(key),
  }, [
    el('span', { class: `review-tree__dot is-${state}` }),
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

  markActiveRow();
  docMap?.setCurrent(order);

  renderEditor();
}

/** Подсветить выбранную строку, не пересобирая дерево. */
function markActiveRow() {
  const tree = root?.querySelector('.review-tree');
  if (!tree) return;
  tree.querySelectorAll('.review-tree__row.is-active').forEach((row) => {
    row.classList.remove('is-active');
  });
  if (currentOrder == null) return;
  const next = tree.querySelector(
    `.review-tree__row[data-order="${CSS.escape(String(currentOrder))}"]`,
  );
  if (next) next.classList.add('is-active');
}

/** Обновить точку состояния одной строки после сохранения. */
function paintRowState(order) {
  const row = root?.querySelector(
    `.review-tree__row[data-order="${CSS.escape(String(order))}"]`,
  );
  const block = blockByKey(String(order));
  const dot = row?.querySelector('.review-tree__dot');
  if (!dot || !block) return false;
  dot.className = `review-tree__dot is-${blockState(block)}`;
  return true;
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
      await loadGlossary(reviewLang);
      docMap?.update({ lang: reviewLang });
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
    paintMissing(textarea);
    const btn = root?.querySelector('.review-save-btn');
    if (btn) btn.disabled = false;
  });
  textarea.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      void saveEdit(true);
    }
  });

  const kindLabel = [block.kind, block.sectionTitle, stateCaption(block)].filter(Boolean).join(' · ');
  const original = el('div', { class: 'review-original' });
  original.append(highlightTerms(block.text || '', 'source'));
  const draft = machineDrafts[reviewLang]?.[String(block.order)] || '';
  const draftBox = draft && draft !== translation
    ? el('details', { class: 'review-draft', open: true }, [
        el('summary', { text: 'Черновик модели' }),
        (() => {
          const node = el('div', { class: 'review-draft__text' });
          node.append(highlightTerms(draft, 'target'));
          return node;
        })(),
      ])
    : null;

  body.replaceChildren(
    el('div', { class: 'review-block-title' }, [
      el('span', { class: 'review-block-title__kind', text: kindLabel || 'Блок' }),
      el('span', { class: 'review-block-title__name ellipsis', text: docName }),
    ]),
    el('div', { class: 'review-columns' }, [
      el('div', { class: 'review-col' }, [
        el('div', { class: 'review-col__label', text: 'Оригинал' }),
        original,
      ]),
      el('div', { class: 'review-col' }, [
        el('div', { class: 'review-col__label', text: 'Перевод' }),
        textarea,
        el('p', { class: 'review-missing' }),
        draftBox,
        buildUnsavedBar(textarea),
      ]),
    ])
  );
  paintMissing(textarea);

  updateStatusBar();
  updateUnsavedBar(textarea);
}

/** Статус «Переведено блоков: X/Y». */
function updateStatusBar() {
  const status = root?.querySelector('.review-status');
  if (!status) return;
  const translatable = blocks.filter((block) => block.translatable);
  const counts = { empty: 0, machine: 0, edited: 0 };
  for (const block of translatable) {
    const state = blockState(block);
    if (state in counts) counts[state] += 1;
  }
  const done = counts.machine + counts.edited;
  status.textContent = `Переведено ${done}/${translatable.length} · пустые ${counts.empty} · черновик ${counts.machine} · правка ${counts.edited}`;
}

function refreshBlockChrome() {
  const block = currentOrder != null ? blockByKey(currentOrder) : null;
  const kind = root?.querySelector('.review-block-title__kind');
  const textarea = root?.querySelector('.review-translation');
  if (!block || !kind) return;
  const kindLabel = [block.kind, block.sectionTitle, stateCaption(block)].filter(Boolean).join(' · ');
  kind.textContent = kindLabel || 'Блок';
  if (textarea) paintMissing(textarea);
}

function stateCaption(block) {
  const state = blockState(block);
  if (state === 'edited') return 'правка';
  if (state === 'machine') return 'черновик';
  if (state === 'empty') return 'пусто';
  return '';
}

function paintMissing(textarea) {
  const note = textarea.closest('.review-col')?.querySelector('.review-missing');
  const block = currentOrder != null ? blockByKey(currentOrder) : null;
  if (!note || !block) return;
  const missing = missingGlossary(block.text || '', textarea.value || '');
  note.textContent = missing.length
    ? `В переводе нет: ${missing.map((term) => term.target).join(', ')}`
    : '';
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
    if (!editedFlags[reviewLang]) editedFlags[reviewLang] = {};
    editedFlags[reviewLang][String(block.order)] = true;
    dirty = false;
    updateUnsavedBar(textarea);
    const btn = root?.querySelector('.review-save-btn');
    if (btn) btn.disabled = true;
    const stillVisible = visibleBlocks().some((item) => String(item.order) === String(block.order));
    if (stillVisible && paintRowState(block.order)) {
      docMap?.touch(block.order, Boolean((textarea.value || '').trim()));
    } else {
      docMap?.update({ translations });
      renderTree();
    }
    updateStatusBar();
    refreshBlockChrome();
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
    if (!editedFlags[reviewLang]) editedFlags[reviewLang] = {};
    editedFlags[reviewLang][String(block.order)] = true;
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
  subtitle: 'Правка перевода и выгрузка',
  help: 'Слева фрагменты документа, справа оригинал и перевод. Фильтры отделяют пустые места, черновик модели и вашу правку. Карта справа открывает фрагмент, «Следующий пустой» прыгает к пропуску.',
  render,
  destroy,
  actions,
});
