/**
 * Страница «Перевод»: список задач, файл с быстрыми настройками и короткий текст.
 */

import { assembleResult, resultFileActions } from './results.js';

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
let focusKind = 'work';
let focusProjectId = '';
let dialogProjectId = '';
let surface = 'file';
/** @type {object[]} */
let tasks = [];
let selectedKey = '';
/** @type {{paths?: string[], names: string[], repeat: object|null}|null} */
let staged = null;
let destinationProjectId = '';
let outputSuffix = '';
let useGlossary = true;
let taskQuery = '';
let stageKey = '';
let quickReady = false;
let quickTouched = false;
let committing = false;
let rememberTimer = 0;
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
  store.subscribe((key) => {
    if (key !== 'translateReady') return;
    if (!takeQuickMemory()) return;
    const host = document.getElementById('page-host');
    if (host && router.currentPage() === 'chat' && focusKind !== 'deck') render(host);
  });
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
  if (thread.length === 0) {
    log.replaceChildren(el('p', { class: 'chat-empty', text: emptyLead() }));
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
  const selected = surface === 'text' && item.id === dialogId;
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
  paintList();
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
  holdBootDialog = false;
  booted = true;
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
    projectId: dialogProjectId,
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
  focusKind = 'work';
  dialogProjectId = data.projectId || '';
  surface = 'text';
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
  focusKind = 'work';
  dialogProjectId = '';
  surface = 'text';
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
  if (mode === 'ask') return 'Напишите сообщение. Модель ответит на этом компьютере, без сети.';
  return 'Напишите фрагмент. Файл удобнее перевести в режиме «Файл».';
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
  paintProjectChoices();
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
  const task = tasks.find((item) => item.taskId === taskId);
  if (!task) {
    await refreshTasks();
    return;
  }
  if (event.status) task.status = event.status;
  if (event.completed != null) task.completed = event.completed;
  if (event.total != null) task.total = event.total;
  if (event.status === 'failed') task.error = event.message || task.error || 'Перевод остановился.';
  if (payload.exportPath) {
    const file = { path: payload.exportPath, format: '', createdAt: '', exportId: '', note: '' };
    task.files = [file, ...(task.files || []).filter((item) => item.path !== payload.exportPath)];
    if (event.status === 'complete') task.status = 'complete';
  } else if (payload.exportError) {
    task.error = payload.exportError;
  }
  paintList();
  if (surface === 'file') paintStage();
}

async function focusProject(id) {
  if (!id) return;
  if (focusKind === 'deck') releaseProjectDeck();
  const [project, error] = await tryCall('openProject', id);
  if (error || !project) {
    toast(error?.message || 'Проект не открылся.', 'error');
    return;
  }
  store.set('activeProject', project);
  destinationProjectId = project.title === 'Быстрые' ? '' : project.id;
  focusProjectId = project.id;
  focusKind = 'work';
  surface = 'file';
  if (project.sourceLang) sourceLang = project.sourceLang;
  if ((project.targetLangs || [])[0]) targetLang = project.targetLangs[0];
  if (project.modelId) modelId = project.modelId;
  stageKey = '';
  const host = document.getElementById('page-host');
  if (host && router.currentPage() === 'chat') render(host);
  else if (window.DL) window.DL.focusProjectId = id;
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
  const projectId = explicitProjectId || '';
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
  const [updated, updateError] = await tryCall('updateProjectSettings', {
    modelId: model.id,
    sourceLang: sourceLang || 'auto',
    targetLangs: [target],
    context: project.context || '',
    rules: project.rules || '',
  });
  if (updateError || !updated) {
    toast(updateError?.message || 'Не удалось записать языки проекта.', 'error');
    return;
  }
  project = updated;
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
    return;
  }
  const [queued, queueError] = await tryCall('enqueueTranslation', {
    documentIds: imported.map((item) => item.id),
    targetLangs: [target],
    outputSuffix,
    useGlossary,
    context: context.slice(0, 800),
  });
  if (queueError || !queued) {
    toast(queueError?.message || 'Перевод не поставлен в очередь.', 'error', 6000);
    return;
  }
  (queued.warnings || []).forEach((item) => toast(`${item.document}: ${item.text}`, 'info', 6000));
  toast('Файл в очереди.', 'success');
  staged = null;
  selectedKey = (queued.taskIds || [])[0] || '';
  stageKey = '';
  surface = 'file';
  await refreshTasks();
}

