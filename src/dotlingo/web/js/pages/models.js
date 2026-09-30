/**
 * Страница «Модели»: каталог GGUF-моделей, рекомендация по устройству,
 * загрузка с проверкой SHA-256, проверка целостности, импорт своей модели
 * и выбор модели для активного проекта.
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import {
  el,
  button,
  badge,
  toast,
  emptyState,
  modal,
  progressBar,
  spinner,
  formatBytes,
  icon,
} from '../components.js';

/* -------------------------------------------------------------------------
 * Словари состояний
 * ------------------------------------------------------------------------- */

/** Подписи installState модели. */
const STATE_LABELS = {
  installed: 'Установлена',
  available: 'Можно загрузить',
  unverified: 'Нельзя скачать',
  missing: 'Файл не найден',
};

/** Тон бейджа installState. */
const STATE_TONES = {
  installed: 'success',
  available: 'muted',
  unverified: 'warning',
  missing: 'error',
};

/** Подписи вердикта совместимости (dotlingo.hardware.assess_model). */
const COMPAT_LABELS = {
  no: 'Не хватает ресурсов',
  unknown: 'Совместимость не определена',
  runtime_missing: 'Нет локального runtime',
  cpu_unverified: 'Ориентир RAM помещается, CPU не проверен',
};

/** Допустимые размеры контекста для своей модели. */
const CONTEXT_SIZES = [2048, 4096, 8192, 16384, 32768];

/** Значение контекста по умолчанию. */
const DEFAULT_CONTEXT = 4096;

/** Сколько языковых чипов видно в свёрнутой карточке. */
const LANG_PREVIEW_COUNT = 6;

/** @type {HTMLElement|null} хост страницы (для ре-рендера по событиям) */
let hostRef = null;
/** @type {Array<() => void>} отписки store-событий страницы */
let unsubs = [];
/** @type {boolean} каталог хотя бы раз успешно загружен */
let loaded = false;
/** @type {{fetchedAt: string|null, stale: boolean, errors: Array<{repo: string, error: string}>, offers: object[]}} */
let marketPayload = { fetchedAt: null, stale: false, errors: [], offers: [] };
/** @type {boolean} идёт обновление рынка */
let marketRefreshing = false;

const MARKET_INTRO = [
  'Фиксированный список переводческих и многоязычных GGUF с Hugging Face.',
  'Кнопка только читает карточки, веса не качаются.',
  'Для CPU оставлены Q4 как баланс скорости и Q6 как более точный и более тяжёлый квант.',
  'Качество перевода здесь не измерялось.',
].join(' ');

/* -------------------------------------------------------------------------
 * Утилиты
 * ------------------------------------------------------------------------- */

/** Метка языка: название из store.languages или сам код. */
function langLabel(code) {
  const languages = store.get('languages') || {};
  return languages[code] || code;
}

/** Парсинг языковых кодов: запятая, точка с запятой, пробел; нижний регистр. */
function parseCodes(value) {
  const seen = new Set();
  const codes = [];
  for (const part of String(value || '').split(/[\s,;]+/)) {
    const code = part.trim().toLowerCase();
    if (code && !seen.has(code)) {
      seen.add(code);
      codes.push(code);
    }
  }
  return codes;
}

/** Мини-чип языка для карточки модели. */
function langChip(code) {
  return el('span', { class: 'model-lang', text: langLabel(code) });
}

/* -------------------------------------------------------------------------
 * Рендер страницы
 * ------------------------------------------------------------------------- */

function render(host) {
  hostRef = host;
  wireEvents();
  paint(host, { loading: !loaded });
  void refresh(host);
}

