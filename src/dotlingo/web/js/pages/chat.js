/**
 * Диалог без проекта.
 * Установленные модели DotLingo — переводческие: реплика уходит как фрагмент,
 * ответ — перевод. Свободный вопрос доступен только модели, у которой стиль не hy-mt2.
 * История живёт в этой вкладке, пока приложение открыто, и не создаёт проект.
 */

import { call, isDemo } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { el, button, toast } from '../components.js';

const TEXT_LIMIT = 2000;

/** @type {Array<{id: string, role: 'user'|'model', text: string, pending?: boolean, error?: string}>} */
const thread = [];

let draft = '';
let context = '';
let sourceLang = 'auto';
let targetLang = 'ru';
let modelId = '';
let mode = 'translate';
let requestId = '';
let sending = false;
let wired = false;

function installedModels() {
  return (store.get('models') || []).filter((model) => model.installed);
}

function currentModel() {
  return installedModels().find((model) => model.id === modelId) || null;
}

function asksAllowed(model) {
  return Boolean(model && model.promptStyle && model.promptStyle !== 'hy-mt2');
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
    const translation = thread[index + 1];
    if (source?.role === 'user' && translation?.role === 'model' && translation.text && !translation.error) {
      pairs.push({ source: source.text, translation: translation.text });
    }
  }
  return pairs.slice(-2);
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
        } else {
          toast(message, 'error');
        }
      }
    }
    paint();
  });
}

function scrollLog() {
  const log = document.querySelector('.chat-log');
  if (log) log.scrollTop = log.scrollHeight;
}

function paint() {
  const root = document.querySelector('.chat');
  if (!root) return;
  const log = root.querySelector('.chat-log');
  const send = root.querySelector('.chat-send');
  const counter = root.querySelector('.chat-counter');
  if (counter) counter.textContent = `${draft.length} / ${TEXT_LIMIT}`;
  if (send) {
    send.textContent = '';
    send.append(sending ? 'Стоп' : 'Отправить');
    send.disabled = !sending && !draft.trim();
  }
  if (!log) return;
  log.replaceChildren();
  if (thread.length === 0) {
    log.append(el('p', {
      class: 'chat-empty',
      text: 'Напишите фрагмент. Модель переведёт его на выбранный язык. Отдельный проект для этого не нужен.',
    }));
    return;
  }
  for (const turn of thread) {
    const copy = turn.role === 'model' && turn.text && !turn.pending
      ? button({
          label: 'Копировать',
          size: 'sm',
          onClick: () => void copyText(turn.text),
        })
      : null;
    log.append(el('article', {
      class: `chat-bubble chat-bubble--${turn.role}${turn.error ? ' is-error' : ''}`,
      dataset: { turn: turn.id },
    }, [
      el('p', { class: 'chat-bubble__role', text: turn.role === 'user' ? 'Текст' : 'Ответ' }),
      el('p', { class: 'chat-bubble__text', text: turn.text || (turn.pending ? 'Модель отвечает…' : '') }),
      copy,
    ]));
  }
  scrollLog();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Скопировано', 'success');
  } catch {
    toast('Не удалось скопировать.', 'error');
  }
}

async function send() {
  if (sending) {
    sending = false;
    try {
      await call('cancelScratch');
    } catch (error) {
      toast(error.message, 'error');
    }
    return;
  }
  const text = draft.trim();
  if (!text) return;
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
    toast(`Фрагмент длиннее ${TEXT_LIMIT} знаков. Для большого текста создайте проект.`, 'error');
    return;
  }
  if (isDemo()) {
    thread.push({ id: `user-${thread.length}`, role: 'user', text });
    thread.push({
      id: `demo-${thread.length}`,
      role: 'model',
      text: 'Демонстрация без локальной модели. В окне приложения здесь будет перевод.',
    });
    draft = '';
    const field = document.querySelector('.chat-input');
    if (field) field.value = '';
    paint();
    return;
  }
  const userId = `user-${Date.now()}`;
  thread.push({ id: userId, role: 'user', text });
  draft = '';
  const field = document.querySelector('.chat-input');
  if (field) field.value = '';
  sending = true;
  paint();
  try {
    const data = await call('askScratch', {
      modelId: model.id,
      sourceLang: sourceLang,
      targetLang: targetLang,
      text,
      context,
      mode: asksAllowed(model) ? mode : 'translate',
      previous: previousPairs().filter((pair) => pair.source !== text),
    });
    requestId = data.requestId;
    thread.push({ id: requestId, role: 'model', text: '', pending: true });
    paint();
  } catch (error) {
    sending = false;
    thread.push({ id: `err-${Date.now()}`, role: 'model', text: error.message, error: error.message });
    paint();
  }
}