async function startExisting(repeat) {
  if (!repeat?.documentId || !repeat.projectId) return;
  if (isDemo()) {
    toast('Перевод файла запускается в окне приложения.', 'info');
    return;
  }
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
  const [project, error] = await tryCall('openProject', repeat.projectId);
  if (error || !project) {
    toast(error?.message || 'Проект не открылся.', 'error');
    return;
  }
  const [updated, updateError] = await tryCall('updateProjectSettings', {
    modelId: model.id,
    sourceLang: sourceLang || 'auto',
    targetLangs: [target],
    context: project.context || '',
    rules: project.rules || '',
  });
  if (updateError || !updated) {
    toast(updateError?.message || 'Не удалось записать языки проекта.', 'error');
    return;
  }
  store.set('activeProject', updated);
  const [queued, queueError] = await tryCall('enqueueTranslation', {
    documentIds: [repeat.documentId],
    targetLangs: [target],
    outputSuffix,
    useGlossary,
    context: context.slice(0, 800),
  });
  if (queueError || !queued) {
    toast(queueError?.message || 'Перевод не поставлен в очередь.', 'error', 6000);
    return;
  }
  toast('Файл в очереди.', 'success');
  staged = null;
  selectedKey = (queued.taskIds || [])[0] || '';
  stageKey = '';
  await refreshTasks();
}

function fileNameOf(path) {
  const parts = String(path || '').split(/[\\/]/);
  return parts[parts.length - 1] || String(path || 'Файл');
}

function stagePaths(paths) {
  const list = [...paths].filter(Boolean);
  if (!list.length) return;
  quickTouched = true;
  staged = { paths: list, names: list.map(fileNameOf), repeat: null };
  selectedKey = '';
  surface = 'file';
  stageKey = '';
  const host = document.getElementById('page-host');
  if (!document.querySelector('.chat-stage-host')) {
    if (host) render(host);
    return;
  }
  paintList();
  paintStage();
}

async function receiveFiles(fileList) {
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
  if (paths.length) stagePaths(paths);
}

async function pickFiles() {
  const [picked, error] = await tryCall('resolveImportPaths');
  if (error) {
    toast(error.message, 'error');
    return;
  }
  const paths = Array.isArray(picked) ? picked.filter(Boolean) : [];
  if (paths.length) stagePaths(paths);
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

async function resumeJob(job) {
  if (!job?.taskId) return;
  const [, error] = await tryCall('resumeTask', job.taskId);
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
    node.classList.add('is-drop');
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
    void receiveFiles(event.dataTransfer?.files || []);
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
      focusKind = 'work';
      focusProjectId = project?.id || '';
      destinationProjectId = project?.title === 'Быстрые' ? '' : (project?.id || '');
      surface = 'file';
      if (project?.sourceLang) sourceLang = project.sourceLang;
      if (project?.targetLangs?.[0]) targetLang = project.targetLangs[0];
      if (project?.modelId) modelId = project.modelId;
      stageKey = '';
      const host = document.getElementById('page-host');
      if (host) render(host);
      void refreshProjects();
    },
    onCancel() {
      releaseProjectDeck();
      focusKind = 'work';
      stageKey = '';
      const workNode = document.querySelector('.chat-work');
      const deckNode = document.querySelector('.chat-deck');
      if (workNode) workNode.hidden = false;
      if (deckNode) {
        deckNode.hidden = true;
        deckNode.replaceChildren();
      }
      paintStage();
    },
  });
}

const STATUS_LABELS = {
  queued: 'В очереди',
  running: 'Идёт',
  paused: 'Пауза',
  complete: 'Готово',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  interrupted: 'Прервано',
  saved: 'Файл',
};

const FORMAT_BY_EXT = {
  txt: ['.txt', '.md'],
  md: ['.md', '.txt'],
  markdown: ['.md', '.txt'],
  docx: ['.docx', '.txt', '.md'],
  epub: ['.epub', '.txt', '.md'],
  pdf: ['.md', '.txt', '.pdf'],
};

const FORMAT_LABEL = {
  '.txt': 'Текст',
  '.md': 'Markdown',
  '.docx': 'DOCX',
  '.epub': 'EPUB',
  '.pdf': 'PDF',
};

