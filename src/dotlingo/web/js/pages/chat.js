/**
 * Диалог без проекта: прокручиваемая лента, перевод или разговор,
 * вложения и список сохранённых диалогов.
 */

import { call, isDemo, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { el, button, toast, confirmDialog } from '../components.js';

const TEXT_LIMIT = 2000;
const FILE_CAP = 3;

/** @type {Array<{id: string, role: 'user'|'model', text: string, pending?: boolean, error?: string}>} */
let thread = [];

/** @type {Array<{id: string, title: string, mode: string, updatedAt: string}>} */
let dialogs = [];

/** @type {Array<{name: string, text: string, truncated: boolean}>} */
let attachments = [];

let draft = '';
let context = '';
let sourceLang = 'auto';
let targetLang = 'ru';
let modelId = '';
let mode = 'translate';
let dialogId = '';
let requestId = '';
let sending = false;
let wired = false;
let stick = true;
let booted = false;
let meterNote = 'Контекст появится после выбора модели.';

function installedModels() {
  return (store.get('models') || []).filter((model) => model.installed);
}

function currentModel() {
  return installedModels().find((model) => model.id === modelId) || null;
}

function newId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

function langLabel(code) {
  if (code === 'auto') return 'Авто';
  const languages = store.get('languages') || {};
  return languages[code] || String(code).toUpperCase();
}

function previousPairs() {
  const pairs = [];
  for (let index = 0; index < thread.length - 1; index += 1) {
    const source = thread[index];
    const reply = thread[index + 1];
    if (source?.role === 'user' && reply?.role === 'model' && reply.text && !reply.error) {
      pairs.push({ source: source.text, translation: reply.text });
    }
  }
  return pairs.slice(-6);
}

function charCount() {
  const history = thread.reduce((sum, turn) => sum + (turn.text?.length || 0), 0);
  const files = attachments.reduce((sum, file) => sum + file.text.length, 0);
  return history + draft.length + context.length + files;
}

function attachmentPayload() {
  return attachments
    .map((file) => `«${file.name}»\n${file.text}`)
    .join('\n\n')
    .slice(0, 8000);
}

function ensureWire() {
  if (wired) return;
  wired = true;
  store.on('chat_token', (payload) => {
    if (!payload || payload.requestId !== requestId) return;
    const turn = thread.find((item) => item.id === requestId);
    if (!turn) return;
    turn.text += payload.text || '';
    turn.pending = true;
    const node = document.querySelector(`[data-turn="${CSS.escape(requestId)}"] .chat-bubble__text`);
    if (node) node.textContent = turn.text;
    else paintLog();
    scrollLog();
  });
  store.on('chat_done', (payload) => {
    if (!payload || payload.requestId !== requestId) return;
    const turn = thread.find((item) => item.id === requestId);
    sending = false;
    requestId = '';
    if (turn) {
      turn.pending = false;
      if (payload.ok && payload.text) turn.text = payload.text;
      if (!payload.ok) {
        const message = payload.error || 'Ответ не получен.';
        if (!turn.text) {
          turn.error = message;
          turn.text = message;
        } else toast(message, 'error');
      }
    }
    paintLog();
    paintSend();
    void persist();
    void refreshMeter();
  });
  store.on('hardware_detected', () => void refreshMeter());
}

function logElement() {
  return document.querySelector('.chat-log');
}

function nearBottom(log) {
  return log.scrollHeight - log.scrollTop - log.clientHeight < 48;
}

function scrollLog() {
  const log = logElement();
  if (!log || !stick) return;
  log.scrollTop = log.scrollHeight;
}

function bindLog(log) {
  log.addEventListener('scroll', () => {
    stick = nearBottom(log);
    const jump = document.querySelector('.chat-jump');
    if (jump) jump.hidden = stick;
  });
}

function paintLog() {
  const log = logElement();
  if (!log) return;
  const top = log.scrollTop;
  if (thread.length === 0) {
    log.replaceChildren(el('p', {
      class: 'chat-empty',
      text: mode === 'ask'
        ? 'Напишите сообщение. Модель ответит на этом компьютере, без сети.'
        : 'Напишите фрагмент. Модель переведёт его на выбранный язык.',
    }));
    return;
  }
  log.replaceChildren(...thread.map((turn) => bubble(turn)));
  if (stick) log.scrollTop = log.scrollHeight;
  else log.scrollTop = top;
  const jump = document.querySelector('.chat-jump');
  if (jump) jump.hidden = stick;
}

function bubble(turn) {
  const copy = turn.role === 'model' && turn.text && !turn.pending
    ? button({
        label: 'Копировать',
        size: 'sm',
        onClick: () => void copyText(turn.text),
      })
    : null;
  return el('article', {
    class: `chat-bubble chat-bubble--${turn.role}${turn.error ? ' is-error' : ''}`,
    dataset: { turn: turn.id },
  }, [
    el('p', { class: 'chat-bubble__role', text: turn.role === 'user' ? 'Вы' : 'Модель' }),
    el('p', {
      class: 'chat-bubble__text',
      text: turn.text || (turn.pending ? 'Модель отвечает…' : ''),
    }),
    copy,
  ]);
}

function paintSend() {
  const send = document.querySelector('.chat-send');
  if (!send) return;
  send.textContent = '';
  send.append(sending ? 'Стоп' : 'Отправить');
  send.disabled = !sending && !draft.trim() && attachments.length === 0;
  const counter = document.querySelector('.chat-counter');
  if (counter) counter.textContent = `${draft.length} / ${TEXT_LIMIT}`;
}

function paintMeter() {
  const node = document.querySelector('.chat-meter');
  if (node) node.textContent = meterNote;
}

function paintFiles() {
  const row = document.querySelector('.chat-files');
  if (!row) return;
  row.replaceChildren(...attachments.map((file, index) => el('span', { class: 'chat-file' }, [
    el('span', {
      text: file.truncated ? `${file.name} · обрезан` : file.name,
    }),
    el('button', {
      type: 'button',
      class: 'chat-file__remove',
      text: 'убрать',
      onClick: () => {
        attachments.splice(index, 1);
        paintFiles();
        paintSend();
        void refreshMeter();
      },
    }),
  ])));
}

function paintDialogs() {
  const list = document.querySelector('.chat-dialogs__list');
  if (!list) return;
  if (!dialogs.length) {
    list.replaceChildren(el('p', { class: 'chat-empty', text: 'Пока нет сохранённых диалогов.' }));
    return;
  }
  list.replaceChildren(...dialogs.map((item) => {
    const row = el('div', { class: `chat-dialog${item.id === dialogId ? ' is-selected' : ''}` }, [
      el('button', {
        type: 'button',
        class: 'chat-dialog__open',
        onClick: () => void openDialog(item.id),
      }, [
        el('span', { class: 'chat-dialog__title', text: item.title || 'Диалог' }),
        el('span', {
          class: 'chat-dialog__meta',
          text: item.mode === 'ask' ? 'Общение' : 'Перевод',
        }),
      ]),
      el('button', {
        type: 'button',
        class: 'chat-dialog__delete',
        text: 'Удалить',
        onClick: () => void removeDialog(item.id),
      }),
    ]);
    return row;
  }));
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Скопировано', 'success');
  } catch {
    toast('Не удалось скопировать.', 'error');
  }
}

