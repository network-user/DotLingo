/**
 * Страница «Модели»: каталог GGUF-моделей, рекомендация по устройству,
 * загрузка с проверкой SHA-256, проверка целостности, импорт своей модели
 * и выбор модели для активного проекта.
 */

import { call, tryCall } from '../bridge.js';
import { catalogFailure, refreshCatalog } from '../device.js';
import { downloadFlow } from '../download-ui.js';
import * as store from '../store.js';
import * as router from '../router.js';
import {
  el,
  button,
  badge,
  toast,
  emptyState,
  modal,
  spinner,
  icon,
  closeHelpMarks,
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
/** @type {object|null} файл рынка, выбранный для «Добавить модель» */
let pickedOffer = null;
/** @type {HTMLElement|null} тело открытого окна рынка */
let marketBody = null;
/** @type {{close: () => void, root: HTMLElement}|null} */
let marketDialog = null;
/** @type {boolean} после выбора на рынке вернуться к окну ссылки */
let resumeLink = false;
/** @type {HTMLElement|null} меню «Добавить модель» */
let addMenu = null;
/** @type {HTMLElement|null} */
let addAnchor = null;
/** @type {(() => void)|null} */
let addMenuOff = null;

const MARKET_INTRO = [
  'Закреплённый список переводческих GGUF с Hugging Face.',
  '«Обновить» только читает карточки и ничего не скачивает.',
  'Для процессора оставлены Q4, он легче, и Q6, он тяжелее и точнее.',
  'Насколько хорошо модель переводит конкретную пару, этот список не проверяет.',
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
  closeHelpMarks();
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
            title: 'В каталоге пока нет моделей',
            text: 'Откройте рынок кнопкой «+» или добавьте свой GGUF через «Добавить модель».',
          })
    );
  } else {
    grid.append(...models.map((model) => modelCard(model)));
  }

  host.replaceChildren(
    el('div', { class: 'stack stack--lg' }, [
      ...top,
      grid,
      loading ? null : el('div', { class: 'model-plus-row' }, [marketPlus()]),
      el('p', {
        class: 'model-shop-note',
        text: '«+» открывает рынок. Свой файл и ссылка из закреплённого списка добавляются через «Добавить модель». Качество пар каталог не проверяет.',
      }),
    ])
  );
}

/** Загружает каталог и перерисовывает страницу. */
async function refresh(host) {
  const ok = await refreshCatalog();
  if (hostRef !== host || !host.isConnected) return;
  if (!ok) {
    if (!(store.get('models') || []).length) {
      host.replaceChildren(
        emptyState({
          iconName: 'error',
          title: 'Не удалось загрузить каталог моделей',
          text: catalogFailure() || 'Не удалось прочитать каталог.',
        })
      );
      return;
    }
    toast('Не удалось обновить каталог моделей', 'error');
    return;
  }
  loaded = true;
  const [market] = await tryCall('listMarket');
  if (hostRef !== host || !host.isConnected) return;
  if (market) {
    marketPayload = market;
    const offers = market.offers || [];
    if (pickedOffer && !offers.some((item) => item.id === pickedOffer.id)) pickedOffer = null;
  }
  paint(host);
  paintMarket();
}

/** Перерисовать открытое окно рынка, не трогая список установленных. */
function paintMarket() {
  if (!marketBody) return;
  marketBody.replaceChildren(marketBlock());
}