function takeQuickMemory() {
  if (quickReady || !store.get('translateReady')) return false;
  quickReady = true;
  if (quickTouched) return false;
  const source = store.get('translateSource');
  const target = store.get('translateTarget');
  const suffix = store.get('translateSuffix');
  const savedModel = store.get('translateModel');
  const tone = store.get('translateContext');
  const glossary = store.get('translateGlossary');
  const savedSurface = store.get('translateSurface');
  if (source) sourceLang = source;
  if (target) targetLang = target;
  if (typeof suffix === 'string') outputSuffix = suffix;
  if (savedModel) modelId = savedModel;
  if (typeof tone === 'string') context = tone;
  if (typeof glossary === 'boolean') useGlossary = glossary;
  if (savedSurface === 'text' || savedSurface === 'file') surface = savedSurface;
  return true;
}

function rememberQuick() {
  clearTimeout(rememberTimer);
  rememberTimer = window.setTimeout(() => {
    void call('setPreferences', {
      translate_source: sourceLang,
      translate_target: targetLang,
      translate_suffix: outputSuffix,
      translate_model: modelId,
      translate_context: context.slice(0, 800),
      translate_glossary: useGlossary,
      translate_surface: surface,
    }).catch(() => {});
  }, 200);
}

function touchQuick() {
  quickTouched = true;
  rememberQuick();
}

function normalizeModel() {
  const models = installedModels();
  if (!modelId || !models.some((model) => model.id === modelId)) {
    modelId = models[0]?.id || '';
  }
  const codes = currentModel()?.languageCodes || [];
  if (targetLang !== 'auto' && codes.length && !codes.includes(targetLang)) {
    targetLang = codes.includes('ru') ? 'ru' : codes[0];
  }
  if (sourceLang !== 'auto' && codes.length && !codes.includes(sourceLang)) {
    sourceLang = 'auto';
  }
}

async function refreshTasks() {
  const [rows] = await tryCall('listTasks');
  const next = Array.isArray(rows) ? rows : [];
  next.sort((a, b) => String(b.updatedAt || b.createdAt || '').localeCompare(
    String(a.updatedAt || a.createdAt || ''),
  ));
  tasks = next;
  if (selectedKey && !tasks.some((item) => item.taskId === selectedKey)) selectedKey = '';
  paintList();
  if (surface === 'file') paintStage();
}

