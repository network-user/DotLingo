/**
 * Страница «Глоссарий»: термины исходный → перевод по выбранному целевому
 * языку активного проекта. Инлайн-редактирование, добавление, удаление.
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
} from '../components.js';

/** Ключ store с выбранным целевым языком глоссария. */
const LANG_KEY = 'glossaryTargetLang';

/* -------------------------------------------------------------------------
 * Хелперы
 * ------------------------------------------------------------------------- */

/** Метка языка из store.languages, иначе код в верхнем регистре. */
function langLabel(code) {
  const languages = store.get('languages') || {};
  if (code === 'auto') return 'Авто';
  return languages[code] || code.toUpperCase();
}

/** Текущий целевой язык: сохранённый (если валиден) или первый из проекта. */
function currentLang(project) {
  const saved = store.get(LANG_KEY);
  if (saved && project.targetLangs.includes(saved)) return saved;
  return project.targetLangs[0] ?? '';
}

/* -------------------------------------------------------------------------
 * Рендер
 * ------------------------------------------------------------------------- */

function render(host) {
  const project = store.get('activeProject');

  if (!project) {
    host.append(noProjectState());
    return;
  }

  const lang = currentLang(project);

  const langSelect = el('select', {
    class: 'select gl-lang-select',
    onChange: (e) => {
      store.set(LANG_KEY, e.target.value);
      refresh(host);
    },
  });
  fillLangOptions(langSelect, project.targetLangs, lang);

  const head = el('div', { class: 'panel__header' }, [
    el('div', {}, [
      el('h2', {
        class: 'panel__title',
        text: `${langLabel(project.sourceLang || 'auto')} → ${langLabel(lang)}`,
      }),
    ]),
    el('div', { class: 'panel__actions' }, [langSelect]),
  ]);

  const body = el('div', { class: 'gl-body' }, [
    el('div', { class: 'page-loading' }, [spinner('lg')]),
  ]);

  const panel = el('section', { class: 'panel gl-panel' }, [head, body]);

  host.append(el('div', { class: 'gl-page stack' }, [panel]));
  void refresh(host);
}

/** Пустое состояние «нет проекта». */
function noProjectState() {
  return emptyState({
    iconName: 'book',
    title: 'Сначала выберите проект',
    text: 'Глоссарий хранится внутри проекта.',
    action: button({
      label: 'К проектам',
      variant: 'ghost',
      onClick: () => router.showPage('projects'),
    }),
  });
}

/** Заполняет селект целевых языков. */
function fillLangOptions(select, codes, value) {
  select.replaceChildren(
    ...codes.map((code) => el('option', { value: code, text: langLabel(code) }))
  );
  select.value = value;
}

/** Загружает список терминов и перерисовывает таблицу. */
async function refresh(host) {
  const project = store.get('activeProject');
  const body = host.querySelector('.gl-body');
  if (!project || !body) return;
  const lang = currentLang(project);

  const [terms, err] = await tryCall('listGlossary', lang);
  if (!body.isConnected) return;
  if (err) {
    body.replaceChildren(
      emptyState({ iconName: 'error', title: 'Не удалось загрузить глоссарий', text: err.message })
    );
    return;
  }

  body.replaceChildren(
    buildTable(terms ?? [], lang, host),
    (terms ?? []).length === 0
      ? emptyState({
          iconName: 'book',
          title: 'В глоссарии пока нет терминов',
          text: 'Добавьте пары термин → перевод, чтобы защищать их от искажения.',
        })
      : null,
    buildFootnote()
  );
}

/** Таблица терминов со строкой добавления. */
function buildTable(terms, lang, host) {
  const table = el('table', { class: 'table gl-table' });
  const thead = el('thead', {}, [
    el('tr', {}, [
      el('th', { text: 'Исходный термин' }),
      el('th', { text: 'Перевод' }),
      el('th', { class: 'gl-table__actions-head', text: '' }),
    ]),
  ]);

  const addRow = buildAddRow(lang, host);
  const tbody = el('tbody', {}, [addRow, ...terms.map((term) => termRow(term, host))]);

  table.append(thead, tbody);
  return table;
}

