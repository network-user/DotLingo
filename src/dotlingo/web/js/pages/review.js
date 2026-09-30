/**
 * Проверка: оригинал рядом с переводом, поиск и ручная правка.
 */

import { call } from '../bridge.js';
import { debounce, spinner } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';
import {
  el,
  emptyState,
  langDisplay,
  requireProject,
  run,
  selectBox,
} from './common.js';

let generation = 0;

router.registerPage('review', {
  title: 'Проверка',
  subtitle: 'Правка пишется в перевод и не меняет оригинал',
  layout: 'wide',
  render(host) {
    const ticket = ++generation;
    host.append(spinner());
    paint(host, ticket);
  },
  destroy() {
    generation += 1;
  },
});

async function paint(host, ticket) {
  const project = store.get('activeProject');
  if (!project) {
    host.replaceChildren();
    requireProject(host, 'Проверка открывается внутри проекта, рядом с импортированным текстом.');
    return;
  }
  const docs = await run(() => call('listDocuments'));
  if (ticket !== generation || !host.isConnected) return;
  if (docs === undefined) {
    host.replaceChildren(emptyState({ iconName: 'error', title: 'Не удалось прочитать документы' }));
    return;
  }
  if (!docs.length) {
    host.replaceChildren(
      emptyState({
        iconName: 'search',
        title: 'Нечего проверять',
        text: 'Сначала импортируйте документ.',
      }),
    );
    return;
  }
  const preferred = store.get('reviewDocId');
  const docId = docs.some((doc) => doc.id === preferred) ? preferred : docs[0].id;
  const targets = project.targetLangs || [];
  const docSelect = selectBox(
    docs.map((doc) => ({ value: doc.id, label: doc.name })),
    docId,
  );
  const langSelect = selectBox(
    targets.map((code) => ({ value: code, label: langDisplay(code) })),
    targets[0] || '',
  );
  const query = el('input', { class: 'input grow', placeholder: 'Поиск по оригиналу и переводу' });
  const body = el('div', { class: 'review__scroll' });
  const toolbar = el('div', { class: 'toolbar' }, [
    docSelect,
    langSelect,
    query,
  ]);

  const load = () => fill(body, docSelect.value, langSelect.value, query.value, ticket);
  docSelect.addEventListener('change', () => {
    store.set('reviewDocId', docSelect.value);
    load();
  });
  langSelect.addEventListener('change', load);
  query.addEventListener('input', debounce(load, 160));

  host.replaceChildren(el('div', { class: 'review' }, [toolbar, body]));
  load();
}

async function fill(body, docId, lang, query, ticket) {
  body.replaceChildren(spinner());
  const doc = await run(() => call('getDocument', docId));
  if (ticket !== generation || !body.isConnected) return;
  if (!doc) {
    body.replaceChildren(emptyState({ iconName: 'error', title: 'Документ не открылся' }));
    return;
  }
  const needle = query.trim().toLowerCase();
  const translations = doc.translations?.[lang] || {};
  let section = '';
  const nodes = [];
  if (doc.warnings?.length) {
    nodes.push(el('div', { class: 'note note--warn', text: doc.warnings.join(' ') }));
  }
  if (doc.detectedLanguage) {
    nodes.push(el('p', { class: 'tiny', text: `Определённый исходный язык: ${langDisplay(doc.detectedLanguage)}` }));
  }
  for (const block of doc.blocks || []) {
    const translated = translations[String(block.order)] || '';
    if (needle && !`${block.text}\n${translated}`.toLowerCase().includes(needle)) continue;
    if (block.sectionTitle && block.sectionTitle !== section) {
      section = block.sectionTitle;
      nodes.push(el('div', { class: 'section-label', text: section }));
    }
    nodes.push(renderBlock(doc.id, lang, block, translated));
  }
  if (!nodes.length) {
    nodes.push(emptyState({ iconName: 'search', title: 'Ничего не найдено', text: 'Измените запрос.' }));
  }
  body.replaceChildren(...nodes);
}

function renderBlock(docId, lang, block, translated) {
  if (!block.translatable) {
    return el('article', { class: 'block block--static' }, [
      el('p', { class: 'block__source', text: block.text || '' }),
    ]);
  }
  const status = el('span', { class: 'tiny', text: '' });
  const area = el('textarea', {
    class: 'textarea',
    value: translated,
  });
  area.placeholder = 'Перевод';
  const save = debounce(async () => {
    const text = area.value;
    const saved = await run(() => call('saveEdit', docId, block.order, text, lang));
    if (saved === undefined) {
      status.textContent = 'Не сохранилось';
      return;
    }
    status.textContent = 'Сохранено';
  }, 420);
  area.addEventListener('input', () => {
    status.textContent = 'Правка…';
    save();
  });
  return el('article', { class: 'block' }, [
    el('div', { class: 'block__source' }, [
      el('div', { class: 'block__kicker', text: 'Оригинал' }),
      el('p', { class: 'block__source', text: block.text || '' }),
    ]),
    el('div', { class: 'block__edit' }, [
      el('div', { class: 'row row--between' }, [
        el('div', { class: 'block__kicker', text: 'Перевод' }),
        status,
      ]),
      area,
    ]),
  ]);
}