/** Полная перерисовка содержимого страницы из store. */
function paint(host, { loading = false } = {}) {
  const hardware = store.get('hardware');
  const recommendation = store.get('recommendation');
  const models = store.get('models') || [];

  const top = [];
  if (hardware) {
    if (recommendation) top.push(recommendationPanel(recommendation));
  } else {
    top.push(noHardwarePanel());
  }

  const grid = el('div', { class: 'model-grid' });
  if (models.length === 0) {
    grid.append(
      loading
        ? el('div', { class: 'page-loading' }, [spinner('lg')])
        : emptyState({
            iconName: 'chip',
            title: 'Каталог моделей пуст',
            text: 'Добавьте собственный GGUF-файл кнопкой «Добавить модель».',
          })
    );
  } else {
    grid.append(...models.map((model) => modelCard(model)));
  }

  host.replaceChildren(
    el('div', { class: 'stack stack--lg' }, [
      ...top,
      grid,
      el('p', {
        class: 'model-shop-note',
        text: 'Свой GGUF добавляется отдельно. Качество перевода отдельных направлений не измеряется.',
      }),
      marketBlock(),
    ])
  );
}

/** Загружает каталог и перерисовывает страницу. */
async function refresh(host) {
  const [data, err] = await tryCall('listModels');
  if (hostRef !== host || !host.isConnected) return;
  if (err) {
    if (!(store.get('models') || []).length) {
      host.replaceChildren(
        emptyState({
          iconName: 'error',
          title: 'Не удалось загрузить каталог моделей',
          text: err.message,
        })
      );
      return;
    }
    toast('Не удалось обновить каталог моделей', 'error');
    return;
  }
  loaded = true;
  store.patch({
    models: data?.models ?? [],
    recommendation: data?.recommendation ?? null,
  });
  const [market] = await tryCall('listMarket');
  if (hostRef !== host || !host.isConnected) return;
  if (market) marketPayload = market;
  paint(host);
}

/** Блок рынка: список с Hugging Face появляется только после кнопки. */
function marketBlock() {
  const offers = marketPayload.offers || [];
  const groups = [];
  for (const offer of offers) {
    let group = groups.find((item) => item.repo === offer.repo);
    if (!group) {
      group = { repo: offer.repo, summary: offer.summary, items: [] };
      groups.push(group);
    }
    group.items.push(offer);
  }

  const body = [];
  if (marketPayload.stale) {
    body.push(el('p', { class: 'model-shop-note', text: 'Показан прошлый список: Hugging Face сейчас не ответил.' }));
  }
  for (const error of marketPayload.errors || []) {
    body.push(el('p', { class: 'model-shop-note', text: `${error.repo}: ${error.error}` }));
  }
  if (!offers.length && !marketPayload.fetchedAt) {
    body.push(el('p', {
      class: 'model-shop-note',
      text: 'Список ещё не загружался. Обновление ходит на huggingface.co и не скачивает веса.',
    }));
  } else if (!offers.length) {
    body.push(el('p', { class: 'model-shop-note', text: 'В этот раз Hugging Face не отдал ни одного файла.' }));
  }

  const grids = groups.map((group) => {
    const grid = el('div', { class: 'model-grid' });
    grid.append(...group.items.map((offer) => marketCard(offer)));
    return el('div', { class: 'stack' }, [
      el('h3', { class: 'hw-panel__title', text: group.repo }),
      group.summary ? el('p', { class: 'model-shop-note', text: group.summary }) : null,
      grid,
    ]);
  });

  return el('section', { class: 'stack' }, [
    el('div', { class: 'model-card__head' }, [
      el('h2', { class: 'hw-panel__title', text: 'Рынок моделей' }),
      button({
        label: marketRefreshing ? 'Обновление…' : 'Обновить с Hugging Face',
        variant: 'ghost',
        disabled: marketRefreshing,
        onClick: () => void refreshMarketList(),
      }),
    ]),
    el('p', { class: 'model-shop-note', text: MARKET_INTRO }),
    marketPayload.fetchedAt
      ? el('p', { class: 'model-shop-note', text: `Список от ${marketPayload.fetchedAt}` })
      : null,
    ...body,
    ...grids,
  ]);
}