function formatWhen(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function pairOf(task) {
  const source = task?.sourceLang ? langLabel(task.sourceLang) : '';
  const target = task?.targetLang ? langLabel(task.targetLang) : '';
  if (source && target) return `${source} → ${target}`;
  return target;
}

function progressText(task) {
  if (!task || !(task.total > 0)) return '';
  if (!['running', 'paused', 'queued'].includes(task.status)) return '';
  return `${task.completed}/${task.total}`;
}

function paintList() {
  const list = document.querySelector('.chat-dialogs__list');
  if (!list) return;
  const query = taskQuery.trim().toLowerCase();
  if (surface === 'text') {
    const rows = dialogs.filter((item) => !query || String(item.title || '').toLowerCase().includes(query));
    list.replaceChildren(...(rows.length
      ? rows.map((item) => dialogRow(item))
      : [el('p', {
        class: 'chat-empty',
        text: query ? 'Ничего не нашлось.' : 'Короткий текст появится здесь.',
      })]));
    return;
  }
  const rows = tasks.filter((task) => {
    if (!query) return true;
    const blob = `${task.documentName || ''} ${task.projectTitle || ''}`.toLowerCase();
    return blob.includes(query);
  });
  list.replaceChildren(...(rows.length
    ? rows.map((task) => taskRow(task))
    : [el('p', {
      class: 'chat-empty',
      text: query ? 'Ничего не нашлось.' : 'Задач пока нет. Бросьте файл справа.',
    })]));
}

function taskRow(task) {
  const selected = !staged && task.taskId === selectedKey;
  const status = STATUS_LABELS[task.status] || task.status || '';
  const projectBit = task.projectTitle && task.projectTitle !== 'Быстрые' ? task.projectTitle : '';
  const meta = [pairOf(task), projectBit, status, progressText(task), formatWhen(task.updatedAt || task.createdAt)]
    .filter(Boolean)
    .join(' · ');
  return el('div', { class: `chat-dialog${selected ? ' is-selected' : ''}` }, [
    el('button', {
      type: 'button',
      class: 'chat-dialog__open',
      title: task.documentName || 'Файл',
      onClick: () => {
        staged = null;
        selectedKey = task.taskId;
        stageKey = '';
        paintList();
        paintStage();
      },
    }, [
      el('span', { class: 'chat-dialog__title', text: task.documentName || 'Файл' }),
      el('span', { class: 'chat-dialog__meta' }, [
        el('span', { class: 'task-status', dataset: { status: task.status || '' }, text: meta }),
      ]),
    ]),
  ]);
}

function stageViewKey() {
  if (staged) {
    const kind = staged.repeat ? 'repeat' : 'new';
    return `sheet:${staged.names.join('\n')}:${modelId}:${destinationProjectId}:${kind}`;
  }
  if (selectedKey) {
    const task = tasks.find((item) => item.taskId === selectedKey);
    return `task:${selectedKey}:${task?.status || ''}:${task?.files?.[0]?.path || ''}`;
  }
  return `drop:${destinationProjectId}:${modelId}`;
}

function paintStage() {
  const node = document.querySelector('.chat-stage-host');
  if (!node || surface !== 'file' || focusKind === 'deck') return;
  const key = stageViewKey();
  if (key === stageKey && node.childElementCount) {
    paintStageLive();
    return;
  }
  stageKey = key;
  const view = staged ? settingsSheet() : (selectedKey ? taskDetail() : dropWell());
  node.replaceChildren(view);
}

function paintStageLive() {
  if (staged) return;
  const task = tasks.find((item) => item.taskId === selectedKey);
  if (!task) return;
  const live = document.querySelector('[data-live-progress]');
  if (live) live.textContent = progressText(task);
  const fill = document.querySelector('.work-file__fill');
  if (fill && task.total > 0) {
    const width = Math.min(100, Math.round((task.completed / task.total) * 100));
    fill.style.width = `${width}%`;
  }
}

function destinationValue() {
  const project = projects.find((item) => item.id === destinationProjectId);
  if (!project || project.title === 'Быстрые') return '';
  return destinationProjectId;
}

function projectChoices() {
  return [
    el('option', { value: '', text: 'Быстрые' }),
    ...projects
      .filter((item) => item.title !== 'Быстрые')
      .map((item) => el('option', { value: item.id, text: item.title || 'Проект' })),
  ];
}

function paintProjectChoices() {
  const select = document.querySelector('[data-project-select]');
  if (!(select instanceof HTMLSelectElement)) return;
  const value = destinationValue();
  select.replaceChildren(...projectChoices());
  if ([...select.options].some((option) => option.value === value)) select.value = value;
}

function destinationLine() {
  const project = projects.find((item) => item.id === destinationValue());
  if (!project) return 'Сохранится в «Быстрые».';
  return `Сохранится в «${project.title}».`;
}

function extOf(name) {
  const match = String(name || '').toLowerCase().match(/\.([a-z0-9]+)$/);
  return match ? match[1] : '';
}

function sharedSuffixes(names) {
  const sets = names.map((name) => new Set(FORMAT_BY_EXT[extOf(name)] || ['.txt', '.md']));
  if (!sets.length) return [];
  return [...sets[0]].filter((suffix) => sets.every((set) => set.has(suffix)));
}

function dropWell() {
  return el('div', { class: 'dropwell' }, [
    el('p', { class: 'dropwell__title', text: 'Перетащите документ' }),
    el('p', {
      class: 'dropwell__hint',
      text: 'TXT, Markdown, DOCX, EPUB или PDF с текстом. После выбора откроются язык, формат и модель. Оригинал не меняется.',
    }),
    el('div', { class: 'dropwell__actions' }, [
      button({ label: 'Выбрать файл', variant: 'primary', onClick: () => void pickFiles() }),
      button({
        label: 'Новый проект',
        title: 'Название, языки и свой глоссарий',
        onClick: () => requestProjectDeck({ mode: 'new' }),
      }),
    ]),
    el('p', { class: 'sheet__where', text: destinationLine() }),
  ]);
}

function sourceControl(onChange) {
  const codes = currentModel()?.languageCodes || [];
  const select = el('select', {
    class: 'select',
    ariaLabel: 'Язык оригинала',
    onChange: (event) => {
      sourceLang = event.target.value;
      onChange?.();
    },
  }, [
    el('option', { value: 'auto', text: 'Авто' }),
    ...codes.map((code) => el('option', { value: code, text: langLabel(code) })),
  ]);
  select.value = sourceLang === 'auto' || codes.includes(sourceLang) ? sourceLang : 'auto';
  sourceLang = select.value;
  return select;
}

function targetControl(onChange) {
  const codes = currentModel()?.languageCodes || [];
  const select = el('select', {
    class: 'select',
    ariaLabel: 'Язык перевода',
    onChange: (event) => {
      targetLang = event.target.value;
      onChange?.();
    },
  }, codes.map((code) => el('option', { value: code, text: langLabel(code) })));
  if (codes.includes(targetLang)) select.value = targetLang;
  return select;
}

function modelControl(onChange) {
  const models = installedModels();
  const select = el('select', {
    class: 'select',
    ariaLabel: 'Модель',
    onChange: (event) => {
      modelId = event.target.value;
      normalizeModel();
      onChange?.();
    },
  }, models.map((item) => el('option', { value: item.id, text: item.name })));
  select.value = modelId;
  return select;
}

function settingsSheet() {
  const names = staged?.names || [];
  const models = installedModels();
  const suffixes = sharedSuffixes(names);
  if (outputSuffix && !suffixes.includes(outputSuffix)) outputSuffix = '';
  const title = names.length > 1 ? filesLabel(names.length) : (names[0] || 'Файл');
  const formatSelect = el('select', {
    class: 'select',
    ariaLabel: 'Формат файла',
    onChange: (event) => {
      outputSuffix = event.target.value;
      touchQuick();
    },
  }, [
    el('option', { value: '', text: 'Как у файла' }),
    ...suffixes.map((suffix) => el('option', { value: suffix, text: FORMAT_LABEL[suffix] || suffix })),
  ]);
  formatSelect.value = outputSuffix;
  const projectSelect = el('select', {
    class: 'select',
    dataset: { projectSelect: '1' },
    ariaLabel: 'Куда сохранить',
    onChange: (event) => {
      destinationProjectId = event.target.value;
      stageKey = '';
      paintStage();
    },
  }, projectChoices());
  projectSelect.value = destinationValue();
  const tone = el('textarea', {
    class: 'textarea',
    rows: 3,
    placeholder: 'Предмет, тон, как обращаться с именами…',
    onInput: (event) => {
      context = event.target.value;
      touchQuick();
    },
  });
  tone.value = context;
  const repeatProject = staged?.repeat
    ? projects.find((item) => item.id === staged.repeat.projectId)
    : null;
  return el('div', { class: 'sheet' }, [
    el('div', { class: 'sheet__head' }, [
      el('div', {}, [
        el('p', { class: 'sheet__kicker', text: staged?.repeat ? 'Ещё раз' : 'Новый перевод' }),
        el('h2', { class: 'sheet__title', text: title }),
        names.length > 1
          ? el('p', { class: 'sheet__names', text: names.join(', ') })
          : null,
      ]),
      button({
        label: 'Убрать',
        size: 'sm',
        onClick: () => {
          staged = null;
          stageKey = '';
          paintList();
          paintStage();
        },
      }),
    ]),
    models.length
      ? el('div', { class: 'sheet__grid' }, [
        field('Оригинал', sourceControl(() => touchQuick()), '«Авто» само определяет язык. Если он известен, выберите его.'),
        field('Перевод', targetControl(() => touchQuick())),
        field(
          'Формат',
          formatSelect,
          'Как у файла сохраняет привычный вид. Обычный PDF приходит как Markdown. Книжный PDF может вернуться PDF.',
        ),
        field('Модель', modelControl(() => {
          touchQuick();
          stageKey = '';
          paintStage();
        })),
        staged?.repeat
          ? field('Куда', el('p', {
            class: 'sheet__where',
            text: repeatProject?.title || 'Тот же проект',
          }))
          : field('Куда', projectSelect),
      ])
      : el('div', { class: 'chat-missing' }, [
        el('p', { text: 'Чтобы переводить, скачайте локальную модель.' }),
        button({ label: 'К моделям', variant: 'primary', onClick: () => router.showPage('models') }),
      ]),
    el('details', { class: 'sheet__more', open: Boolean(context.trim()) }, [
      el('summary', {}, ['Ещё']),
      el('div', { class: 'sheet__more-body' }, [
        field('Тон', tone, 'Необязательно. Уходит вместе с текстом, до 800 знаков.'),
        el('label', { class: 'sheet__check' }, [
          el('input', {
            type: 'checkbox',
            checked: useGlossary,
            onChange: (event) => {
              useGlossary = event.target.checked;
              touchQuick();
            },
          }),
          el('span', { text: 'Брать термины глоссария' }),
          helpMark('Термины проекта, куда сохранится перевод. Список правится на вкладке «Глоссарий».'),
        ]),
        el('p', { class: 'sheet__kicker', text: 'Этот файл раньше' }),
        priorBlock(names),
      ]),
    ]),
    el('div', { class: 'sheet__actions' }, [
      el('p', { class: 'sheet__where', text: staged?.repeat ? 'Тот же документ, новые настройки.' : destinationLine() }),
      models.length
        ? button({
          label: committing ? 'Ставим в очередь…' : 'Перевести',
          variant: 'primary',
          disabled: committing,
          onClick: (event) => {
            event.currentTarget.disabled = true;
            void commitSheet();
          },
        })
        : null,
    ]),
  ]);
}

function priorBlock(names) {
  const wanted = new Set(names.map((name) => name.toLowerCase()));
  const prior = tasks
    .filter((task) => wanted.has(String(task.documentName || '').toLowerCase()))
    .slice(0, 6);
  if (!prior.length) {
    return el('p', { class: 'sheet__where', text: 'Файла с таким именем ещё не переводили.' });
  }
  return el('div', { class: 'prior' }, prior.map((task) => el('div', { class: 'prior__row' }, [
    el('span', {
      class: 'prior__meta',
      text: [pairOf(task), STATUS_LABELS[task.status] || task.status, formatWhen(task.updatedAt)].filter(Boolean).join(' · '),
    }),
    button({
      label: 'Открыть',
      size: 'sm',
      onClick: () => {
        staged = null;
        selectedKey = task.taskId;
        stageKey = '';
        paintList();
        paintStage();
      },
    }),
  ])));
}

async function commitSheet() {
  if (!staged || committing) return;
  committing = true;
  touchQuick();
  const current = staged;
  try {
    if (current.repeat) await startExisting(current.repeat);
    else await startFiles(current.paths || [], destinationProjectId);
  } finally {
    committing = false;
    const action = document.querySelector('.sheet__actions .btn--primary');
    if (action && staged) action.disabled = false;
  }
}

function stageRepeat(task) {
  touchQuick();
  if (task.sourceLang) sourceLang = task.sourceLang;
  if (task.targetLang) targetLang = task.targetLang;
  if (task.modelId) modelId = task.modelId;
  if (typeof task.outputSuffix === 'string') outputSuffix = task.outputSuffix;
  if (typeof task.useGlossary === 'boolean') useGlossary = task.useGlossary;
  normalizeModel();
  staged = {
    names: [task.documentName || 'Файл'],
    repeat: {
      documentId: task.documentId,
      projectId: task.projectId,
      name: task.documentName || 'Файл',
    },
  };
  selectedKey = '';
  surface = 'file';
  stageKey = '';
  paintList();
  paintStage();
}

function taskDetail() {
  const task = tasks.find((item) => item.taskId === selectedKey);
  if (!task) return dropWell();
  const file = (task.files || []).find((item) => item?.path);
  const real = task.taskId && !String(task.taskId).startsWith('export:');
  const actions = [];
  if (real && ['running', 'queued', 'paused'].includes(task.status)) {
    actions.push(button({ label: 'Стоп', size: 'sm', onClick: () => void stopJob({ taskId: task.taskId }) }));
  }
  if (real && ['paused', 'interrupted', 'failed', 'cancelled'].includes(task.status)) {
    actions.push(button({
      label: 'Продолжить',
      size: 'sm',
      variant: 'primary',
      onClick: () => void resumeJob({ taskId: task.taskId }),
    }));
  }
  if (file?.path) actions.push(...resultFileActions(file));
  else if (real && (task.status === 'complete' || task.status === 'failed')) {
    actions.push(button({
      label: 'Собрать файл',
      size: 'sm',
      onClick: () => void assembleResult(task).then(() => refreshTasks()),
    }));
  }
  if (task.documentId && task.projectId) {
    actions.push(button({ label: 'Ещё раз', size: 'sm', onClick: () => stageRepeat(task) }));
  }
  actions.push(button({ label: 'Проверка', size: 'sm', onClick: () => router.showPage('review') }));
  const width = task.total > 0 ? Math.min(100, Math.round((task.completed / task.total) * 100)) : 0;
  const projectBit = task.projectTitle ? task.projectTitle : '';
  return el('article', { class: 'sheet' }, [
    el('p', { class: 'sheet__kicker', text: STATUS_LABELS[task.status] || 'Задача' }),
    el('h2', { class: 'sheet__title', text: task.documentName || 'Файл' }),
    el('p', {
      class: 'sheet__where',
      text: [pairOf(task), projectBit, formatWhen(task.updatedAt || task.createdAt)].filter(Boolean).join(' · '),
    }),
    progressText(task)
      ? el('p', { class: 'sheet__where', dataset: { liveProgress: '1' }, text: progressText(task) })
      : null,
    task.total > 0
      ? el('div', { class: 'work-file__track', ariaHidden: 'true' }, [
        el('div', { class: 'work-file__fill', style: { width: `${width}%` } }),
      ])
      : null,
    task.error ? el('p', { class: 'sheet__error', text: task.error }) : null,
    file?.note ? el('p', { class: 'sheet__where', text: file.note }) : null,
    actions.length ? el('div', { class: 'work-file__actions' }, actions) : null,
    button({
      label: 'Новый файл',
      size: 'sm',
      onClick: () => {
        selectedKey = '';
        stageKey = '';
        paintList();
        paintStage();
      },
    }),
  ]);
}

function topBar(host) {
  return el('div', { class: 'chat-bar chat-bar--top' }, [
    el('button', {
      type: 'button',
      class: 'btn btn--sm chat-list-show',
      hidden: !listHidden,
      title: 'Показать список',
      text: 'Список',
      onClick: () => setListHidden(false),
    }),
    el('div', { class: 'chat-modes', role: 'tablist', ariaLabel: 'Что переводим' }, [
      surfaceButton('file', 'Файл', host),
      surfaceButton('text', 'Текст', host),
    ]),
  ]);
}

function surfaceButton(value, label, host) {
  return el('button', {
    class: `chat-mode${surface === value ? ' is-selected' : ''}`,
    type: 'button',
    role: 'tab',
    ariaSelected: surface === value ? 'true' : 'false',
    onClick: () => {
      if (surface === value) return;
      surface = value;
      touchQuick();
      render(host);
    },
  }, [label]);
}

function textBody(host) {
  const models = installedModels();
  const model = currentModel();
  const log = el('div', { class: 'chat-log', role: 'log', tabindex: '0' });
  bindLog(log);
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
  const tone = el('textarea', {
    class: 'textarea',
    rows: 2,
    placeholder: 'Предмет, тон, как обращаться с именами…',
    onInput: (event) => {
      context = event.target.value;
      touchQuick();
      queueMeter();
    },
  });
  tone.value = context;
  const hyNote = mode === 'ask' && model?.promptStyle === 'hy-mt2'
    ? el('p', {
      class: 'chat-note',
      text: 'Эта модель обучена переводить. В общении она может пересказать фразу, а не поддержать разговор.',
    })
    : null;
  return el('div', { class: 'text-work' }, [
    el('p', { class: 'chat-private', hidden: !incognito, text: 'Этот диалог не сохранится.' }),
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
      models.length && mode === 'translate'
        ? el('div', { class: 'sheet__grid' }, [
          field('Оригинал', sourceControl(() => touchQuick())),
          field('Перевод', targetControl(() => {
            touchQuick();
            void refreshMeter();
          })),
          field('Модель', modelControl(() => {
            touchQuick();
            render(host);
          })),
        ])
        : null,
      models.length && mode === 'ask'
        ? field('Модель', modelControl(() => {
          touchQuick();
          render(host);
        }))
        : null,
      models.length
        ? null
        : el('div', { class: 'chat-missing' }, [
          el('p', { text: 'Чтобы переводить, скачайте локальную модель.' }),
          button({ label: 'К моделям', variant: 'primary', onClick: () => router.showPage('models') }),
        ]),
      el('div', { class: 'chat-mode-wrap' }, [
        el('div', { class: 'chat-modes', role: 'radiogroup', ariaLabel: 'Режим текста' }, [
          modeButton('translate', 'Перевод', host),
          modeButton('ask', 'Общение', host),
        ]),
        el('button', {
          type: 'button',
          class: `btn btn--sm chat-incognito${incognito ? ' is-selected' : ''}`,
          ariaPressed: incognito ? 'true' : 'false',
          title: 'Не записывать этот диалог на диск',
          text: 'Инкогнито',
          onClick: () => setIncognito(!incognito),
        }),
      ]),
      hyNote,
      el('details', { class: 'chat-context', open: Boolean(context.trim()) }, [
        el('summary', {}, [
          'Тон',
          helpMark('Необязательно. Предмет, тон и имена уходят в запрос вместе с текстом, до 800 знаков.'),
        ]),
        tone,
      ]),
      el('div', { class: 'chat-files' }),
      input,
      el('div', { class: 'chat-composer__row' }, [
        el('span', { class: 'chat-counter', text: `${draft.length} / ${TEXT_LIMIT}` }),
        el('div', { class: 'chat-composer__actions' }, [
          button({
            label: 'Документ',
            title: 'Перевести файл целиком',
            onClick: () => void pickFiles(),
          }),
          button({
            label: 'Как текст',
            size: 'sm',
            title: 'Короткая выдержка из файла в сообщение, без сборки документа',
            onClick: () => void attachFiles(),
          }),
          el('button', {
            class: 'btn btn--primary chat-send',
            type: 'submit',
            disabled: !draft.trim() && attachments.length === 0 && !sending,
          }, [sending ? 'Стоп' : 'Отправить']),
        ]),
      ]),
    ]),
  ]);
}

