/**
 * Проекты: список, создание, языки, модель, контекст и правила.
 */

import { call } from '../bridge.js';
import { confirmDialog, modal, spinner, toast } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';
import {
  allLanguageOptions,
  button,
  el,
  emptyState,
  field,
  formatWhen,
  langDisplay,
  languageChecks,
  loadLanguages,
  loadModels,
  reloadProjects,
  run,
  selectBox,
} from './common.js';

let generation = 0;

router.registerPage('projects', {
  title: 'Проекты',
  subtitle: 'Оригинал остаётся отдельным файлом, перевод и экспорт пишутся рядом',
  actions: () => [
    button({
      label: 'Новый проект',
      iconName: 'plus',
      variant: 'primary',
      onClick: () => openCreate(),
    }),
  ],
  render(host) {
    host.append(spinner());
    paint(host);
  },
  destroy() {
    generation += 1;
  },
});

async function paint(host) {
  const ticket = ++generation;
  const loaded = await run(() => reloadProjects());
  if (ticket !== generation || !host.isConnected) return;
  if (loaded === undefined) {
    host.replaceChildren(emptyState({ iconName: 'error', title: 'Не удалось прочитать проекты' }));
    return;
  }
  await run(() => Promise.all([loadModels(), loadLanguages()]));
  if (ticket !== generation || !host.isConnected) return;

  const projects = store.get('projects') || [];
  const active = store.get('activeProject');
  const list = el('div', { class: 'item-list' });
  if (!projects.length) {
    list.append(
      emptyState({
        iconName: 'folder',
        title: 'Проектов пока нет',
        text: 'Создайте проект и укажите языки. Модель можно выбрать сейчас или позже.',
      }),
    );
  }
  for (const project of projects) {
    const open = active?.id === project.id;
    list.append(
      el('button', {
        class: open ? 'item is-active' : 'item',
        type: 'button',
        onClick: () => selectProject(host, project.id),
      }, [
        el('div', { class: 'item__title truncate', text: project.title }),
        el('div', { class: 'item__meta', text: describe(project) }),
      ]),
    );
  }

  const detail = el('div', { class: 'stack--lg stack' });
  if (!active) {
    detail.append(
      emptyState({
        iconName: 'folder',
        title: 'Выберите проект',
        text: 'Слева список. Новый проект задаёт исходный язык, цели и необязательную модель.',
      }),
    );
  } else {
    detail.append(settingsPanel(host, active));
  }

  host.replaceChildren(el('div', { class: 'split-page' }, [list, detail]));
}

function describe(project) {
  const targets = (project.targetLangs || []).map(langDisplay).join(', ');
  const docs = project.documentCount == null ? '' : ` · документов ${project.documentCount}`;
  const when = project.updatedAt ? ` · ${formatWhen(project.updatedAt)}` : '';
  return `${langDisplay(project.sourceLang)} → ${targets || 'язык не выбран'}${docs}${when}`;
}

async function selectProject(host, projectId) {
  const opened = await run(async () => {
    await call('openProject', projectId);
    return reloadProjects();
  });
  if (opened === undefined) return;
  paint(host);
}

function settingsPanel(host, project) {
  const models = store.get('models') || [];
  const title = el('input', { class: 'input', value: project.title || '' });
  const source = selectBox(
    [
      { value: 'auto', label: langDisplay('auto') },
      ...allLanguageOptions().map((item) => ({ value: item.code, label: `${item.label} · ${item.code}` })),
    ],
    project.sourceLang || 'auto',
  );
  const model = selectBox(
    [
      { value: '', label: 'Не выбрана' },
      ...models.map((item) => ({
        value: item.id,
        label: `${item.name} · ${item.installState === 'installed' ? 'установлена' : item.installState === 'available' ? 'не загружена' : item.installState}`,
      })),
    ],
    project.modelId || '',
  );
  const checksHost = el('div');
  const context = el('textarea', { class: 'textarea', value: project.context || '' });
  const rules = el('textarea', { class: 'textarea', value: project.rules || '' });

  const redrawChecks = () => {
    const chosen = models.find((item) => item.id === model.value);
    const allowed = chosen ? new Set(chosen.languageCodes || []) : null;
    const selected = new Set(
      [...checksHost.querySelectorAll('input:checked')].map((input) => input.value),
    );
    if (!checksHost.childElementCount) {
      for (const code of project.targetLangs || []) selected.add(code);
    }
    const picker = languageChecks(
      allLanguageOptions().map((item) => {
        const blocked = allowed ? !allowed.has(item.code) : false;
        const same = source.value !== 'auto' && item.code === source.value;
        return {
          code: item.code,
          label: `${item.label} · ${item.code}`,
          disabled: blocked || same,
          title: blocked ? 'Этой модели нет в списке языков карточки' : '',
        };
      }),
      selected,
    );
    checksHost.replaceChildren(picker.element);
    checksHost._value = picker.value;
  };
  model.addEventListener('change', redrawChecks);
  source.addEventListener('change', redrawChecks);
  redrawChecks();

  const panel = el('section', { class: 'panel panel--raised stack stack--lg' }, [
    el('header', { class: 'panel__header' }, [
      el('div', {}, [
        el('div', { class: 'panel__title', text: project.title || 'Проект' }),
        el('p', { class: 'panel__subtitle', text: 'Языки ограничиваются выбранной моделью' }),
      ]),
      el('div', { class: 'panel__actions' }, [
        button({
          label: 'Удалить',
          iconName: 'trash',
          variant: 'danger',
          onClick: () => removeProject(host, project),
        }),
      ]),
    ]),
    el('div', { class: 'form-grid' }, [
      el('div', { class: 'field--full' }, [field('Название', title)]),
      field('Исходный язык', source, 'Автоопределение отдаёт язык модели при запуске перевода'),
      field('Модель', model, 'Вес можно загрузить позже на странице «Модели»'),
      el('div', { class: 'field field--full' }, [
        el('span', { class: 'field__label', text: 'Целевые языки' }),
        checksHost,
      ]),
      el('div', { class: 'field--full' }, [
        field('Контекст', context, 'Коротко: о чём документ. Попадает в запрос каждого фрагмента'),
      ]),
      el('div', { class: 'field--full' }, [
        field('Правила', rules, 'Тон, обращения, что не переводить. Тоже копируются в задачу'),
      ]),
    ]),
    el('div', { class: 'row row--between' }, [
      button({
        label: 'К документам',
        iconName: 'document',
        onClick: () => router.showPage('documents'),
      }),
      button({
        label: 'Сохранить',
        variant: 'primary',
        onClick: () => save(host, project, { title, source, model, checksHost, context, rules }),
      }),
    ]),
  ]);
  return panel;
}

