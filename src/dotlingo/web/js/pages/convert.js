/**
 * Вкладка «Конвертер»: другой формат файла без перевода.
 * Исходник не перезаписывается.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import { el, button, toast, spinner } from '../components.js';
import * as router from '../router.js';

/** @type {Array<{suffix: string, label: string, note: string}>} */
let targets = [];
/** @type {Array<{path: string, name: string, format: string, title: string, blocks: number, warnings: string[]}>} */
let files = [];
let suffix = '.html';
let directory = '';
let running = false;
/** @type {Array<{path: string, name: string, source: string}>} */
let results = [];
/** @type {Array<{name: string, error: string}>} */
let problems = [];
let wired = false;
/** Лишние ряды форматов спрятаны, пока пользователь не нажмёт «Показать». */
let formatsOpen = false;
/** @type {HTMLElement|null} */
let hostEl = null;
/** @type {ResizeObserver|null} */
let formatObserver = null;

const FORMAT_LABEL = {
  txt: 'TXT',
  markdown: 'Markdown',
  docx: 'DOCX',
  epub: 'EPUB',
  pdf: 'PDF',
};

function ensureWire() {
  if (wired) return;
  wired = true;
  store.on('convert_progress', (payload) => {
    if (!running) return;
    const status = hostEl?.querySelector('.cv-status');
    if (status) {
      status.textContent = `Собирается ${payload?.index || 0} из ${payload?.total || 0}: ${payload?.name || ''}`;
    }
  });
  store.on('convert_done', (payload) => {
    running = false;
    results = Array.isArray(payload?.files) ? payload.files : [];
    problems = Array.isArray(payload?.errors) ? payload.errors : [];
    if (results.length && !problems.length) toast(`Готово файлов: ${results.length}`, 'success');
    else if (problems.length) toast(problems[0].error || 'Часть файлов не собралась', 'error', 7000);
    paint();
  });
}

function currentTarget() {
  return targets.find((item) => item.suffix === suffix) || targets[0] || null;
}

async function loadTargets() {
  if (targets.length) return;
  const [rows] = await tryCall('listConversionTargets');
  targets = Array.isArray(rows) ? rows : [];
  if (!targets.some((item) => item.suffix === suffix) && targets[0]) suffix = targets[0].suffix;
}

function render(host) {
  ensureWire();
  hostEl = host;
  host.replaceChildren(el('div', { class: 'page-loading' }, [spinner('lg')]));
  void loadTargets().then(() => {
    if (hostEl === host) paint();
  });
}

function paint() {
  if (!hostEl) return;
  const target = currentTarget();
  hostEl.replaceChildren(el('div', { class: 'cv stack' }, [
    el('section', { class: 'panel cv-drop', dataset: { drop: '1' } }, [
      el('div', { class: 'cv-intro' }, [
        el('p', { class: 'cv-drop__title', text: 'Файлы для конвертации' }),
        el('p', {
          class: 'cv-drop__text',
          text: 'TXT, Markdown, DOCX, EPUB и PDF. Скан читается распознаванием Windows. Исходный файл остаётся на месте.',
        }),
      ]),
      el('div', { class: 'cv-drop__actions' }, [
        button({ label: 'Выбрать файлы', variant: 'primary', onClick: () => void pickFiles() }),
        files.length
          ? button({ label: 'Очистить', onClick: () => { files = []; results = []; problems = []; paint(); } })
          : null,
      ]),
      files.length ? fileList() : el('p', {
        class: 'cv-note',
        text: 'Перетащите документы сюда или выберите их кнопкой.',
      }),
    ]),
    el('section', { class: 'panel cv-setup' }, [
      el('div', { class: 'cv-field' }, [
        el('div', { class: 'cv-field__head' }, [
          el('h2', { class: 'panel__title', text: 'Во что собрать' }),
          formatToggle(),
        ]),
        el('div', {
          class: 'cv-formats',
          id: 'cv-formats',
          role: 'listbox',
          ariaLabel: 'Формат результата',
        }, targets.map((item) => {
          const selected = item.suffix === suffix;
          return el('button', {
            class: `cv-format${selected ? ' is-selected' : ''}`,
            type: 'button',
            role: 'option',
            ariaSelected: selected ? 'true' : 'false',
            onClick: () => {
              suffix = item.suffix;
              paint();
            },
          }, [
            el('span', { class: 'cv-format__label', text: item.label }),
            el('span', { class: 'cv-format__suffix', text: item.suffix }),
          ]);
        })),
        target ? el('p', { class: 'cv-note', text: target.note }) : null,
      ]),
      el('div', { class: 'cv-bar' }, [
        el('div', { class: 'cv-where' }, [
          el('span', { class: 'cv-bar__label', text: 'Куда положить' }),
          button({
            label: 'Рядом с файлом',
            variant: directory ? 'ghost' : 'primary',
            onClick: () => { directory = ''; paint(); },
          }),
          button({
            label: directory ? 'Другая папка' : 'Выбрать папку',
            variant: directory ? 'primary' : 'ghost',
            onClick: () => void pickFolder(),
          }),
        ]),
        el('div', { class: 'cv-run' }, [
          running ? el('p', { class: 'cv-status', text: 'Собирается…' }) : null,
          button({
            label: running ? 'Собирается…' : 'Конвертировать',
            variant: 'primary',
            disabled: running || !files.length,
            onClick: () => void start(),
          }),
        ]),
      ]),
      el('p', {
        class: directory ? 'cv-note cv-path' : 'cv-note',
        text: directory
          ? directory
          : 'Рядом с исходником. Если имя занято, добавится «.converted».',
      }),
    ]),
    results.length || problems.length ? outcome() : null,
  ]));
  const drop = hostEl.querySelector('[data-drop]');
  if (drop) bindDrop(drop);
  syncFormatCollapse();
  watchFormats();
}