let meterTimer = 0;

function queueMeter() {
  clearTimeout(meterTimer);
  meterTimer = setTimeout(() => void refreshMeter(), 200);
}

async function refreshMeter() {
  const model = currentModel();
  if (!model) {
    meterNote = 'Сначала скачайте модель.';
    paintMeter();
    return;
  }
  const [data] = await tryCall('dialogMeter', { modelId: model.id, charCount: charCount() });
  if (!data) {
    meterNote = 'Нагрузку и контекст сейчас не прочитать.';
    paintMeter();
    return;
  }
  const cpu = Number.isFinite(data.cpuPercent) ? `CPU ${data.cpuPercent}%` : 'CPU —';
  const ram = Number.isFinite(data.ramPercent) ? `RAM ${data.ramPercent}%` : 'RAM —';
  meterNote = `${cpu} · ${ram} · ${data.placement || 'CPU'} · контекст ${data.contextPercent}% (оценка ${data.contextUsed} из ${data.contextLimit})`;
  paintMeter();
  const bar = document.querySelector('.chat-meter__fill');
  if (bar) bar.style.width = `${Math.min(100, data.contextPercent || 0)}%`;
}

async function loadDialogs() {
  const [rows] = await tryCall('listDialogs');
  dialogs = Array.isArray(rows) ? rows : [];
  paintDialogs();
  if (booted || sending) return;
  booted = true;
  if (dialogs[0] && thread.length === 0) await openDialog(dialogs[0].id);
}