function resetThread() {
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  thread.splice(0, thread.length);
  paint();
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
  if (!asksAllowed(model)) mode = 'translate';

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
    },
  }, codes.map((code) => el('option', { value: code, text: langLabel(code) })));
  if (codes.includes(targetLang)) targetSelect.value = targetLang;

  const modeRow = asksAllowed(model)
    ? el('div', { class: 'chat-modes', role: 'radiogroup', 'aria-label': 'Режим диалога' }, [
        modeButton('translate', 'Перевод'),
        modeButton('ask', 'Вопрос'),
      ])
    : el('p', {
        class: 'chat-note',
        text: 'Каждая реплика переводится отдельно. Свободный разговор эти модели не обещают.',
      });

  const input = el('textarea', {
    class: 'textarea chat-input',
    rows: 3,
    placeholder: mode === 'ask' ? 'Спросите модель…' : 'Фрагмент для перевода…',
    onInput: (event) => {
      draft = event.target.value;
      const send = document.querySelector('.chat-send');
      const counter = document.querySelector('.chat-counter');
      if (counter) counter.textContent = `${draft.length} / ${TEXT_LIMIT}`;
      if (send && !sending) send.disabled = !draft.trim();
    },
    onKeydown: (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
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
    },
  });
  contextInput.value = context;

  host.replaceChildren(el('div', { class: 'chat' }, [
    models.length
      ? el('div', { class: 'chat-bar' }, [
          field('Модель', modelSelect),
          mode === 'translate' ? field('Оригинал', sourceSelect) : null,
          mode === 'translate' ? field('Перевод', targetSelect) : null,
          modeRow,
        ])
      : el('div', { class: 'chat-missing' }, [
          el('p', { text: 'Чтобы начать диалог, скачайте локальную модель.' }),
          button({
            label: 'К моделям',
            variant: 'primary',
            onClick: () => router.showPage('models'),
          }),
        ]),
    el('div', { class: 'chat-log', role: 'log', 'aria-live': 'polite' }),
    models.length
      ? el('form', {
          class: 'chat-composer',
          onSubmit: (event) => {
            event.preventDefault();
            void send();
          },
        }, [
          el('details', { class: 'chat-context' }, [
            el('summary', { text: 'Контекст для тона' }),
            contextInput,
          ]),
          input,
          el('div', { class: 'chat-composer__row' }, [
            el('span', { class: 'chat-counter', text: `${draft.length} / ${TEXT_LIMIT}` }),
            el('button', {
              class: 'btn btn--primary chat-send',
              type: 'submit',
              disabled: !draft.trim() && !sending,
            }, [sending ? 'Стоп' : 'Отправить']),
          ]),
        ])
      : null,
  ]));
  paint();
}

function field(label, control) {
  return el('label', { class: 'chat-field' }, [
    el('span', { class: 'chat-field__label', text: label }),
    control,
  ]);
}

function modeButton(value, label) {
  return el('button', {
    class: `chat-mode${mode === value ? ' is-selected' : ''}`,
    type: 'button',
    onClick: () => {
      mode = value;
      const host = document.getElementById('page-host');
      if (host) render(host);
    },
  }, [label]);
}

function actions() {
  if (!thread.length) return [];
  return [button({ label: 'Новый диалог', onClick: resetThread })];
}

router.registerPage('chat', {
  title: 'Диалог',
  subtitle: 'Перевод без проекта',
  render,
  actions,
});