function formatToggle() {
  const toggle = button({
    label: formatsOpen ? 'Скрыть' : 'Показать',
    size: 'sm',
    onClick: () => {
      formatsOpen = !formatsOpen;
      paint();
    },
  });
  toggle.dataset.formatsToggle = '1';
  toggle.setAttribute('aria-controls', 'cv-formats');
  toggle.setAttribute('aria-expanded', formatsOpen ? 'true' : 'false');
  return toggle;
}

/** Первый ряд форматов остаётся, остальные прячутся, пока список не раскрыт. */
function syncFormatCollapse() {
  const grid = hostEl?.querySelector('.cv-formats');
  const toggle = hostEl?.querySelector('[data-formats-toggle]');
  if (!(grid instanceof HTMLElement)) return;
  const items = [...grid.querySelectorAll('.cv-format')];
  if (!items.length) return;
  const firstTop = items[0].offsetTop;
  const rest = items.filter((item) => item.offsetTop > firstTop + 1);
  if (toggle instanceof HTMLElement) toggle.hidden = rest.length === 0;
  const collapse = !formatsOpen && rest.length > 0;
  grid.classList.toggle('is-collapsed', collapse);
  grid.style.maxHeight = collapse ? `${items[0].offsetHeight}px` : '';
  items.forEach((item) => {
    const hidden = collapse && rest.includes(item);
    item.tabIndex = hidden ? -1 : 0;
    item.setAttribute('aria-hidden', hidden ? 'true' : 'false');
  });
}

function watchFormats() {
  formatObserver?.disconnect();
  formatObserver = null;
  const panel = hostEl?.querySelector('.cv-setup');
  if (!panel || typeof ResizeObserver === 'undefined') return;
  let width = -1;
  formatObserver = new ResizeObserver((entries) => {
    const next = Math.round(entries[0].contentRect.width);
    if (next === width) return;
    width = next;
    syncFormatCollapse();
  });
  formatObserver.observe(panel);
}

function fileList() {
  return el('div', { class: 'cv-files' }, files.map((file) => el('article', { class: 'cv-file' }, [
    el('div', { class: 'cv-file__main' }, [
      el('p', { class: 'cv-file__name', text: file.name }),
      el('p', {
        class: 'cv-file__meta',
        text: [
          FORMAT_LABEL[file.format] || file.format,
          file.blocks ? `${file.blocks} фрагм.` : 'нет текста',
          file.title && file.title !== file.name ? file.title : '',
        ].filter(Boolean).join(' · '),
      }),
      ...(file.warnings || []).slice(0, 2).map((warning) => el('p', { class: 'cv-note', text: warning })),
    ]),
    button({
      label: 'Убрать',
      size: 'sm',
      onClick: () => {
        files = files.filter((item) => item.path !== file.path);
        paint();
      },
    }),
  ])));
}