function snapshot() {
  const first = thread.find((turn) => turn.role === 'user' && turn.text.trim());
  const title = first ? first.text.replace(/\s+/g, ' ').trim().slice(0, 48) : 'Новый диалог';
  return {
    id: dialogId,
    title,
    mode,
    modelId,
    sourceLang,
    targetLang,
    context,
    messages: thread
      .filter((turn) => turn.text && !turn.pending)
      .map((turn) => ({
        id: turn.id,
        role: turn.role,
        text: turn.text,
        error: turn.error || '',
      })),
  };
}

async function persist() {
  if (!dialogId || !thread.some((turn) => turn.text && !turn.pending)) return;
  const [saved] = await tryCall('saveDialog', snapshot());
  if (!saved) return;
  const summary = {
    id: saved.id,
    title: saved.title,
    mode: saved.mode,
    updatedAt: saved.updatedAt,
  };
  dialogs = [summary, ...dialogs.filter((item) => item.id !== saved.id)];
  paintDialogs();
}

async function openDialog(id) {
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  if (id === dialogId) return;
  await persist();
  const [data, error] = await tryCall('loadDialog', id);
  if (error || !data) {
    toast(error?.message || 'Диалог не открылся.', 'error');
    return;
  }
  dialogId = data.id;
  mode = data.mode === 'ask' ? 'ask' : 'translate';
  modelId = data.modelId || modelId;
  sourceLang = data.sourceLang || 'auto';
  targetLang = data.targetLang || targetLang;
  context = data.context || '';
  thread = (data.messages || []).map((turn) => ({
    id: turn.id || newId(),
    role: turn.role,
    text: turn.text || '',
    error: turn.error || '',
  }));
  attachments = [];
  draft = '';
  stick = true;
  const host = document.getElementById('page-host');
  if (host) render(host);
}

async function removeDialog(id) {
  if (sending && id === dialogId) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  const yes = await confirmDialog({
    title: 'Удалить диалог?',
    text: 'Сообщения этого диалога будут стёрты с компьютера.',
    confirmLabel: 'Удалить',
    danger: true,
  });
  if (!yes) return;
  await tryCall('deleteDialog', id);
  dialogs = dialogs.filter((item) => item.id !== id);
  if (dialogId === id) {
    dialogId = '';
    thread = [];
    const host = document.getElementById('page-host');
    if (host) render(host);
  } else paintDialogs();
}

function startNew() {
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  void persist();
  dialogId = newId();
  thread = [];
  attachments = [];
  draft = '';
  stick = true;
  const host = document.getElementById('page-host');
  if (host) render(host);
}

async function attachFiles() {
  if (attachments.length >= FILE_CAP) {
    toast(`Можно прикрепить не больше ${FILE_CAP} файлов.`, 'info');
    return;
  }
  const [paths, error] = await tryCall('resolveImportPaths');
  if (error) {
    toast(error.message, 'error');
    return;
  }
  const list = Array.isArray(paths) ? paths : [];
  for (const path of list) {
    if (attachments.length >= FILE_CAP) break;
    const [file, readError] = await tryCall('readChatAttachment', path);
    if (readError || !file?.text) {
      toast(readError?.message || 'В файле нет текста.', 'error');
      continue;
    }
    attachments.push({
      name: file.name || 'файл',
      text: file.text,
      truncated: Boolean(file.truncated),
    });
  }
  paintFiles();
  paintSend();
  void refreshMeter();
}

