/**
 * Мастер первого запуска: проверка устройства → рекомендация → выбор модели →
 * загрузка → готово. Показывается один раз (preferences.setup_seen).
 */

import { call, tryCall } from '../bridge.js';
import * as store from '../store.js';
import * as router from '../router.js';
import { el, button, badge, toast, spinner, progressBar, formatBytes } from '../components.js';
import { downloadFlow } from './models.js';

const STEP_COUNT = 5;

/** @type {HTMLElement|null} корень оверлея */
let overlayRoot = null;

/** @type {Array<() => void>} отписки событий */
let unsubs = [];

/** Показывал ли мастер в этой сессии (защита от повторного запуска). */
let shownThisSession = false;

/* -------------------------------------------------------------------------
 * Публичная точка входа (вызывается из index.js)
 * ------------------------------------------------------------------------- */

export async function setupWizard() {
  if (shownThisSession) return;
  const [prefs] = await tryCall('getPreferences');
  if (!prefs || prefs.setup_seen) return;
  shownThisSession = true;
  showStep(0);
}

/* -------------------------------------------------------------------------
 * Рендер оверлея и шагов
 * ------------------------------------------------------------------------- */

/** Закрыть оверлей и снять подписки. */
function closeWizard() {
  unsubs.forEach((off) => off());
  unsubs = [];
  overlayRoot?.remove();
  overlayRoot = null;
  document.removeEventListener('keydown', blockEscape, true);
}

/** Esc не должен закрывать мастер (шаг загрузки блокирует закрытие). */
function blockEscape(e) {
  if (e.key === 'Escape' && overlayRoot) {
    e.stopPropagation();
    e.preventDefault();
  }
}

/**
 * Показать шаг мастера.
 * @param {number} step - 0..4
 * @param {object} [extra] - контекст между шагами (выбранная модель и т.п.).
 */
function showStep(step, extra = {}) {
  if (overlayRoot) {
    overlayRoot.remove();
    document.removeEventListener('keydown', blockEscape, true);
  }

  const card = el('div', { class: 'wizard__card', role: 'dialog', 'aria-modal': 'true' });
  const body = el('div', { class: 'wizard__body' });
  const footer = el('footer', { class: 'wizard__footer' });

  overlayRoot = el('div', { class: 'wizard' }, [
    el('div', { class: 'wizard__progress-wrap' }, [
      el('span', { class: 'wizard__step-label', text: `Шаг ${step + 1} из ${STEP_COUNT}` }),
      stepProgress(step),
    ]),
    card,
  ]);
  card.append(body, footer);
  document.body.appendChild(overlayRoot);
  document.addEventListener('keydown', blockEscape, true);

  renderers[step]?.(body, footer, extra);
}

/** Тонкий индикатор прогресса шагов. */
function stepProgress(step) {
  const api = progressBar(step / STEP_COUNT);
  api.root.classList.add('wizard__progress');
  return api.root;
}

/** Завершение мастера. */
async function finishWizard() {
  await call('setPreferences', { setup_seen: true }).catch(() => {});
  closeWizard();
  router.showPage('projects');
}

/** Отложить мастер (кроме шага загрузки). */
async function postponeWizard() {
  await call('setPreferences', { setup_seen: true }).catch(() => {});
  closeWizard();
  toast('Мастер можно пройти позже на странице «Настройки».', 'info');
}

/* -------------------------------------------------------------------------
 * Тексты шагов
 * ------------------------------------------------------------------------- */