/** Карточка одного файла с рынка. */
function marketCard(offer) {
  const downloadBtn = button({
    label: 'Скачать',
    variant: 'primary',
    size: 'sm',
    iconName: 'download',
    disabled: true,
    onClick: () => void downloadFlow({ id: offer.id, name: offer.name, market: true }),
  });
  const consent = offer.installed
    ? el('p', { class: 'model-card__status-note', text: 'Этот файл уже есть в каталоге.' })
    : el('label', { class: 'consent-check' }, [
        el('input', {
          class: 'consent-check__input',
          type: 'checkbox',
          onChange: (e) => {
            downloadBtn.disabled = !e.target.checked;
          },
        }),
        el('span', { class: 'consent-check__box' }),
        el('span', {
          class: 'consent-check__label',
          text: `Ознакомился с лицензией ${offer.license || 'Не указана'} и согласен скачать ${offer.sizeLabel}`,
        }),
      ]);
  return el('article', { class: 'model-card panel' }, [
    el('div', { class: 'model-card__head' }, [
      el('h3', { class: 'model-card__name ellipsis', title: offer.name, text: offer.name }),
      badge({
        label: offer.installed ? 'Уже в каталоге' : (offer.roleLabel || offer.quantization),
        tone: offer.installed ? 'success' : 'muted',
      }),
    ]),
    el('p', { class: 'model-meta__row', text: [offer.quantization, offer.sizeLabel, offer.license].filter(Boolean).join(' · ') }),
    offer.caveat ? el('p', { class: 'model-shop-note', text: offer.caveat }) : null,
    consent,
    offer.installed ? null : downloadBtn,
    offer.cardUrl
      ? el('a', { class: 'model-link', href: offer.cardUrl, target: '_blank', rel: 'noreferrer', text: 'Карточка на Hugging Face' })
      : null,
  ]);
}

/** Явный запрос карточек. Веса не скачиваются. */
async function refreshMarketList() {
  if (marketRefreshing) return;
  marketRefreshing = true;
  if (hostRef) paint(hostRef);
  try {
    const data = await call('refreshMarket');
    if (!data?.started) {
      marketRefreshing = false;
      toast('Обновление рынка доступно в окне приложения', 'error');
      if (hostRef) paint(hostRef);
    }
  } catch (e) {
    marketRefreshing = false;
    toast(e.message, 'error');
    if (hostRef) paint(hostRef);
  }
}

/** Ре-рендер по push-событию (только пока страница открыта). */
function rerender() {
  if (router.currentPage() !== 'models') return;
  if (!hostRef || !hostRef.isConnected) return;
  void refresh(hostRef);
}

/* -------------------------------------------------------------------------
 * Панели сверху страницы
 * ------------------------------------------------------------------------- */

/** Панель «проверка не запускалась». */
function noHardwarePanel() {
  return el('div', { class: 'panel hw-panel' }, [
    el('div', {}, [
      el('h3', { class: 'hw-panel__title', text: 'Проверка устройства ещё не запускалась.' }),
      el('p', {
        class: 'hw-panel__text',
        text: 'Подбор учитывает доступную RAM, свободное место и CPU.',
      }),
    ]),
    button({ label: 'Проверить устройство', variant: 'primary', onClick: () => void runDetect() }),
  ]);
}

/** Панель рекомендации устройства. */
function recommendationPanel(recommendation) {
  const model = (store.get('models') || []).find((m) => m.id === recommendation.id);
  const activeProject = store.get('activeProject');

  return el('section', { class: 'panel model-rec' }, [
    el('span', { class: 'model-rec__eyebrow', text: 'Рекомендация для этого устройства' }),
    el('h3', {
      class: 'model-rec__name',
      text: model?.name ?? 'Подходящая модель не определена',
    }),
    el('p', { class: 'model-rec__reason', text: recommendation.reason || '' }),
    el('p', {
      class: 'model-rec__disclaimer',
      text: 'Подбор оценивает совместимость по ресурсам, не качество перевода; используется CPU.',
    }),
    activeProject && model
      ? el('div', { class: 'model-rec__actions' }, [
          button({
            label: 'Выбрать для проекта',
            variant: 'primary',
            size: 'sm',
            onClick: () => void selectModel(model),
          }),
        ])
      : null,
  ]);
}