/** Содержимое окна рынка. */
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

  return el('div', { class: 'stack' }, [
    el('div', { class: 'model-market__bar' }, [
      el('p', { class: 'model-shop-note', text: MARKET_INTRO }),
      button({
        label: marketRefreshing ? 'Обновление…' : 'Обновить с Hugging Face',
        variant: 'ghost',
        disabled: marketRefreshing,
        onClick: () => void refreshMarketList(),
      }),
    ]),
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
  const picked = pickedOffer?.id === offer.id;
  const pickBtn = offer.installed
    ? null
    : button({
        label: picked ? 'Выбрано' : 'Выбрать',
        variant: 'ghost',
        size: 'sm',
        onClick: () => pickOffer(offer),
      });
  if (pickBtn) pickBtn.setAttribute('aria-pressed', picked ? 'true' : 'false');
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
  return el('article', { class: `model-card panel${picked ? ' is-picked' : ''}` }, [
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
    el('div', { class: 'model-card__actions row row--wrap' }, [
      offer.installed ? null : downloadBtn,
      pickBtn,
    ]),
    offer.cardUrl
      ? el('a', { class: 'model-link', href: offer.cardUrl, target: '_blank', rel: 'noreferrer', text: 'Карточка на Hugging Face' })
      : null,
  ]);
}

/** Явный запрос карточек. Веса не скачиваются. */
async function refreshMarketList() {
  if (marketRefreshing) return;
  marketRefreshing = true;
  paintMarket();
  try {
    const data = await call('refreshMarket');
    if (!data?.started) {
      marketRefreshing = false;
      toast('Обновление рынка доступно в окне приложения', 'error');
      paintMarket();
    }
  } catch (e) {
    marketRefreshing = false;
    toast(e.message, 'error');
    paintMarket();
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
        text: 'Подбор учитывает всю память устройства, свободное место и CPU.',
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
      text: 'Подбор смотрит, хватит ли памяти и места на диске. Качество перевода он не оценивает.',
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
    model.testedOnWindows === false && model.installState !== 'installed'
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

export { downloadFlow };

/* -------------------------------------------------------------------------
 * Рынок и добавление
 * ------------------------------------------------------------------------- */

/** Кнопка под списком: не карточка модели, открывает рынок. */
function marketPlus() {
  return el('button', {
    class: 'model-plus',
    type: 'button',
    title: 'Рынок моделей',
    'aria-label': 'Открыть рынок моделей',
    onClick: () => openMarket(),
  }, [
    icon('plus'),
    el('span', { class: 'model-plus__label', text: 'Рынок' }),
  ]);
}

/** Окно закреплённого списка. Скачивание остаётся на карточке, после согласия. */
function openMarket() {
  closeAddMenu();
  if (marketDialog?.root?.isConnected) {
    paintMarket();
    return;
  }
  marketDialog = modal({
    title: 'Рынок моделей',
    subtitle: 'Закреплённый список. Файл скачивается только после согласия, с повторной проверкой ревизии и SHA-256.',
    panelClass: 'modal--market',
    render: (body) => {
      marketBody = body;
      paintMarket();
    },
    actions: [{ label: 'Закрыть', onClick: () => marketDialog?.close() }],
    onClose: () => {
      marketBody = null;
      marketDialog = null;
      resumeLink = false;
    },
  });
}

/** Запомнить файл для пункта «По ссылке или с рынка». */
function pickOffer(offer) {
  pickedOffer = offer;
  const back = resumeLink;
  resumeLink = false;
  if (back) {
    marketDialog?.close();
    linkModal();
    return;
  }
  paintMarket();
  toast(`Выбрано: ${offer.name}. Добавить можно через «Добавить модель».`);
}

/** Кнопка шапки с двумя способами добавления. */
function addModelButton() {
  const trigger = button({
    label: 'Добавить модель',
    variant: 'primary',
    iconName: 'plus',
    onClick: (event) => openAddMenu(event.currentTarget),
  });
  trigger.setAttribute('aria-haspopup', 'menu');
  trigger.setAttribute('aria-expanded', 'false');
  return trigger;
}

function closeAddMenu() {
  addMenu?.remove();
  addMenu = null;
  addAnchor?.setAttribute('aria-expanded', 'false');
  addAnchor = null;
  addMenuOff?.();
  addMenuOff = null;
}

function openAddMenu(anchor) {
  if (addMenu) {
    closeAddMenu();
    return;
  }
  addAnchor = anchor;
  anchor.setAttribute('aria-expanded', 'true');
  const menu = el('div', { class: 'add-menu', role: 'menu' }, [
    addChoice(
      'С компьютера',
      'Локальный файл GGUF. Копия проверяется по размеру и SHA-256.',
      () => {
        closeAddMenu();
        importModal();
      },
    ),
    addChoice(
      'По ссылке или с рынка',
      'Ссылка на файл из закреплённого списка или модель, выбранная на рынке.',
      () => {
        closeAddMenu();
        linkModal();
      },
    ),
  ]);
  document.body.append(menu);
  const rect = anchor.getBoundingClientRect();
  const width = Math.min(320, window.innerWidth - 16);
  menu.style.width = `${width}px`;
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.left = `${Math.max(8, rect.right - width)}px`;
  const onDoc = (event) => {
    if (menu.contains(event.target) || anchor.contains(event.target)) return;
    closeAddMenu();
  };
  const onKey = (event) => {
    if (event.key === 'Escape') {
      closeAddMenu();
      anchor.focus();
    }
  };
  setTimeout(() => document.addEventListener('click', onDoc), 0);
  document.addEventListener('keydown', onKey);
  addMenu = menu;
  addMenuOff = () => {
    document.removeEventListener('click', onDoc);
    document.removeEventListener('keydown', onKey);
  };
  menu.querySelector('button')?.focus();
}

function addChoice(title, hint, onClick) {
  return el('button', {
    class: 'add-menu__item',
    type: 'button',
    role: 'menuitem',
    onClick,
  }, [
    el('span', { class: 'add-menu__title', text: title }),
    el('span', { class: 'add-menu__hint', text: hint }),
  ]);
}

/**
 * Второй пункт «Добавить модель»: ссылка только выбирает файл из кэша рынка.
 * Скачивание идёт тем же путём, что и кнопка «Скачать» на карточке.
 */
function linkModal() {
  let dialog;
  let linkInput;
  let errorText;
  let preview;
  let previewName;
  let previewMeta;
  let choiceBox;
  let consentRow;
  let consentInput;
  let consentLabel;
  let addBtn;
  let current = pickedOffer;
  let agreed = false;

  const syncAdd = () => {
    if (!addBtn) return;
    addBtn.disabled = !current || current.installed || !agreed;
  };

  const setCurrent = (offer) => {
    current = offer || null;
    if (current) pickedOffer = current;
    agreed = false;
    if (consentInput) consentInput.checked = false;
    if (preview) preview.hidden = !current;
    if (current && previewName && previewMeta && consentLabel) {
      previewName.textContent = current.name;
      previewMeta.textContent = [current.repo, current.filename, current.quantization, current.sizeLabel]
        .filter(Boolean)
        .join(' · ');
      consentLabel.textContent = current.installed
        ? 'Этот файл уже есть в каталоге.'
        : `Ознакомился с лицензией ${current.license || 'Не указана'} и согласен скачать ${current.sizeLabel}`;
    }
    if (consentRow) consentRow.hidden = !current;
    if (consentInput) consentInput.disabled = Boolean(current?.installed);
    choiceBox?.querySelectorAll('.model-link-choice').forEach((node) => {
      node.classList.toggle('is-selected', node.dataset.id === current?.id);
    });
    syncAdd();
  };

  const showError = (text) => {
    if (!errorText) return;
    errorText.textContent = text ?? '';
    errorText.classList.toggle('is-visible', Boolean(text));
  };

  dialog = modal({
    title: 'По ссылке или с рынка',
    subtitle: 'Ссылка только выбирает файл из закреплённого списка. Произвольный адрес не скачивается.',
    render: (body) => {
      linkInput = el('input', {
        class: 'input',
        type: 'url',
        placeholder: 'https://huggingface.co/…/file.gguf',
        spellcheck: false,
      });
      errorText = el('div', { class: 'field-error' });
      previewName = el('div', { class: 'model-link-pick__name' });
      previewMeta = el('div', { class: 'model-link-pick__meta' });
      preview = el('div', { class: 'model-link-pick', hidden: !current }, [
        previewName,
        previewMeta,
        button({
          label: 'Убрать',
          variant: 'ghost',
          size: 'sm',
          onClick: () => {
            pickedOffer = null;
            current = null;
            setCurrent(null);
          },
        }),
      ]);
      choiceBox = el('div', { class: 'model-link-choices' });
      consentLabel = el('span', { class: 'consent-check__label' });
      consentInput = el('input', {
        class: 'consent-check__input',
        type: 'checkbox',
        onChange: (event) => {
          agreed = event.target.checked;
          syncAdd();
        },
      });
      consentRow = el('label', { class: 'consent-check', hidden: true }, [
        consentInput,
        el('span', { class: 'consent-check__box' }),
        consentLabel,
      ]);
      body.append(
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Ссылка Hugging Face' }),
          linkInput,
          el('p', {
            class: 'field__hint',
            text: 'Репозиторий должен быть в рынке DotLingo, а файл — в уже загруженном списке.',
          }),
        ]),
        el('div', { class: 'row row--wrap' }, [
          button({ label: 'Найти по ссылке', variant: 'ghost', size: 'sm', onClick: () => void lookup() }),
          button({
            label: 'Выбрать на рынке',
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              resumeLink = true;
              dialog.close();
              openMarket();
            },
          }),
        ]),
        preview,
        choiceBox,
        consentRow,
        errorText,
      );
      setCurrent(current);
    },
    actions: [
      { label: 'Отмена', onClick: () => dialog.close() },
      { label: 'Добавить', variant: 'primary', onClick: () => void submit() },
    ],
  });
  addBtn = dialog.root.querySelector('.modal__footer .btn--primary');
  syncAdd();

  async function lookup() {
    showError('');
    const url = linkInput.value.trim();
    if (!url) {
      showError('Вставьте ссылку или выберите файл на рынке.');
      return;
    }
    try {
      const data = await call('resolveMarketLink', url);
      const offers = data?.offers || [];
      if (!offers.length) {
        showError('В текущем списке нет такого файла. Откройте рынок и обновите его.');
        return;
      }
      if (data.offerId) {
        choiceBox.replaceChildren();
        setCurrent(offers.find((item) => item.id === data.offerId) || offers[0]);
        return;
      }
      setCurrent(null);
      choiceBox.replaceChildren(
        el('p', {
          class: 'field__hint',
          text: 'В ссылке нет файла. Выберите один из текущего списка этого репозитория.',
        }),
        ...offers.map((offer) => el('button', {
          class: 'model-link-choice',
          type: 'button',
          dataset: { id: offer.id },
          onClick: () => setCurrent(offer),
        }, [
          el('span', { class: 'model-link-choice__name', text: offer.name }),
          el('span', {
            class: 'model-link-choice__meta',
            text: [offer.quantization, offer.sizeLabel, offer.license].filter(Boolean).join(' · '),
          }),
        ])),
      );
    } catch (error) {
      showError(error.message);
    }
  }

  async function submit() {
    showError('');
    if (!current) {
      showError('Вставьте ссылку или выберите файл на рынке.');
      return;
    }
    if (current.installed) {
      showError('Этот файл уже есть в каталоге.');
      return;
    }
    if (!agreed) {
      showError('Подтвердите лицензию и размер файла.');
      return;
    }
    const offer = current;
    await downloadFlow({
      id: offer.id,
      name: offer.name,
      market: true,
      sizeBytes: offer.sizeBytes,
    });
    if (store.get('download')?.modelId === offer.id) dialog.close();
  }
}

