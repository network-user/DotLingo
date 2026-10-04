/**
 * Страница «Перевод»: лента, полка проектов и диалогов, быстрый файл.
 */

import { call, isDemo, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { el, button, toast, confirmDialog, helpMark, closeHelpMarks } from '../components.js';
import {
  consumeDeckRequest,
  mountProjectDeck,
  releaseProjectDeck,
  requestProjectDeck,
} from './projects.js';

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
let focusKind = 'dialog';
let focusProjectId = '';
let tuneOpen = false;
/** @type {object[]} */
let projects = [];
/** @type {object[]} */
let projectDocs = [];
/** @type {Map<string, object>} */
let jobs = new Map();
let projectSaveTimer = 0;
let holdBootDialog = false;
let dialogId = '';
let requestId = '';
let sending = false;
let wired = false;
let stick = true;
let booted = false;
let incognito = false;
let listHidden = false;
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
  store.on('task_event', (payload) => {
    void onTaskEvent(payload);
  });
  store.on('open-project-deck', () => {
    const request = consumeDeckRequest();
    if (!request || router.currentPage() !== 'chat') return;
    openDeck(request);
  });
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
  const cards = focusKind === 'project' ? visibleJobs().map((job) => fileCard(job)) : [];
  if (thread.length === 0 && cards.length === 0) {
    log.replaceChildren(el('p', { class: 'chat-empty', text: emptyLead() }));
    return;
  }
  log.replaceChildren(...cards, ...thread.map((turn) => bubble(turn)));
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

function filesLabel(count) {
  const value = Math.abs(Number(count) || 0);
  const mod10 = value % 10;
  const mod100 = value % 100;
  if (mod10 === 1 && mod100 !== 11) return `${value} файл`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${value} файла`;
  return `${value} файлов`;
}

function projectMeta(project) {
  const targets = (project.targetLangs || []).map((code) => langLabel(code)).join(', ');
  const pair = `${langLabel(project.sourceLang || 'auto')} → ${targets || 'язык не задан'}`;
  const summary = project.taskSummary || {};
  let state = '';
  if (summary.running) state = 'идёт перевод';
  else if (summary.queued) state = 'в очереди';
  else if (summary.failed) state = 'есть ошибка';
  else if (summary.complete) state = 'есть готовые';
  return [pair, filesLabel(project.documentCount ?? 0), state].filter(Boolean).join(' · ');
}

function dialogRow(item) {
  const selected = focusKind === 'dialog' && item.id === dialogId;
  return el('div', { class: `chat-dialog${selected ? ' is-selected' : ''}` }, [
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
}

function projectRow(project) {
  const selected = focusKind === 'project' && project.id === focusProjectId;
  return el('div', {
    class: `chat-dialog work-row${selected ? ' is-selected' : ''}`,
    dataset: { dropProject: project.id },
  }, [
    el('button', {
      type: 'button',
      class: 'chat-dialog__open',
      onClick: () => void focusProject(project.id),
    }, [
      el('span', { class: 'chat-dialog__title', text: project.title || 'Проект' }),
      el('span', { class: 'chat-dialog__meta', text: projectMeta(project) }),
    ]),
    el('button', {
      type: 'button',
      class: 'chat-dialog__delete',
      text: 'Настроить',
      onClick: () => requestProjectDeck({ mode: 'existing', projectId: project.id }),
    }),
  ]);
}

function paintDialogs() {
  const list = document.querySelector('.chat-dialogs__list');
  if (!list) return;
  const loose = dialogs.filter((item) => !item.projectId);
  const projectNodes = projects.length
    ? projects.map((project) => projectRow(project))
    : [el('p', {
      class: 'chat-empty',
      text: 'Бросьте файл на список или создайте проект.',
    })];
  const dialogNodes = loose.length
    ? loose.map((item) => dialogRow(item))
    : [el('p', { class: 'chat-empty', text: 'Короткий текст без файла появится здесь.' })];
  list.replaceChildren(
    el('p', { class: 'work-label', text: 'Проекты' }),
    ...projectNodes,
    el('p', { class: 'work-label', text: 'Диалоги' }),
    ...dialogNodes,
  );
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
  if (holdBootDialog) {
    holdBootDialog = false;
    booted = true;
    return;
  }
  if (booted || sending || focusKind === 'project' || focusKind === 'deck') return;
  booted = true;
  const first = dialogs.find((item) => !item.projectId);
  if (first && thread.length === 0) await openDialog(first.id);
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
    projectId: focusKind === 'project' ? focusProjectId : '',
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
  if (incognito) return;
  if (!dialogId || !thread.some((turn) => turn.text && !turn.pending)) return;
  const [saved] = await tryCall('saveDialog', snapshot());
  if (!saved) return;
  const summary = {
    id: saved.id,
    title: saved.title,
    mode: saved.mode,
    updatedAt: saved.updatedAt,
    projectId: saved.projectId || '',
  };
  dialogs = [summary, ...dialogs.filter((item) => item.id !== saved.id)];
  paintDialogs();
}

async function openDialog(id) {
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  if (incognito) {
    toast('Инкогнито не открывает сохранённые диалоги. Выключите его, чтобы открыть историю.', 'info');
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
  focusKind = data.projectId ? 'project' : 'dialog';
  focusProjectId = data.projectId || '';
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

function setListHidden(hidden) {
  listHidden = hidden;
  store.set('chatListHidden', hidden);
  rememberPane({ chat_list_hidden: hidden });
  const chat = document.querySelector('.chat');
  if (!chat) return;
  chat.classList.toggle('is-list-hidden', hidden);
  const show = chat.querySelector('.chat-list-show');
  if (show) show.hidden = !hidden;
}

function setIncognito(next) {
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  incognito = next;
  const button = document.querySelector('.chat-incognito');
  if (button) {
    button.classList.toggle('is-selected', incognito);
    button.setAttribute('aria-pressed', incognito ? 'true' : 'false');
  }
  const note = document.querySelector('.chat-private');
  if (note) note.hidden = !incognito;
  if (!incognito) void persist();
}

async function startNew() {
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  await persist();
  focusKind = 'dialog';
  focusProjectId = '';
  projectDocs = [];
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
    toast(`Сообщение длиннее ${TEXT_LIMIT} знаков. Большой текст бросьте файлом.`, 'error');
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

const JOB_LABELS = {
  imported: 'В проекте',
  queued: 'В очереди',
  running: 'Перевод',
  paused: 'Пауза',
  complete: 'Собираем файл',
  ready: 'Файл готов',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  interrupted: 'Прервано',
};

function emptyLead() {
  if (focusKind === 'project') {
    return 'Бросьте файл в этот проект или напишите фрагмент. Файл вернётся в том же формате.';
  }
  if (mode === 'ask') {
    return 'Напишите сообщение. Модель ответит на этом компьютере, без сети.';
  }
  return 'Напишите фрагмент или бросьте файл. Готовый файл придёт в том же формате.';
}

function tuneText() {
  const model = currentModel();
  const modelName = model?.name || 'нет модели';
  if (focusKind === 'project') {
    const project = projects.find((item) => item.id === focusProjectId);
    const targets = (project?.targetLangs || [targetLang]).map((code) => langLabel(code)).join(', ');
    const name = project?.title || 'Проект';
    return `${name} · ${langLabel(project?.sourceLang || sourceLang)} → ${targets}`;
  }
  if (mode === 'ask') return `Общение · ${modelName}`;
  return `${langLabel(sourceLang)} → ${langLabel(targetLang)} · ${modelName}`;
}

function visibleJobs() {
  const ids = new Set(projectDocs.map((doc) => doc.id));
  return [...jobs.values()].filter((job) => ids.has(job.documentId));
}

function queueProjectSave() {
  if (focusKind !== 'project' || !focusProjectId) return;
  clearTimeout(projectSaveTimer);
  projectSaveTimer = window.setTimeout(() => void saveProjectPair(), 300);
}

async function saveProjectPair() {
  const project = projects.find((item) => item.id === focusProjectId) || store.get('activeProject');
  if (!project || project.id !== focusProjectId) return;
  const existing = project.targetLangs || [];
  const targets = existing.length > 1
    ? [targetLang, ...existing.filter((code) => code !== targetLang)]
    : [targetLang];
  const [updated, error] = await tryCall('updateProjectSettings', {
    modelId: modelId || project.modelId,
    sourceLang,
    targetLangs: targets.filter(Boolean),
    context: project.context || '',
    rules: project.rules || '',
  });
  if (error || !updated) {
    toast(error?.message || 'Настройки проекта не сохранились.', 'error');
    return;
  }
  store.set('activeProject', updated);
  projects = projects.map((item) => (item.id === updated.id ? { ...item, ...updated } : item));
  const toggle = document.querySelector('.work-tune-toggle');
  if (toggle) toggle.textContent = tuneText();
  paintDialogs();
}

async function refreshProjects() {
  const [rows] = await tryCall('listProjects');
  projects = Array.isArray(rows) ? rows : [];
  store.set('projects', projects);
  paintDialogs();
}

function pdfJobNote(doc) {
  const warnings = Array.isArray(doc?.warnings) ? doc.warnings : [];
  if (warnings.some((item) => String(item).includes('вернётся PDF'))) return 'Вернётся PDF.';
  if (doc?.format === 'pdf') return 'Вернётся как Markdown.';
  return '';
}

async function refreshProjectDocs() {
  if (focusKind !== 'project' || !focusProjectId) {
    projectDocs = [];
    return;
  }
  const [docs] = await tryCall('listDocuments');
  const [tasks] = await tryCall('listTasks');
  projectDocs = Array.isArray(docs) ? docs : [];
  const mine = (Array.isArray(tasks) ? tasks : []).filter((task) => task.projectId === focusProjectId);
  const next = new Map();
  for (const doc of projectDocs) {
    const related = mine.filter((task) => task.documentId === doc.id);
    if (!related.length) {
      const key = `${doc.id}:`;
      next.set(key, {
        ...(jobs.get(key) || {}),
        key,
        documentId: doc.id,
        name: doc.name,
        format: doc.format,
        targetLang: '',
        status: 'imported',
        note: (jobs.get(key) || {}).note || pdfJobNote(doc),
      });
      continue;
    }
    for (const task of related) {
      const key = `${doc.id}:${task.targetLang}`;
      const prev = jobs.get(key) || {};
      const status = prev.status === 'ready' && task.status === 'complete' ? 'ready' : (task.status || 'queued');
      next.set(key, {
        ...prev,
        key,
        documentId: doc.id,
        name: doc.name || task.documentName,
        format: doc.format,
        note: prev.note || pdfJobNote(doc),
        targetLang: task.targetLang,
        taskId: task.taskId,
        status,
        completed: task.completed || 0,
        total: task.total || 0,
        error: task.error || prev.error || '',
      });
    }
  }
  jobs = next;
  const pending = [...jobs.values()].filter(
    (job) => job.status === 'complete' && job.targetLang && !job.path && !job.publishing,
  );
  await Promise.all(pending.map(async (job) => {
    job.publishing = true;
    await publishJob(job, focusProjectId);
  }));
}

async function publishJob(job, projectId) {
  const [data, error] = await tryCall(
    'publishTranslation',
    job.documentId,
    job.targetLang,
    projectId || '',
  );
  job.publishing = false;
  if (error || !data?.path) {
    job.status = 'failed';
    job.error = error?.message || 'Файл не собрался.';
    return;
  }
  job.status = 'ready';
  job.path = data.path;
  job.note = data.note || '';
}

async function onTaskEvent(payload) {
  const event = payload?.task;
  const taskId = event?.task_id;
  if (!taskId) return;
  const job = [...jobs.values()].find((item) => item.taskId === taskId);
  if (!job) return;
  if (event.status) job.status = event.status;
  if (event.completed != null) job.completed = event.completed;
  if (event.total != null) job.total = event.total;
  if (event.status === 'failed') job.error = event.message || job.error || 'Перевод остановился.';
  if (event.status === 'complete' && !job.publishing && !job.path) {
    job.publishing = true;
    await publishJob(job, payload.projectId || focusProjectId);
  }
  paintLog();
}

async function focusProject(id) {
  if (!id) return;
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  if (focusKind === 'deck') releaseProjectDeck();
  await persist();
  const [project, error] = await tryCall('openProject', id);
  if (error || !project) {
    toast(error?.message || 'Проект не открылся.', 'error');
    return;
  }
  store.set('activeProject', project);
  focusKind = 'project';
  focusProjectId = project.id;
  sourceLang = project.sourceLang || 'auto';
  targetLang = (project.targetLangs || [])[0] || targetLang;
  if (project.modelId) modelId = project.modelId;
  const bound = dialogs.find((item) => item.projectId === project.id);
  if (bound) {
    const [data, loadError] = await tryCall('loadDialog', bound.id);
    if (!loadError && data) {
      dialogId = data.id;
      mode = data.mode === 'ask' ? 'ask' : 'translate';
      context = data.context || '';
      thread = (data.messages || []).map((turn) => ({
        id: turn.id || newId(),
        role: turn.role,
        text: turn.text || '',
        error: turn.error || '',
      }));
    } else {
      dialogId = newId();
      thread = [];
    }
  } else {
    dialogId = newId();
    thread = [];
  }
  attachments = [];
  draft = '';
  stick = true;
  await refreshProjectDocs();
  const host = document.getElementById('page-host');
  if (host) render(host);
}

function waitImport(paths) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const stop = store.on('documents_imported', (payload) => {
      if (settled) return;
      settled = true;
      stop();
      resolve(payload || {});
    });
    call('importDocuments', paths).catch((error) => {
      if (settled) return;
      settled = true;
      stop();
      reject(error);
    });
  });
}

async function startFiles(paths, explicitProjectId) {
  if (isDemo()) {
    toast('Перевод файла запускается в окне приложения.', 'info');
    return;
  }
  if (sending) {
    toast('Сначала остановите ответ.', 'info');
    return;
  }
  const list = [...paths].filter(Boolean);
  if (!list.length) return;
  const model = currentModel();
  if (!model) {
    toast('Сначала скачайте модель.', 'error');
    router.showPage('models');
    return;
  }
  const codes = model.languageCodes || [];
  const target = codes.includes(targetLang) ? targetLang : (codes.includes('ru') ? 'ru' : codes[0]);
  if (!target) {
    toast('У модели нет языка перевода.', 'error');
    return;
  }
  targetLang = target;
  const projectId = explicitProjectId || (focusKind === 'project' ? focusProjectId : '');
  let project;
  let error;
  if (projectId) {
    [project, error] = await tryCall('openProject', projectId);
  } else {
    [project, error] = await tryCall('ensureWorkProject', {
      modelId: model.id,
      sourceLang,
      targetLangs: [target],
    });
  }
  if (error || !project) {
    toast(error?.message || 'Проект не открылся.', 'error');
    return;
  }
  if (!project.modelId || !(project.targetLangs || []).length) {
    const [updated, updateError] = await tryCall('updateProjectSettings', {
      modelId: project.modelId || model.id,
      sourceLang: project.sourceLang || sourceLang || 'auto',
      targetLangs: (project.targetLangs || []).length ? project.targetLangs : [target],
      context: project.context || '',
      rules: project.rules || '',
    });
    if (updateError || !updated) {
      toast(updateError?.message || 'Не удалось записать языки проекта.', 'error');
      return;
    }
    project = updated;
  }
  store.set('activeProject', project);
  let payload;
  try {
    payload = await waitImport(list);
  } catch (importError) {
    toast(importError.message || 'Импорт не начался.', 'error');
    return;
  }
  const imported = payload?.imported || [];
  (payload?.errors || []).forEach((item) => toast(`${item.name}: ${item.error}`, 'error', 6000));
  if (!imported.length) {
    if (!(payload?.errors || []).length) toast('Файлы не импортированы.', 'error');
    await focusProject(project.id);
    return;
  }
  const targets = (project.targetLangs || []).length ? project.targetLangs : [target];
  const [queued, queueError] = await tryCall('enqueueTranslation', {
    documentIds: imported.map((item) => item.id),
    targetLangs: targets,
  });
  if (queueError || !queued) {
    toast(queueError?.message || 'Перевод не поставлен в очередь.', 'error', 6000);
    await focusProject(project.id);
    return;
  }
  (queued.warnings || []).forEach((item) => toast(`${item.document}: ${item.text}`, 'info', 6000));
  toast('Файл в очереди. Готовый документ появится в ленте.', 'success');
  await focusProject(project.id);
}

async function receiveFiles(fileList, projectId) {
  const files = [...fileList];
  if (!files.length) {
    toast('В переносе нет файла.', 'info');
    return;
  }
  const paths = [];
  for (const file of files) {
    const [found, error] = await tryCall('claimDroppedFile', file.name);
    if (error) {
      toast(error.message, 'error');
      continue;
    }
    if (found) paths.push(found);
    else toast(`Не вижу путь к «${file.name}». Выберите его кнопкой «Файл».`, 'error');
  }
  if (paths.length) await startFiles(paths, projectId || '');
}

async function pickFiles(projectId) {
  const [picked, error] = await tryCall('resolveImportPaths');
  if (error) {
    toast(error.message, 'error');
    return;
  }
  const paths = Array.isArray(picked) ? picked.filter(Boolean) : [];
  if (paths.length) await startFiles(paths, projectId || '');
}

async function translateDocument(documentId) {
  const project = store.get('activeProject');
  const targets = project?.targetLangs || [];
  if (!targets.length) {
    toast('Сначала выберите язык перевода.', 'error');
    return;
  }
  const [queued, error] = await tryCall('enqueueTranslation', {
    documentIds: [documentId],
    targetLangs: targets,
  });
  if (error || !queued) {
    toast(error?.message || 'Перевод не поставлен в очередь.', 'error', 6000);
    return;
  }
  toast('Файл в очереди.', 'success');
  await refreshProjectDocs();
  paintLog();
}

async function stopJob(job) {
  if (!job?.taskId) return;
  const [, error] = await tryCall('cancelTask', job.taskId);
  if (error) toast(error.message, 'error');
}

function fileCard(job) {
  const label = JOB_LABELS[job.status] || job.status || 'Файл';
  const progress = job.total > 0 ? `${job.completed}/${job.total}` : '';
  const pair = job.targetLang ? ` → ${langLabel(job.targetLang)}` : '';
  const width = job.total > 0 ? Math.min(100, Math.round((job.completed / job.total) * 100)) : 0;
  const actions = [];
  if (job.status === 'imported') {
    actions.push(button({
      label: 'Перевести',
      size: 'sm',
      variant: 'primary',
      onClick: () => void translateDocument(job.documentId),
    }));
  }
  if (job.taskId && (job.status === 'running' || job.status === 'queued' || job.status === 'paused')) {
    actions.push(button({ label: 'Стоп', size: 'sm', onClick: () => void stopJob(job) }));
  }
  if (job.path) {
    actions.push(button({
      label: 'Показать',
      size: 'sm',
      variant: 'primary',
      onClick: () => void call('revealPath', job.path).catch((error) => toast(error.message, 'error')),
    }));
  } else if (job.status === 'failed' || job.status === 'complete') {
    actions.push(button({
      label: 'Собрать файл',
      size: 'sm',
      onClick: () => void publishJob(job, focusProjectId).then(() => paintLog()),
    }));
  }
  if (focusKind === 'project') {
    actions.push(button({ label: 'Проверка', size: 'sm', onClick: () => router.showPage('review') }));
  }
  return el('article', { class: 'work-file', dataset: { job: job.key } }, [
    el('p', { class: 'work-file__name', text: `${job.name || 'Файл'}${pair}` }),
    el('p', {
      class: 'work-file__meta',
      text: [label, progress, job.note, job.error].filter(Boolean).join(' · '),
    }),
    job.total > 0
      ? el('div', { class: 'work-file__track', ariaHidden: 'true' }, [
        el('div', { class: 'work-file__fill', style: { width: `${width}%` } }),
      ])
      : null,
    actions.length ? el('div', { class: 'work-file__actions' }, actions) : null,
  ]);
}

function bindDrop(node, projectFromNode) {
  if (!(node instanceof HTMLElement) || node.dataset.dropBound === '1') return;
  node.dataset.dropBound = '1';
  const clear = () => {
    node.classList.remove('is-drop');
    node.querySelectorAll('.is-drop').forEach((item) => item.classList.remove('is-drop'));
  };
  const arm = (event) => {
    if (!event.dataTransfer) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    clear();
    const row = event.target?.closest?.('[data-drop-project]');
    (row || node).classList.add('is-drop');
  };
  node.addEventListener('dragenter', arm);
  node.addEventListener('dragover', arm);
  node.addEventListener('dragleave', (event) => {
    if (node.contains(event.relatedTarget)) return;
    clear();
  });
  node.addEventListener('drop', (event) => {
    event.preventDefault();
    event.stopPropagation();
    clear();
    const row = event.target?.closest?.('[data-drop-project]');
    const projectId = row?.dataset.dropProject || projectFromNode() || '';
    void receiveFiles(event.dataTransfer?.files || [], projectId);
  });
}

function openDeck(request) {
  const deck = document.querySelector('.chat-deck');
  const work = document.querySelector('.chat-work');
  if (!deck || !work) return;
  focusKind = 'deck';
  work.hidden = true;
  deck.hidden = false;
  mountProjectDeck(deck, request, {
    onSaved(project) {
      releaseProjectDeck();
      store.set('activeProject', project);
      focusKind = 'project';
      focusProjectId = project?.id || '';
      sourceLang = project?.sourceLang || sourceLang;
      targetLang = project?.targetLangs?.[0] || targetLang;
      if (project?.modelId) modelId = project.modelId;
      dialogId = newId();
      thread = [];
      attachments = [];
      draft = '';
      const host = document.getElementById('page-host');
      if (host) render(host);
      void refreshProjects();
      void refreshProjectDocs().then(() => paintLog());
    },
    onCancel() {
      releaseProjectDeck();
      focusKind = focusProjectId ? 'project' : 'dialog';
      const workNode = document.querySelector('.chat-work');
      const deckNode = document.querySelector('.chat-deck');
      if (workNode) workNode.hidden = false;
      if (deckNode) {
        deckNode.hidden = true;
        deckNode.replaceChildren();
      }
    },
  });
}

function render(host) {
  closeHelpMarks();
  ensureWire();
  const deckRequest = consumeDeckRequest();
  const pendingFocus = window.DL?.focusProjectId || '';
  if (window.DL) window.DL.focusProjectId = '';
  if (deckRequest || pendingFocus) holdBootDialog = true;
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
  listHidden = Boolean(store.get('chatListHidden'));

  const modelSelect = el('select', {
    class: 'select',
    onChange: (event) => {
      modelId = event.target.value;
      if (focusKind === 'project') queueProjectSave();
      render(host);
    },
  }, models.map((item) => el('option', { value: item.id, text: item.name })));
  modelSelect.value = modelId;

  const sourceSelect = el('select', {
    class: 'select',
    onChange: (event) => {
      sourceLang = event.target.value;
      if (focusKind === 'project') queueProjectSave();
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
      if (focusKind === 'project') queueProjectSave();
      void refreshMeter();
    },
  }, codes.map((code) => el('option', { value: code, text: langLabel(code) })));
  if (codes.includes(targetLang)) targetSelect.value = targetLang;

  const input = el('textarea', {
    class: 'textarea chat-input',
    rows: 3,
    title: 'Enter отправляет, Shift+Enter переносит строку',
    placeholder: focusKind === 'project'
      ? 'Фрагмент для этого проекта. Файл можно бросить сюда.'
      : (mode === 'ask' ? 'Сообщение…' : 'Фрагмент или бросьте файл…'),
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

  const chat = el('div', {
    class: `chat${listHidden ? ' is-list-hidden' : ''}`,
    dataset: { stack: 'col' },
  }, [
    el('aside', { class: 'chat-dialogs', ariaLabel: 'Проекты и диалоги' }, [
      el('div', { class: 'chat-dialogs__head' }, [
        el('p', { class: 'chat-dialogs__label', text: 'Перевод' }),
        el('div', { class: 'chat-dialogs__actions' }, [
          button({
            label: 'Скрыть',
            size: 'sm',
            title: 'Скрыть список и отдать место ленте',
            onClick: () => setListHidden(true),
          }),
          button({ label: 'Диалог', size: 'sm', title: 'Новый диалог без файла', onClick: () => void startNew() }),
          button({
            label: 'Проект',
            size: 'sm',
            title: 'Новый проект: название, языки, модель',
            onClick: () => requestProjectDeck({ mode: 'new' }),
          }),
        ]),
      ]),
      el('div', { class: 'chat-dialogs__list' }),
    ]),
    el('div', {
      class: 'chat-split',
      role: 'separator',
      ariaOrientation: 'vertical',
      ariaLabel: 'Ширина списка',
      tabIndex: 0,
    }),
    el('div', { class: 'chat-main' }, [
      el('div', { class: 'chat-deck', hidden: true }),
      el('div', { class: 'chat-work' }, [
      el('div', { class: 'chat-bar' }, [
        el('button', {
          type: 'button',
          class: 'btn btn--sm chat-list-show',
          hidden: !listHidden,
          title: 'Показать проекты и диалоги',
          text: 'Список',
          onClick: () => setListHidden(false),
        }),
        el('button', {
          type: 'button',
          class: 'work-tune-toggle',
          ariaExpanded: tuneOpen ? 'true' : 'false',
          title: 'Языки, модель и редкие настройки',
          text: tuneText(),
          onClick: () => {
            tuneOpen = !tuneOpen;
            const panel = document.querySelector('.work-tune');
            const toggle = document.querySelector('.work-tune-toggle');
            if (panel) panel.hidden = !tuneOpen;
            if (toggle) toggle.setAttribute('aria-expanded', tuneOpen ? 'true' : 'false');
          },
        }),
        el('div', { class: 'work-tune', hidden: !tuneOpen }, [
          el('button', {
            type: 'button',
            class: `btn btn--sm chat-incognito${incognito ? ' is-selected' : ''}`,
            ariaPressed: incognito ? 'true' : 'false',
            title: 'Не записывать этот диалог на диск',
            text: 'Инкогнито',
            onClick: () => setIncognito(!incognito),
          }),
          el('div', { class: 'chat-mode-wrap' }, [
            el('div', { class: 'chat-modes', role: 'radiogroup', ariaLabel: 'Режим' }, [
              modeButton('translate', 'Перевод', host),
              modeButton('ask', 'Общение', host),
            ]),
            helpMark(
              '«Перевод» берёт фрагмент и пару языков. «Общение» отвечает на сообщение. Модель, обученная переводить, может просто пересказать фразу.',
            ),
          ]),
          models.length
            ? field('Модель', modelSelect)
            : el('div', { class: 'chat-missing' }, [
                el('p', { text: 'Чтобы переводить, скачайте локальную модель.' }),
                button({
                  label: 'К моделям',
                  variant: 'primary',
                  onClick: () => router.showPage('models'),
                }),
              ]),
          mode === 'translate' && models.length
            ? field(
              'Оригинал',
              sourceSelect,
              '«Авто» не называет язык оригинала. Если он известен, выберите его в списке.',
            )
            : null,
          mode === 'translate' && models.length ? field('Перевод', targetSelect) : null,
          focusKind === 'project'
            ? button({
              label: 'Настроить проект',
              size: 'sm',
              onClick: () => requestProjectDeck({ mode: 'existing', projectId: focusProjectId }),
            })
            : null,
          focusKind === 'project'
            ? button({ label: 'Документы', size: 'sm', onClick: () => router.showPage('documents') })
            : null,
          focusKind === 'project'
            ? button({ label: 'Проверка', size: 'sm', onClick: () => router.showPage('review') })
            : null,
          button({
            label: 'Как текст',
            size: 'sm',
            title: 'Короткая выдержка из файла в сообщение, без сборки документа',
            onClick: () => void attachFiles(),
          }),
          hyNote,
        ]),
      ]),
      el('p', {
        class: 'chat-private',
        hidden: !incognito,
        text: 'Этот диалог не сохранится.',
      }),
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
              el('div', { class: 'chat-meter-line' }, [
                el('p', { class: 'chat-meter', text: meterNote }),
                helpMark('Оценка, сколько контекста модели уже занято. Это не ход перевода.'),
              ]),
            ]),
            el('details', { class: 'chat-context' }, [
              el('summary', {}, [
                'Контекст для тона',
                helpMark('Необязательно. Предмет, тон и имена уходят в запрос вместе с текстом, до 800 знаков.'),
              ]),
              contextInput,
            ]),
            el('div', { class: 'chat-files' }),
            input,
            el('div', { class: 'chat-composer__row' }, [
              el('span', { class: 'chat-counter', text: `${draft.length} / ${TEXT_LIMIT}` }),
              el('div', { class: 'chat-composer__actions' }, [
                button({
                  label: 'Файл',
                  title: 'Перевести файл и вернуть его в том же формате',
                  onClick: () => void pickFiles(focusKind === 'project' ? focusProjectId : ''),
                }),
                el('button', {
                  class: 'btn btn--primary chat-send',
                  type: 'submit',
                  disabled: !draft.trim() && attachments.length === 0 && !sending,
                }, [sending ? 'Стоп' : 'Отправить']),
              ]),
            ]),
          ]),
      ]),
    ]),
  ]);
  host.replaceChildren(chat);
  bindChatSplit(chat);
  const list = chat.querySelector('.chat-dialogs__list');
  const work = chat.querySelector('.chat-work');
  if (list) bindDrop(list, () => '');
  if (work) {
    bindDrop(work, () => (focusKind === 'project' ? focusProjectId : ''));
  }
  paintDialogs();
  paintLog();
  paintFiles();
  void refreshProjects();
  void loadDialogs();
  void refreshMeter();
  if (deckRequest) openDeck(deckRequest);
  else if (pendingFocus) void focusProject(pendingFocus);
}

const LIST_MIN = 168;
const LIST_HEIGHT_MIN = 120;
const MAIN_MIN = 300;
const STACK_AT = 520;
let splitWatch = null;

function paneNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function rememberPane(patch) {
  void call('setPreferences', patch).catch(() => {});
}

function bindChatSplit(chat) {
  splitWatch?.();
  const handle = chat.querySelector('.chat-split');
  const pane = chat.querySelector('.chat-dialogs');
  if (!(handle instanceof HTMLElement) || !(pane instanceof HTMLElement)) return;

  const stacked = () => chat.dataset.stack === 'row';
  let drag = null;

  const place = () => {
    if (drag) return;
    const narrow = chat.clientWidth > 0 && chat.clientWidth < STACK_AT;
    const next = narrow ? 'row' : 'col';
    if (chat.dataset.stack !== next) chat.dataset.stack = next;
    const vertical = stacked();
    const total = vertical ? chat.clientHeight : chat.clientWidth;
    if (total <= 0) return;
    const min = vertical ? LIST_HEIGHT_MIN : LIST_MIN;
    const reserve = vertical ? 240 : MAIN_MIN;
    const max = Math.max(min, total - reserve - 10);
    const raw = vertical
      ? paneNumber(store.get('chatListHeight'), 200)
      : paneNumber(store.get('chatListWidth'), 240);
    const size = Math.round(Math.min(max, Math.max(min, raw)));
    chat.style.setProperty(vertical ? '--chat-list-h' : '--chat-list', `${size}px`);
    handle.ariaOrientation = vertical ? 'horizontal' : 'vertical';
    handle.ariaValueMin = String(min);
    handle.ariaValueMax = String(Math.round(max));
    handle.ariaValueNow = String(size);
    const label = vertical ? 'Высота списка' : 'Ширина списка';
    handle.setAttribute('aria-label', label);
    handle.title = vertical
      ? 'Потяните, чтобы изменить высоту. Двойной щелчок вернёт размер.'
      : 'Потяните, чтобы изменить ширину. Двойной щелчок вернёт размер.';
  };

  place();
  const observer = new ResizeObserver(place);
  observer.observe(chat);
  splitWatch = () => {
    observer.disconnect();
    splitWatch = null;
  };
  const stopStore = store.subscribe((key) => {
    if (key === 'chatListWidth' || key === 'chatListHeight') place();
  });
  const previous = splitWatch;
  splitWatch = () => {
    stopStore();
    previous?.();
  };

  const finish = () => {
    if (!drag) return;
    const vertical = drag.vertical;
    const size = Number(handle.ariaValueNow);
    drag = null;
    handle.classList.remove('is-dragging');
    document.body.classList.remove('is-resizing', 'is-resizing-row');
    if (!Number.isFinite(size)) return;
    if (vertical) {
      store.set('chatListHeight', size);
      rememberPane({ chat_list_height: size });
    } else {
      store.set('chatListWidth', size);
      rememberPane({ chat_list_width: size });
    }
  };

  const onPointerMove = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const delta = (drag.vertical ? event.clientY : event.clientX) - drag.start;
    const total = drag.vertical ? chat.clientHeight : chat.clientWidth;
    const min = drag.vertical ? LIST_HEIGHT_MIN : LIST_MIN;
    const reserve = drag.vertical ? 240 : MAIN_MIN;
    const max = Math.max(min, total - reserve - 10);
    const size = Math.round(Math.min(max, Math.max(min, drag.size + delta)));
    chat.style.setProperty(drag.vertical ? '--chat-list-h' : '--chat-list', `${size}px`);
    handle.ariaValueNow = String(size);
  };

  const endDrag = (event) => {
    if (event && drag && event.pointerId !== drag.pointerId) return;
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', endDrag);
    window.removeEventListener('pointercancel', endDrag);
    finish();
  };

  handle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || drag) return;
    event.preventDefault();
    const vertical = stacked();
    drag = {
      pointerId: event.pointerId,
      vertical,
      start: vertical ? event.clientY : event.clientX,
      size: vertical ? pane.getBoundingClientRect().height : pane.getBoundingClientRect().width,
    };
    handle.classList.add('is-dragging');
    document.body.classList.add('is-resizing');
    document.body.classList.toggle('is-resizing-row', vertical);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
    try {
      handle.setPointerCapture(event.pointerId);
    } catch {
      // Синтетический указатель не захватывается. Слушатели на окне всё равно ведут жест.
    }
  });
  handle.addEventListener('dblclick', () => {
    const vertical = stacked();
    if (vertical) {
      store.set('chatListHeight', 200);
      rememberPane({ chat_list_height: 200 });
    } else {
      store.set('chatListWidth', 240);
      rememberPane({ chat_list_width: 240 });
    }
  });
  handle.addEventListener('keydown', (event) => {
    const vertical = stacked();
    const grow = vertical ? event.key === 'ArrowDown' : event.key === 'ArrowRight';
    const shrink = vertical ? event.key === 'ArrowUp' : event.key === 'ArrowLeft';
    if (!grow && !shrink) return;
    event.preventDefault();
    const current = vertical ? pane.getBoundingClientRect().height : pane.getBoundingClientRect().width;
    const min = vertical ? LIST_HEIGHT_MIN : LIST_MIN;
    const total = vertical ? chat.clientHeight : chat.clientWidth;
    const reserve = vertical ? 240 : MAIN_MIN;
    const max = Math.max(min, total - reserve - 10);
    const size = Math.round(Math.min(max, Math.max(min, current + (grow ? 24 : -24))));
    chat.style.setProperty(vertical ? '--chat-list-h' : '--chat-list', `${size}px`);
    handle.ariaValueNow = String(size);
    if (vertical) {
      store.set('chatListHeight', size);
      rememberPane({ chat_list_height: size });
    } else {
      store.set('chatListWidth', size);
      rememberPane({ chat_list_width: size });
    }
  });
}

function field(label, control, help) {
  return el('label', { class: 'chat-field' }, [
    el('span', { class: 'chat-field__label chat-field__label--with-help' }, [
      label,
      help ? helpMark(help) : null,
    ]),
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

function destroy() {
  releaseProjectDeck();
}

window.addEventListener('dl-focus-project', (event) => {
  const id = String(event.detail || '');
  if (!id || router.currentPage() !== 'chat') return;
  void focusProject(id);
});

router.registerPage('chat', {
  title: 'Перевод',
  subtitle: 'Текст, файл или проект',
  help: 'Напишите фрагмент или бросьте файл. Файл без выбранного проекта попадает в «Быстрые» и возвращается в том же формате. Книжный PDF возвращается PDF: заменяется текст, картинки остаются. Прочий PDF приходит как Markdown. Языки и модель открываются строкой под полем. Здесь лимит фрагмента '
    + `${TEXT_LIMIT} знаков.`,
  layout: 'chat',
  render,
  destroy,
});
