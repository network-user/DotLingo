/**
 * Глоссарий открытого проекта: термины по целевому языку.
 */

import { call } from '../bridge.js';
import { confirmDialog, modal, spinner } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';
import {
  button,
  el,
  emptyState,
  field,
  langDisplay,
  requireProject,
  run,
  selectBox,
  toast,
} from './common.js';

let generation = 0;

router.registerPage('glossary', {
  title: 'Глоссарий',
  subtitle: 'Термин подставляется в запрос и возвращается в перевод как есть',
  actions: () => [
    button({
      label: 'Добавить термин',
      iconName: 'plus',
      variant: 'primary',
      onClick: () => openEditor(),
    }),
  ],
  render(host) {
    const ticket = ++generation;
    paint(host, ticket);
  },
  destroy() {
    generation += 1;
  },
});

async function paint(host, ticket, lang) {
  host.replaceChildren(spinner());
  const project = store.get('activeProject');
  if (!project) {
    host.replaceChildren();
    requireProject(host, 'Глоссарий хранится в проекте и делится по целевому языку.');
    return;
  }
  const targets = project.targetLangs || [];
  const current = targets.includes(lang) ? lang : targets[0];
  if (!current) {
    host.replaceChildren(emptyState({
      iconName: 'book',
      title: 'Нет целевого языка',
      text: 'Сначала укажите его в проекте.',
      action: button({
        label: 'К проекту',
        variant: 'primary',
        onClick: () => router.showPage('projects'),
      }),
    }));
    return;
  }
  const terms = await run(() => call('listGlossary', current));
  if (ticket !== generation || !host.isConnected) return;
  if (terms === undefined) {
    host.replaceChildren(emptyState({ iconName: 'error', title: 'Глоссарий не прочитался' }));
    return;
  }
  const langSelect = selectBox(
    targets.map((code) => ({ value: code, label: langDisplay(code) })),
    current,
  );
  langSelect.addEventListener('change', () => paint(host, ticket, langSelect.value));

  const body = el('tbody');
  for (const term of terms) {
    body.append(el('tr', {}, [
      el('td', { text: term.source }),
      el('td', { text: term.target }),
      el('td', {}, [
        el('div', { class: 'row-actions' }, [
          button({
            label: 'Править',
            iconName: 'edit',
            size: 'sm',
            onClick: () => openEditor(term, current, host, ticket),
          }),
          button({
            label: 'Удалить',
            iconName: 'trash',
            size: 'sm',
            variant: 'danger',
            onClick: () => removeTerm(term, current, host, ticket),
          }),
        ]),
      ]),
    ]));
  }

  const table = terms.length
    ? el('div', { class: 'table-wrap' }, [
      el('table', { class: 'table' }, [
        el('thead', {}, [
          el('tr', {}, [
            el('th', { text: 'Оригинал' }),
            el('th', { text: 'Перевод' }),
            el('th', { text: '' }),
          ]),
        ]),
        body,
      ]),
    ])
    : emptyState({
      iconName: 'book',
      title: 'Терминов пока нет',
      text: 'Пара фиксирует, как слово должно звучать в выбранном языке.',
    });

  host.replaceChildren(
    el('div', { class: 'stack stack--lg' }, [
      el('div', { class: 'toolbar' }, [langSelect]),
      table,
    ]),
  );
}

function openEditor(term, lang, host, ticket) {
  const project = store.get('activeProject');
  const targets = project?.targetLangs || [];
  const source = el('input', { class: 'input', value: term?.source || '' });
  const target = el('input', { class: 'input', value: term?.target || '' });
  const langSelect = selectBox(
    targets.map((code) => ({ value: code, label: langDisplay(code) })),
    lang || targets[0] || '',
  );
  const dialog = modal({
    title: term ? 'Термин' : 'Новый термин',
    subtitle: 'Обе формы обязательны',
    render: (body) => {
      body.append(el('div', { class: 'stack stack--lg' }, [
        term ? null : field('Язык перевода', langSelect),
        field('Как в оригинале', source),
        field('Как в переводе', target),
      ]));
    },
    actions: [
      { label: 'Отмена', onClick: () => dialog.close() },
      {
        label: 'Сохранить',
        variant: 'primary',
        onClick: async () => {
          const saved = await run(() => term
            ? call('updateGlossaryTerm', {
              id: term.id,
              source: source.value,
              target: target.value,
            })
            : call('addGlossaryTerm', {
              source: source.value,
              target: target.value,
              targetLang: langSelect.value,
            }));
          if (saved === undefined) return;
          dialog.close();
          const page = document.querySelector('#page-host .page');
          if (page && router.currentPage() === 'glossary') {
            paint(page, generation, term ? lang : langSelect.value);
          }
          toast('Термин записан', 'success');
        },
      },
    ],
  });
  source.focus();
}

async function removeTerm(term, lang, host, ticket) {
  const yes = await confirmDialog({
    title: 'Удалить термин?',
    text: `${term.source} → ${term.target}`,
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!yes) return;
  const done = await run(() => call('deleteGlossaryTerm', term.id));
  if (done === undefined) return;
  paint(host, ticket, lang);
}