async function send() {
  if (sending) {
    try {
      await call('cancelScratch');
    } catch (error) {
      toast(error.message, 'error');
    }
    return;
  }
  const text = draft.trim();
  if (!text && attachments.length === 0) return;
  const model = currentModel();
  if (!model) {
    toast('Сначала скачайте модель.', 'error');
    return;
  }
  if (mode === 'translate' && !targetLang) {
    toast('Выберите язык перевода.', 'error');
    return;
  }
  if (text.length > TEXT_LIMIT) {
    toast(`Сообщение длиннее ${TEXT_LIMIT} знаков. Большой текст положите в проект.`, 'error');
    return;
  }
  if (!dialogId) dialogId = newId();
  const shown = [text, ...attachments.map((file) => `Файл «${file.name}»`)].filter(Boolean).join('\n');
  const files = attachmentPayload();
  if (isDemo()) {
    thread.push({ id: newId(), role: 'user', text: shown });
    thread.push({
      id: newId(),
      role: 'model',
      text: 'Демонстрация без локальной модели.',
    });
    draft = '';
    attachments = [];
    stick = true;
    paintLog();
    paintSend();
    paintFiles();
    return;
  }
  thread.push({ id: newId(), role: 'user', text: shown });
  draft = '';
  attachments = [];
  stick = true;
  sending = true;
  const field = document.querySelector('.chat-input');
  if (field) field.value = '';
  paintLog();
  paintSend();
  paintFiles();
  try {
    const data = await call('askScratch', {
      modelId: model.id,
      sourceLang: sourceLang,
      targetLang: targetLang,
      text,
      context,
      attachment: files,
      mode,
      previous: previousPairs().filter((pair) => pair.source !== shown),
    });
    requestId = data.requestId;
    thread.push({ id: requestId, role: 'model', text: '', pending: true });
    paintLog();
    void persist();
  } catch (error) {
    sending = false;
    thread.push({ id: newId(), role: 'model', text: error.message, error: error.message });
    paintLog();
    paintSend();
  }
}