async function save(host, project, form) {
  const title = form.title.value.trim();
  const targets = form.checksHost._value ? form.checksHost._value() : [];
  if (!title) {
    toast('Название проекта не может быть пустым', 'warning');
    return;
  }
  if (!targets.length) {
    toast('Выберите хотя бы один целевой язык', 'warning');
    return;
  }
  const saved = await run(async () => {
    if (title !== project.title) await call('renameProject', project.id, title);
    await call('updateProjectSettings', {
      modelId: form.model.value,
      sourceLang: form.source.value,
      targetLangs: targets,
      context: form.context.value,
      rules: form.rules.value,
    });
    return reloadProjects();
  });
  if (saved === undefined) return;
  toast('Настройки проекта сохранены', 'success');
  paint(host);
}

async function removeProject(host, project) {
  const yes = await confirmDialog({
    title: `Удалить «${project.title}»?`,
    text: 'Пропадут копия оригинала, переводы, глоссарий и очередь этого проекта. Исходный файл на диске, откуда его импортировали, не трогается.',
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!yes) return;
  const done = await run(async () => {
    await call('deleteProject', project.id);
    return reloadProjects();
  });
  if (done === undefined) return;
  paint(host);
}

function openCreate() {
  const models = store.get('models') || [];
  const title = el('input', { class: 'input', value: '', placeholder: 'Например, договор поставки' });
  const source = selectBox(
    [
      { value: 'auto', label: langDisplay('auto') },
      ...allLanguageOptions().map((item) => ({ value: item.code, label: `${item.label} · ${item.code}` })),
    ],
    'auto',
  );
  const model = selectBox(
    [
      { value: '', label: 'Выбрать позже' },
      ...models.map((item) => ({ value: item.id, label: item.name })),
    ],
    '',
  );
  const checksHost = el('div');
  const redraw = () => {
    const chosen = models.find((item) => item.id === model.value);
    const allowed = chosen ? new Set(chosen.languageCodes || []) : null;
    const selected = new Set(
      [...checksHost.querySelectorAll('input:checked')].map((input) => input.value),
    );
    if (!checksHost.childElementCount && !allowed) selected.add('ru');
    const picker = languageChecks(
      allLanguageOptions().map((item) => ({
        code: item.code,
        label: `${item.label} · ${item.code}`,
        disabled: allowed ? !allowed.has(item.code) || (source.value !== 'auto' && item.code === source.value) : source.value !== 'auto' && item.code === source.value,
        title: allowed && !allowed.has(item.code) ? 'Нет в карточке модели' : '',
      })),
      selected,
    );
    checksHost.replaceChildren(picker.element);
    checksHost._value = picker.value;
  };
  model.addEventListener('change', redraw);
  source.addEventListener('change', redraw);
  redraw();

  const dialog = modal({
    title: 'Новый проект',
    subtitle: 'Один исходник можно перевести на несколько языков',
    render: (body) => {
      body.append(
        el('div', { class: 'stack stack--lg' }, [
          field('Название', title),
          field('Исходный язык', source),
          field('Модель', model),
          el('div', { class: 'field' }, [
            el('span', { class: 'field__label', text: 'Целевые языки' }),
            checksHost,
          ]),
        ]),
      );
    },
    actions: [
      { label: 'Отмена', onClick: () => dialog.close() },
      {
        label: 'Создать',
        variant: 'primary',
        onClick: async () => {
          const targets = checksHost._value ? checksHost._value() : [];
          const created = await run(() => call('createProject', {
            title: title.value.trim() || 'Новый проект',
            modelId: model.value,
            sourceLang: source.value,
            targetLangs: targets,
          }));
          if (created === undefined) return;
          dialog.close();
          await run(() => reloadProjects());
          const host = document.querySelector('#page-host .page');
          if (host && router.currentPage() === 'projects') paint(host);
        },
      },
    ],
  });
  title.focus();
}