/* -------------------------------------------------------------------------
 * Карточка модели
 * ------------------------------------------------------------------------- */

/** Карточка одной модели каталога. */
function modelCard(model) {
  const activeProject = store.get('activeProject');
  const compat = model.compatibility ?? null;

  const licenseLine = [`Лицензия: ${model.license || 'Не указана'}`, model.quantization, model.sizeLabel]
    .filter(Boolean)
    .join(' · ');

  const langsFull = model.languageCodes || [];
  const preview = langsFull.slice(0, LANG_PREVIEW_COUNT);
  const restCount = langsFull.length - preview.length;

  const meta = el('div', { class: 'model-meta' }, [
    el('div', { class: 'model-meta__row', text: licenseLine }),
    el('div', {
      class: 'model-meta__row',
      text: model.estimatedRamGb
        ? `≈ ${model.estimatedRamGb} ГБ RAM (расчёт)`
        : 'RAM не опубликована',
    }),
    compat
      ? el('div', { class: 'model-compat' }, [
          el('span', {
            class: `model-compat__verdict model-compat__verdict--${compat.verdict}`,
            text: COMPAT_LABELS[compat.verdict] ?? compat.verdict,
          }),
          el('span', { class: 'model-compat__reason', text: compat.reason || '' }),
        ])
      : null,
    model.testedOnWindows === false
      ? el('div', { class: 'model-warn', text: 'Запуск этой сборки на Windows не проверен' })
      : null,
    model.uiDetails
      ? el('div', { class: 'model-meta__row model-meta__row--muted', text: model.uiDetails })
      : null,
  ]);

  const langsRow = el('div', { class: 'model-card__langs' }, [
    ...preview.map((code) => langChip(code)),
    restCount > 0 ? el('span', { class: 'model-lang model-lang--more', text: `+${restCount}` }) : null,
  ]);

  const details = el('div', { class: 'model-card__details', hidden: true }, [
    el('div', { class: 'model-card__details-section' }, [
      el('span', { class: 'model-card__details-label', text: 'Языки' }),
      el('div', { class: 'model-card__langs' }, langsFull.map((code) => langChip(code))),
    ]),
    model.uiDetails
      ? el('div', { class: 'model-card__details-section' }, [
          el('span', { class: 'model-card__details-label', text: 'Детали' }),
          el('p', { class: 'model-card__details-text', text: model.uiDetails }),
        ])
      : null,
    model.notes
      ? el('div', { class: 'model-card__details-section' }, [
          el('span', { class: 'model-card__details-label', text: 'Заметки' }),
          el('p', { class: 'model-card__details-text', text: model.notes }),
        ])
      : null,
  ]);

  const toggle = el(
    'button',
    { class: 'model-card__toggle', type: 'button', onClick: () => {
      const open = details.hidden;
      details.hidden = !open;
      toggle.classList.toggle('is-open', open);
    } },
    [el('span', { text: 'Подробнее' }), icon('chevron-down')]
  );

  return el('article', { class: 'model-card panel', dataset: { id: model.id } }, [
    el('div', { class: 'model-card__head' }, [
      el('h3', { class: 'model-card__name ellipsis', title: model.name, text: model.name }),
      badge({ label: STATE_LABELS[model.installState] ?? model.installState, tone: STATE_TONES[model.installState] ?? 'muted' }),
    ]),
    el('p', {
      class: 'model-card__desc',
      text: model.uiDescription || model.notes || 'Описание модели не заполнено.',
    }),
    meta,
    langsRow,
    toggle,
    details,
    cardFooter(model, activeProject),
  ]);
}