function render(host) {
  ensureWire();
  const models = installedModels();
  if (!modelId || !models.some((model) => model.id === modelId)) {
    modelId = models[0]?.id || '';
  }
  const model = currentModel();
  const codes = model?.languageCodes || [];
  if (targetLang !== 'auto' && codes.length && !codes.includes(targetLang)) {
    targetLang = codes.includes('ru') ? 'ru' : codes[0];
  }
  if (!dialogId) dialogId = newId();

  const modelSelect = el('select', {
    class: 'select',
    onChange: (event) => {
      modelId = event.target.value;
      render(host);
    },
  }, models.map((item) => el('option', { value: item.id, text: item.name })));
  modelSelect.value = modelId;

  const sourceSelect = el('select', {
    class: 'select',
    onChange: (event) => {
      sourceLang = event.target.value;
    },
  }, [
    el('option', { value: 'auto', text: 'Авто' }),
    ...codes.map((code) => el('option', { value: code, text: langLabel(code) })),
  ]);
  sourceSelect.value = sourceLang === 'auto' || codes.includes(sourceLang) ? sourceLang : 'auto';

  const targetSelect = el('select', {
    class: 'select',
    onChange: (event) => {
      targetLang = event.target.value;
      void refreshMeter();
    },
  }, codes.map((code) => el('option', { value: code, text: langLabel(code) })));
  if (codes.includes(targetLang)) targetSelect.value = targetLang;

  const input = el('textarea', {
    class: 'textarea chat-input',
    rows: 3,
    title: 'Enter отправляет, Shift+Enter переносит строку',
    placeholder: mode === 'ask' ? 'Сообщение…' : 'Фрагмент для перевода…',
    onInput: (event) => {
      draft = event.target.value;
      paintSend();
      queueMeter();
    },
    onKeydown: (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        void send();
      }
    },
  });
  input.value = draft;

  const contextInput = el('textarea', {
    class: 'textarea',
    rows: 2,
    placeholder: 'Необязательно: предмет, тон, как обращаться с именами…',
    onInput: (event) => {
      context = event.target.value;
      queueMeter();
    },
  });
  contextInput.value = context;

  const log = el('div', { class: 'chat-log', role: 'log', tabindex: '0' });
  bindLog(log);

  const hyNote = mode === 'ask' && model?.promptStyle === 'hy-mt2'
    ? el('p', {
        class: 'chat-note',
        text: 'Эта модель обучена переводить. В общении она может пересказать фразу, а не поддержать разговор.',
      })
    : null;

  host.replaceChildren(el('div', { class: 'chat' }, [
    el('aside', { class: 'chat-dialogs', ariaLabel: 'Диалоги' }, [
      el('div', { class: 'chat-dialogs__head' }, [
        el('p', { class: 'chat-dialogs__label', text: 'Диалоги' }),
        button({ label: 'Новый', size: 'sm', onClick: startNew }),
      ]),
      el('div', { class: 'chat-dialogs__list' }),
    ]),
    el('div', { class: 'chat-main' }, [
      el('div', { class: 'chat-bar' }, [
        models.length
          ? field('Модель', modelSelect)
          : el('div', { class: 'chat-missing' }, [
              el('p', { text: 'Чтобы начать диалог, скачайте локальную модель.' }),
              button({
                label: 'К моделям',
                variant: 'primary',
                onClick: () => router.showPage('models'),
              }),
            ]),
        el('div', { class: 'chat-modes', role: 'radiogroup', ariaLabel: 'Режим' }, [
          modeButton('translate', 'Перевод', host),
          modeButton('ask', 'Общение', host),
        ]),
        mode === 'translate' && models.length ? field('Оригинал', sourceSelect) : null,
        mode === 'translate' && models.length ? field('Перевод', targetSelect) : null,
        hyNote,
      ]),
      el('div', { class: 'chat-stage' }, [
        log,
        el('button', {
          type: 'button',
          class: 'chat-jump',
          hidden: true,
          text: 'Вниз',
          onClick: () => {
            stick = true;
            scrollLog();
          },
        }),
      ]),
      el('form', {
            class: 'chat-composer',
            onSubmit: (event) => {
              event.preventDefault();
              void send();
            },
          }, [
            el('div', { class: 'chat-meter-row' }, [
              el('div', { class: 'chat-meter-track', ariaHidden: 'true' }, [
                el('div', { class: 'chat-meter__fill' }),
              ]),
              el('p', { class: 'chat-meter', text: meterNote }),
            ]),
            el('details', { class: 'chat-context' }, [
              el('summary', { text: 'Контекст для тона' }),
              contextInput,
            ]),
            el('div', { class: 'chat-files' }),
            input,
            el('div', { class: 'chat-composer__row' }, [
              el('span', { class: 'chat-counter', text: `${draft.length} / ${TEXT_LIMIT}` }),
              el('div', { class: 'chat-composer__actions' }, [
                button({ label: 'Файл', onClick: () => void attachFiles() }),
                el('button', {
                  class: 'btn btn--primary chat-send',
                  type: 'submit',
                  disabled: !draft.trim() && attachments.length === 0 && !sending,
                }, [sending ? 'Стоп' : 'Отправить']),
              ]),
            ]),
          ]),
    ]),
  ]));
  paintDialogs();
  paintLog();
  paintFiles();
  void loadDialogs();
  void refreshMeter();
}

function field(label, control) {
  return el('label', { class: 'chat-field' }, [
    el('span', { class: 'chat-field__label', text: label }),
    control,
  ]);
}

function modeButton(value, label, host) {
  return el('button', {
    class: `chat-mode${mode === value ? ' is-selected' : ''}`,
    type: 'button',
    onClick: () => {
      if (mode === value) return;
      mode = value;
      render(host);
    },
  }, [label]);
}

router.registerPage('chat', {
  title: 'Диалог',
  subtitle: 'Перевод или разговор на этом компьютере',
  layout: 'chat',
  render,
});