function outcome() {
  return el('section', { class: 'panel stack' }, [
    el('h2', { class: 'panel__title', text: 'Результат' }),
    ...results.map((file) => el('div', { class: 'cv-result' }, [
      el('p', { class: 'cv-file__name', text: file.name }),
      el('p', { class: 'cv-note', text: `Из ${file.source}` }),
      el('div', { class: 'cv-result__actions' }, [
        button({
          label: 'Открыть',
          size: 'sm',
          variant: 'primary',
          onClick: () => void call('openPath', file.path).catch((error) => toast(error.message, 'error')),
        }),
        button({
          label: 'В папке',
          size: 'sm',
          onClick: () => void call('revealPath', file.path).catch((error) => toast(error.message, 'error')),
        }),
        button({
          label: 'Просмотр',
          size: 'sm',
          onClick: () => void preview(file.path),
        }),
      ]),
    ])),
    ...problems.map((item) => el('p', { class: 'cv-error', text: `${item.name}: ${item.error}` })),
  ]);
}

async function preview(path) {
  const [data, error] = await tryCall('previewExport', path);
  if (error || !data) {
    toast(error?.message || 'Просмотр для этого файла недоступен.', 'error');
    return;
  }
  if (data.kind === 'text') {
    const { modal } = await import('../components.js');
    modal({
      title: data.name || 'Результат',
      panelClass: 'modal--preview',
      render: (body) => body.append(el('pre', { class: 'result-preview', text: data.text || '' })),
    });
    return;
  }
  toast('Этот формат открывается своей программой.', 'info');
}

async function pickFiles() {
  const [picked, error] = await tryCall('resolveImportPaths');
  if (error) {
    toast(error.message, 'error');
    return;
  }
  const paths = Array.isArray(picked) ? picked.filter(Boolean) : [];
  if (paths.length) await addPaths(paths);
}

async function pickFolder() {
  const [picked, error] = await tryCall('resolveOutputDirectory');
  if (error) {
    toast(error.message, 'error');
    return;
  }
  if (picked) {
    directory = String(picked);
    paint();
  }
}

async function addPaths(paths) {
  const known = new Set(files.map((item) => item.path));
  const fresh = paths.filter((path) => !known.has(path));
  if (!fresh.length) return;
  const [data, error] = await tryCall('inspectConversion', fresh);
  if (error || !data) {
    toast(error?.message || 'Файлы не прочитались.', 'error');
    return;
  }
  files = [...files, ...(data.files || [])];
  (data.errors || []).forEach((item) => toast(`${item.name}: ${item.error}`, 'error', 6000));
  paint();
}

async function start() {
  if (running || !files.length) return;
  running = true;
  results = [];
  problems = [];
  paint();
  const [, error] = await tryCall('convertDocuments', {
    paths: files.map((item) => item.path),
    suffix,
    directory,
  });
  if (error) {
    running = false;
    toast(error.message, 'error');
    paint();
  }
}

function bindDrop(node) {
  if (!(node instanceof HTMLElement) || node.dataset.bound === '1') return;
  node.dataset.bound = '1';
  const clear = () => node.classList.remove('is-drop');
  node.addEventListener('dragover', (event) => {
    if (!event.dataTransfer) return;
    event.preventDefault();
    node.classList.add('is-drop');
  });
  node.addEventListener('dragleave', clear);
  node.addEventListener('drop', (event) => {
    event.preventDefault();
    clear();
    const list = event.dataTransfer?.files;
    if (!list?.length) return;
    const host = window.chrome?.webview;
    if (host?.postMessageWithAdditionalObjects) {
      try {
        host.postMessageWithAdditionalObjects('FilesDropped', list);
      } catch {
        // claimDroppedFile ниже попросит выбрать файл кнопкой.
      }
    }
    void receive(list);
  });
}

async function receive(list) {
  const paths = [];
  for (const file of list) {
    const [found, error] = await tryCall('claimDroppedFile', file.name);
    if (error) {
      toast(error.message, 'error');
      continue;
    }
    if (found) paths.push(found);
    else toast(`Не вижу путь к «${file.name}». Выберите его кнопкой.`, 'error');
  }
  if (paths.length) await addPaths(paths);
}

function destroy() {
  formatObserver?.disconnect();
  formatObserver = null;
  hostEl = null;
}

router.registerPage('convert', {
  title: 'Конвертер',
  subtitle: 'Другой формат того же текста, без перевода.',
  help: 'Берёт текст файла и собирает новый. Скан PDF читается распознаванием Windows. Исходный файл не изменяется.',
  render,
  destroy,
});