/* -------------------------------------------------------------------------
 * Модалка импорта своей модели
 * ------------------------------------------------------------------------- */

/**
 * Перетаскивание .gguf на поле. WebView2 отдаёт путь только после FilesDropped.
 * @param {HTMLElement} zone
 * @param {(path: string) => void} onPath
 * @param {(text: string) => void} onError
 */
function bindModelDrop(zone, onPath, onError) {
  const arm = (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    zone.classList.add('is-drop');
  };
  zone.addEventListener('dragenter', arm);
  zone.addEventListener('dragover', arm);
  zone.addEventListener('dragleave', (event) => {
    if (zone.contains(event.relatedTarget)) return;
    zone.classList.remove('is-drop');
  });
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('is-drop');
    void acceptDroppedModel(event).then((path) => {
      if (path) onPath(path);
    }).catch((error) => onError(error.message || 'Не удалось принять файл.'));
  });
}

function hasFiles(event) {
  const types = event.dataTransfer?.types;
  return Boolean(types && [...types].includes('Files'));
}

/** @param {DragEvent} event @returns {Promise<string>} */
async function acceptDroppedModel(event) {
  const files = [...(event.dataTransfer?.files || [])];
  const file = files.find((item) => item.name.toLowerCase().endsWith('.gguf')) || files[0];
  if (!file) throw new Error('Файл не попал в поле.');
  if (!file.name.toLowerCase().endsWith('.gguf')) {
    throw new Error('Нужен файл с расширением .gguf.');
  }
  const webviewHost = window.chrome?.webview;
  if (!webviewHost?.postMessageWithAdditionalObjects) {
    throw new Error('Перетаскивание работает в окне приложения. Иначе откройте проводник кнопкой.');
  }
  webviewHost.postMessageWithAdditionalObjects('FilesDropped', event.dataTransfer.files);
  const [path, err] = await tryCall('claimDroppedFile', file.name);
  if (err) throw err;
  if (!path) throw new Error('Путь файла не прочитался. Выберите его кнопкой.');
  return String(path);
}

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

      const fileRow = el('div', { class: 'model-import__file-row' }, [
        pathInput,
        button({ label: 'Выбрать файл', variant: 'ghost', size: 'sm', onClick: () => void pickFile() }),
      ]);
      pathInput.addEventListener('click', () => void pickFile());
      bindModelDrop(fileRow, (path) => {
        pathInput.value = path;
        showError('');
      }, showError);
      body.append(
        el('div', { class: 'field' }, [
          el('label', { class: 'field__label', text: 'Файл GGUF' }),
          fileRow,
          el('p', {
            class: 'field__hint',
            text: 'Кнопка открывает проводник. Сюда же можно перетащить файл .gguf. Копия проверяется по SHA-256.',
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

  /** Выбор GGUF-файла через проводник. Отмена диалога путь не меняет. */
  async function pickFile() {
    const [path, err] = await tryCall('resolveGGUFPath');
    if (err) {
      showError(err.message);
      return;
    }
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
    addModelButton(),
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
      paintMarket();
      rerender();
    }),
  ];
}

function destroy() {
  closeAddMenu();
  unsubs.forEach((unsub) => unsub());
  unsubs = [];
  hostRef = null;
  loaded = false;
  marketBody = null;
  marketDialog = null;
  resumeLink = false;
}

/* -------------------------------------------------------------------------
 * Регистрация
 * ------------------------------------------------------------------------- */

router.registerPage('models', {
  title: 'Модели',
  subtitle: 'Модель, которой переводим на этом компьютере.',
  help: 'Каталог - файлы на диске. «+» внизу списка открывает рынок: закреплённый список с Hugging Face, скачивание только после согласия. «Добавить модель» копирует локальный GGUF или берёт файл из этого списка по ссылке. Подбор смотрит память и место, не качество перевода.',
  render,
  destroy,
  actions,
});