/** Футер карточки: действия по installState. */
function cardFooter(model, activeProject) {
  const links = el('div', { class: 'model-card__links' }, [
    model.cardUrl
      ? el('a', { class: 'model-link', href: model.cardUrl, target: '_blank', rel: 'noreferrer', text: 'Карточка модели' })
      : null,
    model.licenseUrl
      ? el('a', { class: 'model-link', href: model.licenseUrl, target: '_blank', rel: 'noreferrer', text: 'Лицензия' })
      : null,
  ]);

  if (model.installState === 'installed') {
    return el('footer', { class: 'model-card__footer' }, [
      el('div', { class: 'model-card__actions row row--wrap' }, [
        activeProject
          ? button({
              label: 'Выбрать для проекта',
              variant: 'primary',
              size: 'sm',
              onClick: () => void selectModel(model),
            })
          : null,
        button({
          label: 'Проверить',
          variant: 'ghost',
          size: 'sm',
          onClick: () => void verifyModel(model),
        }),
      ]),
      links.childElementCount > 0 ? links : null,
    ]);
  }

  if (model.installState === 'available') {
    const downloadBtn = button({
      label: 'Скачать',
      variant: 'primary',
      size: 'sm',
      iconName: 'download',
      disabled: true,
      onClick: () => void downloadFlow(model),
    });
    const consent = el('label', { class: 'consent-check' }, [
      el('input', {
        class: 'consent-check__input',
        type: 'checkbox',
        onChange: (e) => {
          downloadBtn.disabled = !e.target.checked;
        },
      }),
      el('span', { class: 'consent-check__box' }),
      el('span', {
        class: 'consent-check__label',
        text: `Ознакомился с лицензией ${model.license || 'Не указана'} и согласен скачать ${model.sizeLabel}`,
      }),
    ]);
    return el('footer', { class: 'model-card__footer' }, [
      consent,
      el('div', { class: 'model-card__actions row row--wrap' }, [downloadBtn]),
      links.childElementCount > 0 ? links : null,
    ]);
  }

  if (model.installState === 'unverified') {
    const text = model.format === 'GGUF'
      ? 'Скачать нельзя: файл не закреплён.'
      : 'Скачать нельзя: нужен один файл GGUF. Этот формат приложение не запускает.';
    return el('footer', { class: 'model-card__footer' }, [
      el('p', { class: 'model-card__status-note', text }),
    ]);
  }

  return el('footer', { class: 'model-card__footer' }, [
    el('p', { class: 'model-card__status-note', text: 'Файл не найден · добавьте GGUF заново.' }),
  ]);
}

/* -------------------------------------------------------------------------
 * Действия с моделями
 * ------------------------------------------------------------------------- */

