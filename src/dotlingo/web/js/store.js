/**
 * Реактивный мини-стор: состояние, подписки, события.
 * Хранит: projects, activeProject, page, theme, hardware, models, taskEvents.
 */

/** Начальное состояние приложения. */
const state = {
  /** @type {Array<object>} список проектов */
  projects: [],
  /** @type {object|null} выбранный проект */
  activeProject: null,
  /** @type {string} имя текущей страницы (роутер) */
  page: '',
  /** @type {'dark'|'light'} активная тема */
  theme: 'dark',
  /** @type {boolean} уменьшить движение и переходы интерфейса */
  reduceMotion: false,
  /** @type {object|null} информация об устройстве (HardwareSnapshot) */
  hardware: null,
  /** @type {Array<object>} доступные модели */
  models: [],
  /** @type {object|null} рекомендация устройства {id, reason} */
  recommendation: null,
  /** @type {Object<string, string>} языки: код → русское название */
  languages: {},
  /** @type {object|null} каталоги данных {projectsDir, modelsDir} */
  dataDirs: null,
  /** @type {Array<object>} события очереди задач (push из Python) */
  taskEvents: [],
  /** @type {boolean} bridge на моках (demo data) */
  demo: false,
  /** @type {boolean} колонка сжата до рельса значков */
  sidebarCollapsed: false,
  /** @type {number} последняя ширина колонки выше рельса, px */
  sidebarWidth: 252,
  /** @type {number} ширина списка диалогов, px */
  chatListWidth: 240,
  /** @type {number} высота списка диалогов в узком окне, px */
  chatListHeight: 200,
  /** @type {boolean} список диалогов скрыт, лента на всю ширину */
  chatListHidden: false,
  /** Память быстрого перевода. Подставляется после getPreferences. */
  translateSource: 'auto',
  translateTarget: 'ru',
  translateSuffix: '',
  translateModel: '',
  translateContext: '',
  translateGlossary: true,
  translateSurface: 'file',
  translateReady: false,
};

/** Подписчики на изменения: (ключ, значение, предыдущее) => void. */
const subscribers = new Set();

/** Подписчики на именованные события: Map<event, Set<fn>>. */
const listeners = new Map();

/**
 * Прочитать значение из стора.
 * @param {string} key
 * @returns {unknown}
 */
export function get(key) {
  return state[key];
}

/** Снимок всего состояния (только для чтения). */
export function snapshot() {
  return { ...state };
}

/**
 * Записать значение и оповестить подписчиков.
 * @param {string} key
 * @param {unknown} value
 */
export function set(key, value) {
  if (state[key] === value) return;
  const prev = state[key];
  state[key] = value;
  subscribers.forEach((fn) => {
    try {
      fn(key, value, prev);
    } catch (e) {
      console.error('[store] ошибка подписчика', e);
    }
  });
}

/**
 * Обновить несколько ключей одним проходом (одна волна подписчиков).
 * @param {Object<string, unknown>} patch
 */
export function patch(entries) {
  const changed = {};
  for (const [key, value] of Object.entries(entries)) {
    if (state[key] !== value) {
      changed[key] = { value, prev: state[key] };
      state[key] = value;
    }
  }
  if (Object.keys(changed).length === 0) return;
  subscribers.forEach((fn) => {
    for (const [key, { value, prev }] of Object.entries(changed)) {
      try {
        fn(key, value, prev);
      } catch (e) {
        console.error('[store] ошибка подписчика', e);
      }
    }
  });
}

/**
 * Подписаться на изменения. Возвращает функцию отписки.
 * @param {(key: string, value: unknown, prev: unknown) => void} fn
 * @returns {() => void}
 */
export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

/**
 * Добавить событие очереди задач (сверху, ограничение 200 записей).
 * @param {object} event
 */
export function pushTaskEvent(event) {
  const events = [event, ...state.taskEvents];
  if (events.length > 200) events.length = 200;
  set('taskEvents', events);
}

/**
 * Подписка на изменения конкретного ключа. Возвращает функцию отписки.
 * @param {string} key
 * @param {(value: unknown, prev: unknown) => void} fn
 * @returns {() => void}
 */
export function watch(key, fn) {
  return subscribe((k, value, prev) => {
    if (k === key) fn(value, prev);
  });
}

/* -------------------------------------------------------------------------
 * Именованные события (emit/on) - для слабосвязанных сигналов между модулями.
 * ------------------------------------------------------------------------- */

/**
 * Публикация именованного события.
 * @param {string} event
 * @param {...unknown} args
 */
export function emit(event, ...args) {
  const set = listeners.get(event);
  if (!set) return;
  set.forEach((fn) => {
    try {
      fn(...args);
    } catch (e) {
      console.error(`[store] ошибка обработчика события ${event}`, e);
    }
  });
}

/**
 * Подписка на именованное событие. Возвращает функцию отписки.
 * @param {string} event
 * @param {(...args: unknown[]) => void} fn
 * @returns {() => void}
 */
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  const set = listeners.get(event);
  set.add(fn);
  return () => set.delete(fn);
}