/** Строка добавления нового термина. */
function buildAddRow(lang, host) {
  const sourceInput = el('input', {
    class: 'input gl-cell-input',
    type: 'text',
    placeholder: 'Термин',
  });
  const targetInput = el('input', {
    class: 'input gl-cell-input',
    type: 'text',
    placeholder: 'Перевод',
  });

  const submitBtn = button({
    label: 'Добавить',
    variant: 'primary',
    size: 'sm',
    disabled: true,
    onClick: () => addTerm(sourceInput, targetInput, lang, host),
  });

  const sync = () => {
    submitBtn.disabled = !sourceInput.value.trim() || !targetInput.value.trim();
  };
  sourceInput.addEventListener('input', sync);
  targetInput.addEventListener('input', sync);
  sourceInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !submitBtn.disabled) submitBtn.click();
  });
  targetInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !submitBtn.disabled) submitBtn.click();
  });

  return el('tr', { class: 'gl-add-row' }, [
    el('td', {}, [sourceInput]),
    el('td', {}, [targetInput]),
    el('td', { class: 'gl-table__actions' }, [submitBtn]),
  ]);
}

/** Добавление термина. */
async function addTerm(sourceInput, targetInput, lang, host) {
  const source = sourceInput.value.trim();
  const target = targetInput.value.trim();
  if (!source || !target) return;

  try {
    await call('addGlossaryTerm', { source, target, targetLang: lang });
    sourceInput.value = '';
    targetInput.value = '';
    toast('Термин добавлён', 'success');
    void refresh(host);
  } catch (e) {
    toast(e.message, 'error');
  }
}

/** Строка существующего термина с инлайн-редактированием. */
function termRow(term, host) {
  const hasId = term.id != null;
  const cells = [];

  const makeCell = (field) => {
    const cell = el('td', { class: 'gl-cell' });
    cell.append(el('span', { class: 'gl-cell__text', text: term[field] }));

    cell.addEventListener('dblclick', () => {
      if (cell.querySelector('.gl-cell-input') || !hasId) return;
      startEdit(cell, term, field, host);
    });

    return cell;
  };

  cells.push(makeCell('source'));
  cells.push(makeCell('target'));

  const actionsCell = el('td', { class: 'gl-table__actions' });
  if (hasId) {
    actionsCell.append(
      button({
        iconName: 'trash',
        variant: 'ghost',
        size: 'sm',
        title: 'Удалить термин',
        onClick: () => deleteTermFlow(term, host),
      })
    );
  }

  return el('tr', { class: 'gl-row', dataset: { id: hasId ? String(term.id) : '' } }, [
    ...cells,
    actionsCell,
  ]);
}

/** Превращает ячейку в input для правки. */
function startEdit(cell, term, field, host) {
  const text = cell.querySelector('.gl-cell__text');
  if (!text) return;

  const input = el('input', {
    class: 'input gl-cell-input',
    type: 'text',
    value: term[field],
  });

  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    input.remove();
    text.hidden = false;
  };

  const commit = async () => {
    if (done) return;
    const value = input.value.trim();
    if (value === term[field]) {
      finish();
      return;
    }
    if (!value) {
      finish();
      return;
    }
    const next = { ...term, [field]: value };
    done = true;
    input.disabled = true;
    try {
      await call('updateGlossaryTerm', {
        id: term.id,
        source: next.source,
        target: next.target,
      });
      toast('Термин обновлён', 'success');
      void refresh(host);
    } catch (e) {
      toast(e.message, 'error');
      input.remove();
      text.hidden = false;
    }
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish();
    }
  });
  input.addEventListener('blur', () => commit());

  text.hidden = true;
  cell.append(input);
  input.focus();
  input.select();
}

/** Удаление термина с подтверждением. */
async function deleteTermFlow(term, host) {
  const confirmed = await confirmDialog({
    title: `Удалить термин «${term.source}»?`,
    text: 'Термин будет удалён из глоссария проекта.',
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!confirmed) return;
  try {
    await call('deleteGlossaryTerm', term.id);
    toast('Термин удалён', 'success');
    void refresh(host);
  } catch (e) {
    toast(e.message, 'error');
  }
}

/** Подпись под таблицей. */
function buildFootnote() {
  return el('p', {
    class: 'gl-footnote text-tertiary',
    text: 'Совпадения без учёта регистра защищаются внутри каждого фрагмента при переводе.',
  });
}

/* -------------------------------------------------------------------------
 * Действия шапки
 * ------------------------------------------------------------------------- */

function actions(host) {
  if (!store.get('activeProject')) return [];
  return [
    button({
      label: 'Добавить термин',
      variant: 'primary',
      iconName: 'plus',
      onClick: () => {
        const input = host.querySelector('.gl-add-row .gl-cell-input');
        input?.focus();
      },
    }),
  ];
}

/* -------------------------------------------------------------------------
 * Регистрация
 * ------------------------------------------------------------------------- */

router.registerPage('glossary', {
  title: 'Глоссарий',
  subtitle: 'Защита терминов при переводе',
  render,
  actions,
});