function render(host) {
  closeHelpMarks();
  ensureWire();
  const deckRequest = consumeDeckRequest();
  const pendingFocus = window.DL?.focusProjectId || '';
  if (window.DL) window.DL.focusProjectId = '';
  if (deckRequest || pendingFocus) holdBootDialog = true;
  takeQuickMemory();
  normalizeModel();
  if (!dialogId) dialogId = newId();
  listHidden = Boolean(store.get('chatListHidden'));
  stageKey = '';

  const chat = el('div', {
    class: `chat${listHidden ? ' is-list-hidden' : ''}`,
    dataset: { stack: 'col' },
  }, [
    el('aside', { class: 'chat-dialogs', ariaLabel: surface === 'text' ? 'Диалоги' : 'Задачи' }, [
      el('div', { class: 'chat-dialogs__head' }, [
        el('p', { class: 'chat-dialogs__label', text: surface === 'text' ? 'Диалоги' : 'Задачи' }),
        el('div', { class: 'chat-dialogs__actions' }, [
          button({
            label: 'Скрыть',
            size: 'sm',
            title: 'Скрыть список',
            onClick: () => setListHidden(true),
          }),
          surface === 'text'
            ? button({ label: 'Новый', size: 'sm', title: 'Новый диалог', onClick: () => void startNew() })
            : button({
              label: 'Проект',
              size: 'sm',
              title: 'Новый проект: название, языки, модель',
              onClick: () => requestProjectDeck({ mode: 'new' }),
            }),
        ]),
      ]),
      el('input', {
        class: 'input task-search',
        type: 'search',
        placeholder: 'Найти',
        value: taskQuery,
        ariaLabel: 'Найти в списке',
        onInput: (event) => {
          taskQuery = event.target.value;
          paintList();
        },
      }),
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
        topBar(host),
        surface === 'text' ? textBody(host) : el('div', { class: 'chat-stage-host' }),
      ]),
    ]),
  ]);
  host.replaceChildren(chat);
  bindChatSplit(chat);
  const list = chat.querySelector('.chat-dialogs__list');
  const work = chat.querySelector('.chat-work');
  if (list) bindDrop(list);
  if (work) bindDrop(work);
  paintList();
  if (surface === 'text') {
    paintLog();
    paintFiles();
    paintSend();
    void refreshMeter();
  } else {
    paintStage();
  }
  void refreshProjects();
  void loadDialogs();
  void refreshTasks();
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
  subtitle: 'Файл или короткий текст',
  help: 'Бросьте документ или нажмите «Выбрать файл». Дальше язык, формат и модель. Последний выбор запоминается. «Текст» переводит фрагмент до '
    + `${TEXT_LIMIT} знаков. Проект со своим глоссарием открывается кнопкой «Проект».`,
  layout: 'chat',
  render,
  destroy,
});