const renderers = [
  // Шаг 1: проверка устройства
  (body, footer) => {
    body.append(
      el('h2', { class: 'wizard__title', text: 'Проверка устройства' }),
      el('p', {
        class: 'wizard__text',
        text: 'DotLingo проверит RAM, диск и локальный runtime. Модель подбирается по ресурсам устройства.',
      }),
      el('div', { class: 'wizard__wait', id: 'wizard-hw-wait' }, [
        spinner(),
        el('span', { text: 'Проверяем устройство…' }),
      ]),
    );
    const next = button({ label: 'Продолжить', variant: 'primary', disabled: true });
    const postpone = button({ label: 'Отложить', onClick: () => postponeWizard() });
    footer.append(postpone, next);

    // Устройство могло быть проверено ещё до мастера.
    if (store.get('hardware')) {
      next.disabled = false;
      document.getElementById('wizard-hw-wait')?.remove();
      next.addEventListener('click', () => showStep(1));
      return;
    }
    call('detectHardware').catch(() => {});
    unsubs.push(
      store.on('hardware_detected', () => {
        document.getElementById('wizard-hw-wait')?.remove();
        next.disabled = false;
      })
    );
    next.addEventListener('click', () => showStep(1));
  },

  // Шаг 2: рекомендация
  (body, footer) => {
    const hw = store.get('hardware');
    const recommendation = store.get('recommendation');
    const model = recommendation?.id
      ? store.get('models').find((m) => m.id === recommendation.id)
      : null;

    body.append(
      el('h2', { class: 'wizard__title', text: 'Рекомендация для этого устройства' }),
      model
        ? el('div', { class: 'wizard__recommend' }, [
            el('strong', { text: model.name }),
            el('span', { class: 'wizard__muted', text: ` · ${model.sizeLabel}` }),
            el('p', { class: 'wizard__text', text: recommendation?.reason || '' }),
          ])
        : el('p', {
            class: 'wizard__text',
            text: 'Подходящая модель не найдена. Можно продолжить без установки и добавить модель позже.',
          }),
      el('p', {
        class: 'wizard__text wizard__muted',
        text: 'Установка модели необязательна: документы и проекты доступны сразу. Подбор оценивает размещение по ресурсам, не качество перевода.',
      }),
    );
    if (hw) body.append(hardwareSummary(hw));

    footer.append(
      button({ label: 'Назад', onClick: () => showStep(0) }),
      model
        ? button({ label: 'Выбрать модель', variant: 'primary', onClick: () => showStep(2) })
        : button({ label: 'Пропустить', variant: 'primary', onClick: () => showStep(4) })
    );
  },

  // Шаг 3: выбор модели и лицензии
  (body, footer) => {
    const models = store
      .get('models')
      .filter((m) => m.installState === 'available' || m.installState === 'installed');
    let selected =
      store.get('recommendation')?.id && models.some((m) => m.id === store.get('recommendation').id)
        ? store.get('recommendation').id
        : models[0]?.id;
    let consent = false;

    body.append(
      el('h2', { class: 'wizard__title', text: 'Выбор модели' }),
      el('p', {
        class: 'wizard__text',
        text: 'Загружается закреплённая ревизия; размер и SHA-256 проверяются после скачивания.',
      }),
    );

    const list = el('div', { class: 'wizard__models' });
    const consentRow = el('label', { class: 'wizard__consent' });
    const error = el('p', { class: 'wizard__error', hidden: true });

    /** Перерисовать список и согласие. */
    const render = () => {
      list.replaceChildren();
      for (const model of models) {
        const row = el('label', { class: `wizard__model${model.id === selected ? ' is-selected' : ''}` }, [
          el('input', {
            type: 'radio',
            name: 'wizard-model',
            checked: model.id === selected,
            onChange: () => {
              selected = model.id;
              consent = false;
              error.hidden = true;
              render();
            },
          }),
          el('span', { class: 'wizard__model-name', text: model.name }),
          el('span', { class: 'wizard__model-meta', text: model.sizeLabel }),
          badge({
            label:
              model.installState === 'installed'
                ? 'Установлена'
                : model.estimatedRamGb
                  ? `≈ ${model.estimatedRamGb} ГБ RAM`
                  : '',
            tone: model.installState === 'installed' ? 'success' : 'muted',
          }),
        ]);
        list.append(row);
      }

      const chosen = models.find((m) => m.id === selected);
      consentRow.replaceChildren();
      if (chosen && chosen.installState !== 'installed') {
        consentRow.append(
          el('input', {
            type: 'checkbox',
            checked: consent,
            onChange: (e) => {
              consent = e.target.checked;
              error.hidden = true;
            },
          }),
          el('span', {
            text: `Ознакомился с лицензией «${chosen.license}» и согласен скачать ${chosen.sizeLabel}.`,
          }),
        );
      }
    };
    render();
    body.append(list, consentRow, error);

    footer.append(
      button({ label: 'Назад', onClick: () => showStep(1) }),
      button({ label: 'Отложить установку', onClick: () => showStep(4) }),
      button({
        label: 'Скачать выбранную',
        variant: 'primary',
        onClick: async () => {
          const chosen = models.find((m) => m.id === selected);
          if (!chosen) return;
          if (chosen.installState === 'installed') {
            toast('Модель уже установлена.', 'success');
            showStep(4);
            return;
          }
          if (!consent) {
            error.textContent = 'Отметьте согласие с лицензией, чтобы продолжить.';
            error.hidden = false;
            return;
          }
          showStep(3, { model: chosen });
        },
      })
    );
  },

  // Шаг 4: загрузка
  (body, footer, extra) => {
    const model = extra.model;
    body.append(
      el('h2', { class: 'wizard__title', text: `Загрузка · ${model?.name ?? ''}` }),
      el('p', {
        class: 'wizard__text',
        text: 'Файл проверяется по размеру и SHA-256 перед активацией. Не закрывайте приложение.',
      }),
    );
    footer.append(
      button({
        label: 'Отменить загрузку',
        onClick: async () => {
          const [data] = await tryCall('cancelDownload');
          if (data && data.accepted === false) {
            toast('Модель уже активируется. Дождитесь завершения.', 'warning');
          }
        },
      })
    );
    if (model) {
      // downloadFlow рисует собственную модалку поверх мастера; по завершении
      // (успех или ошибка) переходим дальше.
      const offDone = store.on('download_done', (payload) => {
        if (payload.modelId !== model.id) return;
        offDone();
        if (payload.ok) showStep(4);
        else {
          toast(payload.error || 'Загрузка не удалась.', 'error');
          showStep(2);
        }
      });
      unsubs.push(offDone);
      downloadFlow(model);
    }
  },

  // Шаг 5: готово
  (body, footer) => {
    const hw = store.get('hardware');
    const runtime = hw?.llamaRuntimeAvailable;
    body.append(
      el('h2', { class: 'wizard__title', text: 'DotLingo готов' }),
      el('p', {
        class: 'wizard__text',
        text: 'Исходники, проекты и веса моделей хранятся отдельно. Перевод выполняется локально.',
      }),
      el('p', {
        class: 'wizard__text wizard__muted',
        text: runtime
          ? 'Runtime llama.cpp найден.'
          : 'Runtime llama.cpp не найден в текущем окружении. Для перевода нужен установленный runtime и проверенная модель.',
      }),
      el('p', {
        class: 'wizard__text wizard__muted',
        text: 'OCR для сканированных PDF не устанавливается автоматически.',
      }),
    );
    footer.append(
      button({ label: 'Готово', variant: 'primary', onClick: () => finishWizard() })
    );
  },
];

/** Краткая сводка устройства для шага 2. */
function hardwareSummary(hw) {
  const rows = [
    ['Устройство', hw.profile],
    ['CPU', `${hw.cpuThreads} потоков`],
    ['RAM', `${formatBytes(hw.ramTotalGb * 1024 ** 3)} всего · доступно ${formatBytes(hw.ramAvailableGb * 1024 ** 3)}`],
    ['Диск', `свободно ${formatBytes(hw.diskFreeGb * 1024 ** 3)}`],
    ['GPU', hw.gpuNames?.length ? hw.gpuNames.join(', ') : 'не найден'],
    ['Runtime', hw.llamaRuntimeAvailable ? 'llama.cpp доступен' : 'llama.cpp не найден'],
  ];
  return el('div', { class: 'wizard__hw' }, [
    el('h3', { class: 'wizard__hw-title', text: 'Устройство' }),
    ...rows.map(([label, value]) =>
      el('div', { class: 'wizard__hw-row' }, [
        el('span', { class: 'wizard__hw-label', text: label }),
        el('span', { text: value }),
      ])
    ),
  ]);
}

// Мастер запускается самостоятельно (первый импорт из index.js).
setupWizard().catch((e) => console.error('[wizard]', e));