/** Выбрать модель для активного проекта. */
async function selectModel(model) {
  try {
    const project = await call('selectModelForProject', model.id);
    store.set('activeProject', project);
    toast('Модель выбрана для проекта', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/** Запустить проверку целостности установленной модели. */
async function verifyModel(model) {
  try {
    await call('verifyModel', model.id);
    toast('Проверка запущена');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/** Запустить определение устройства. */
async function runDetect() {
  try {
    await call('detectHardware');
    toast('Проверка устройства запущена');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* -------------------------------------------------------------------------
 * Поток загрузки модели (переиспользуется мастером первого запуска)
 * ------------------------------------------------------------------------- */

/**
 * Запускает загрузку модели и открывает модалку прогресса.
 * @param {object} model - карточка модели из listModels.
 */
export async function downloadFlow(model) {
  try {
    await call(model.market ? 'downloadMarketModel' : 'downloadModel', model.id);
  } catch (e) {
    toast(e.message, 'error');
    return;
  }

  const bar = progressBar(null);
  const status = el('p', { class: 'download-status', text: 'Подготовка загрузки…' });
  const unsubs = [];

  unsubs.push(
    store.on('download_progress', (p) => {
      if (p?.modelId === model.id) update(p);
    })
  );

  unsubs.push(
    store.on('download_done', (p) => {
      if (p?.modelId !== model.id) return;
      dialog.close();
      if (p.ok) toast('Модель загружена и проверена', 'success');
      else toast(p.error || 'Загрузка не удалась', 'error');
    })
  );

  const dialog = modal({
    title: `Загрузка · ${model.name}`,
    body: [
      el('div', { class: 'stack' }, [
        el('p', {
          class: 'download-note',
          text: 'Файл проверяется по размеру и SHA-256 перед активацией.',
        }),
        bar.root,
        status,
      ]),
    ],
    actions: [{ label: 'Отменить', onClick: () => void doCancel() }],
    onClose: () => unsubs.forEach((unsub) => unsub()),
  });

  const cancelBtn = dialog.root.querySelector('.modal__footer .btn');

  /** Обновление прогресса по фазам. */
  function update(p) {
    if (p.phase === 'verifying') {
      bar.set(null);
      status.textContent = 'Проверка SHA-256 · отмена ещё доступна';
      return;
    }
    if (p.phase === 'activating') {
      bar.set(null);
      status.textContent = 'Активация файла · отмена больше недоступна';
      if (cancelBtn) cancelBtn.disabled = true;
      return;
    }
    if (p.total > 0) {
      bar.set(p.bytes / p.total);
      status.textContent = `Получено ${formatBytes(p.bytes)} из ${formatBytes(p.total)} · ETA не показывается`;
    } else {
      bar.set(null);
      status.textContent = `Получено ${formatBytes(p.bytes)} · размер неизвестен · ETA не показывается`;
    }
  }

  /** Отмена загрузки, пока она доступна. */
  async function doCancel() {
    try {
      const data = await call('cancelDownload');
      if (data && data.accepted === false) {
        status.textContent = 'Модель уже активируется. Отмена недоступна.';
        if (cancelBtn) cancelBtn.disabled = true;
      }
    } catch (e) {
      toast(e.message, 'error');
    }
  }
}

/* -------------------------------------------------------------------------
 * Модалка импорта своей модели
 * ------------------------------------------------------------------------- */

/** Модалка «Добавить свою модель»: выбор GGUF, метаданные, импорт. */
function importModal() {
  let pathInput;
  let nameInput;
  let licenseInput;
  let codesInput;
  let contextSelect;
  let errorText;
  let importBtn;

  const dialog = modal({
    title: 'Добавить свою модель',
    render: (body) => {
      pathInput = el('input', { class: 'input', type: 'text', readonly: true, placeholder: 'Файл не выбран' });
      nameInput = el('input', { class: 'input', type: 'text', placeholder: 'Например, My Translator Q4_K_M' });
      licenseInput = el('input', { class: 'input', type: 'text', placeholder: 'Не указана' });
      codesInput = el('input', { class: 'input', type: 'text', placeholder: 'en, ru' });
      contextSelect = el(
        'select',
        { class: 'select' },
        CONTEXT_SIZES.map((size) => el('option', { value: String(size), text: String(size) }))
      );
      contextSelect.value = String(DEFAULT_CONTEXT);
      errorText = el('div', { class: 'field-error' });

      body.append(
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Файл GGUF' }),
          el('div', { class: 'model-import__file-row' }, [
            pathInput,
            button({ label: 'Выбрать файл', variant: 'ghost', size: 'sm', onClick: () => void pickFile() }),
          ]),
          el('p', {
            class: 'field__hint',
            text: 'Файл будет скопирован в хранилище DotLingo и проверен по SHA-256.',
          }),
        ]),
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Название' }),
          nameInput,
        ]),
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Лицензия' }),
          licenseInput,
        ]),
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Языковые коды' }),
          codesInput,
          el('p', { class: 'field__hint', text: 'Через запятую или пробел, например: en, ru, de.' }),
        ]),
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Контекст' }),
          contextSelect,
        ]),
        errorText
      );
    },
    actions: [
      { label: 'Отмена' },
      { label: 'Импортировать', variant: 'primary', onClick: () => void submit() },
    ],
    onClose: () => {
      unsubImported?.();
      unsubImported = null;
    },
  });

  importBtn = dialog.root.querySelector('.modal__footer .btn--primary');

  /** @type {(() => void)|null} отписка custom_model_imported */
  let unsubImported = store.on('custom_model_imported', (payload) => {
    if (!payload) return;
    if (payload.ok) {
      dialog.close();
      toast('Модель добавлена', 'success');
    } else {
      setBusy(false);
      toast(payload.error || 'Не удалось импортировать модель', 'error');
    }
  });

  /** Выбор GGUF-файла через нативный диалог. */
  async function pickFile() {
    const [path] = await tryCall('resolveGGUFPath');
    if (path) {
      pathInput.value = String(path);
      showError('');
    }
  }

  /** Показать/скрыть inline-ошибку формы. */
  function showError(text) {
    if (!errorText) return;
    errorText.textContent = text ?? '';
    errorText.classList.toggle('is-visible', Boolean(text));
  }

  /** Включить/выключить кнопку импорта (spinner + текст занятости). */
  function setBusy(busy) {
    if (!importBtn) return;
    importBtn.disabled = busy;
    importBtn.replaceChildren(
      ...(busy ? [spinner('sm')] : []),
      el('span', { class: 'btn__label', text: busy ? 'Копирование и проверка файла…' : 'Импортировать' })
    );
  }

  /** Валидация и запуск импорта. */
  async function submit() {
    showError('');
    const sourcePath = pathInput.value.trim();
    const name = nameInput.value.trim();
    const codes = parseCodes(codesInput.value);
    const contextSize = Number(contextSelect.value);

    if (!sourcePath.toLowerCase().endsWith('.gguf')) {
      showError('Выберите файл с расширением .gguf.');
      return;
    }
    if (!name) {
      showError('Укажите название модели.');
      return;
    }
    if (codes.length === 0) {
      showError('Укажите хотя бы один языковой код.');
      return;
    }
    if (!CONTEXT_SIZES.includes(contextSize)) {
      showError('Выберите размер контекста из списка.');
      return;
    }

    setBusy(true);
    try {
      await call('importCustomModel', {
        sourcePath,
        name,
        languageCodes: codes,
        contextSize,
        licenseName: licenseInput.value.trim(),
      });
    } catch (e) {
      setBusy(false);
      toast(e.message, 'error');
    }
  }
}

/* -------------------------------------------------------------------------
 * Действия шапки
 * ------------------------------------------------------------------------- */

function actions() {
  return [
    button({
      label: 'Проверить устройство',
      variant: 'ghost',
      onClick: () => void runDetect(),
    }),
    button({
      label: 'Добавить модель',
      variant: 'primary',
      iconName: 'plus',
      onClick: () => importModal(),
    }),
  ];
}

/* -------------------------------------------------------------------------
 * Жизненный цикл
 * ------------------------------------------------------------------------- */

/** Подписки на push-события, влияющие на страницу. */
function wireEvents() {
  unsubs.forEach((unsub) => unsub());
  unsubs = [
    store.on('hardware_detected', rerender),
    store.on('model_verified', (payload) => {
      if (!payload) return;
      if (payload.ok) toast('Модель проверена', 'success');
      else toast(payload.error || 'Проверка не пройдена', 'error');
      rerender();
    }),
    store.on('custom_model_imported', rerender),
    store.on('download_done', rerender),
    store.on('market_refreshed', (payload) => {
      marketRefreshing = false;
      if (payload && payload.ok === false) toast(payload.error || 'Рынок не обновился', 'error');
      else if (payload?.stale) toast('Список прежний: Hugging Face не ответил');
      else toast('Рынок обновлён');
      rerender();
    }),
  ];
}

function destroy() {
  unsubs.forEach((unsub) => unsub());
  unsubs = [];
  hostRef = null;
  loaded = false;
}

/* -------------------------------------------------------------------------
 * Регистрация
 * ------------------------------------------------------------------------- */

router.registerPage('models', {
  title: 'Модели',
  subtitle: 'Локальные GGUF-модели перевода',
  render,
  destroy,
  actions,
});
