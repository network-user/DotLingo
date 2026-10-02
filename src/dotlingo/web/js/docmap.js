/**
 * Спектр документа - фирменная карта DotLingo.
 *
 * Каждый блок документа - тонкий сегмент полосы: переведённые светятся,
 * нетранслируемые - волосяные штрихи, границы секций - просветы.
 * Вертикальная карта работает навигатором в редакторе, горизонтальная
 * («штрих-код перевода») показывает весь документ одним взглядом.
 */

import { el } from './components.js';

/** Максимум сегментов вертикальной карты (длинные документы корзинятся). */
const MAX_VERTICAL_SEGMENTS = 320;

/**
 * Построить спектр документа.
 * @param {object} opts
 * @param {Array<object>} [opts.blocks] - блоки документа (order, text, translatable, section).
 * @param {Object<string, Object<string, string>>} [opts.translations] - переводы по языкам.
 * @param {string} [opts.lang] - целевой язык.
 * @param {'v'|'h'} [opts.orientation='v'] - вертикальная (навигатор) или горизонтальная.
 * @param {(block: object) => void} [opts.onNavigate] - клик по сегменту.
 * @returns {{ root: HTMLElement, setCurrent: (order: number|string|null) => void,
 *            update: (patch: object) => void }}
 */
export function spectrum(opts = {}) {
  let { blocks = [], translations = {}, lang = '', onNavigate = null } = opts;
  const orientation = opts.orientation === 'h' ? 'h' : 'v';
  let currentOrder = null;

  const root = el('div', {
    class: `spectrum spectrum--${orientation}`,
    role: 'img',
    'aria-label': 'Карта перевода документа',
  });

  /** Переведён ли блок в текущем языке. */
  const isTranslated = (block) =>
    Boolean((translations[lang]?.[String(block.order)] || '').trim());

  /** Высота вертикального сегмента - грубая мера длины блока. */
  const segHeight = (block) =>
    Math.min(14, Math.max(3, Math.round((block.text || '').length / 80)));

  /**
   * Свернуть блоки в корзины, если их слишком много.
   * Корзина наследует order первого блока - навигация остаётся рабочей.
   */
  function buckets() {
    if (orientation === 'v' && blocks.length > MAX_VERTICAL_SEGMENTS) {
      const size = Math.ceil(blocks.length / MAX_VERTICAL_SEGMENTS);
      const out = [];
      for (let start = 0; start < blocks.length; start += size) {
        const chunk = blocks.slice(start, start + size);
        out.push({
          order: chunk[0].order,
          section: chunk[0].section,
          text: chunk.map((b) => b.text || '').join(' '),
          translatable: chunk.some((b) => b.translatable),
          _ratio: chunk.filter(isTranslated).length / Math.max(1, chunk.filter((b) => b.translatable).length),
        });
      }
      return out;
    }
    return blocks;
  }

  /** Перерисовать сегменты. */
  function render() {
    const nodes = [];
    let prevSection = null;
    for (const block of buckets()) {
      const translated = block._ratio != null ? block._ratio : isTranslated(block) ? 1 : 0;
      const segment = el('div', {
        class: 'spectrum__seg',
        dataset: { order: String(block.order) },
      });
      if (block.translatable) {
        segment.classList.add('spectrum__seg--text');
        segment.style.setProperty('--lit', String(translated));
        if (translated > 0) segment.classList.add('is-translated');
      } else {
        segment.classList.add('spectrum__seg--gap');
      }
      if (block._ratio != null) segment.dataset.shared = 'true';
      if (orientation === 'v') {
        segment.style.height = `${segHeight(block)}px`;
        if (prevSection != null && block.section !== prevSection) {
          segment.classList.add('spectrum__seg--section');
        }
        segment.title = (block.text || '').replace(/\s+/g, ' ').slice(0, 90);
        if (onNavigate) {
          segment.addEventListener('click', () => onNavigate(block));
        }
      }
      prevSection = block.section;
      nodes.push(segment);
    }
    root.replaceChildren(...nodes);
    markCurrent();
  }

  /** Подсветить текущий блок. */
  function markCurrent() {
    root.querySelectorAll('.spectrum__seg').forEach((segment) => {
      segment.classList.toggle('is-current', segment.dataset.order === currentOrder);
    });
  }

  render();

  return {
    root,
    /** @param {number|string|null} order */
    setCurrent(order) {
      currentOrder = order == null ? null : String(order);
      markCurrent();
    },
    /**
     * Обновить один блок, не пересобирая карту.
     * Корзина из нескольких блоков перерисовывается целиком.
     * @param {number|string} order
     * @param {boolean} translated
     */
    touch(order, translated) {
      const segment = root.querySelector(
        `.spectrum__seg[data-order="${CSS.escape(String(order))}"]`,
      );
      if (!segment || segment.dataset.shared === 'true') {
        render();
        return;
      }
      if (!segment.classList.contains('spectrum__seg--text')) return;
      segment.style.setProperty('--lit', translated ? '1' : '0');
      segment.classList.toggle('is-translated', Boolean(translated));
    },
    /** @param {object} patch - { blocks?, translations?, lang? } */
    update(patch = {}) {
      if (patch.blocks) blocks = patch.blocks;
      if (patch.translations) translations = patch.translations;
      if (patch.lang !== undefined) lang = patch.lang;
      render();
    },
  };
}

/**
 * Горизонтальный штрих-код из готового спектра (значения 0..1, -1 - пропуск).
 * Используется там, где блоков нет под рукой - например, в списке документов.
 * @param {number[]} values
 * @returns {HTMLElement}
 */
/** Цвет одного деления штрих-кода. Совпадает с opacity текстовых сегментов. */
function tickColor(value) {
  if (value < 0) return 'var(--hairline)';
  const opacity = value > 0 ? 0.32 + Math.min(1, value) * 0.68 : 0.1;
  const percent = Math.round(opacity * 100);
  return `color-mix(in srgb, var(--text) ${percent}%, transparent)`;
}

export function barcode(values) {
  const root = el('div', { class: 'spectrum spectrum--h', 'aria-hidden': 'true' });
  const list = Array.isArray(values) ? values : [];
  if (list.length === 0) return root;
  const stops = [];
  const step = 100 / list.length;
  for (let index = 0; index < list.length; index += 1) {
    const start = (index * step).toFixed(4);
    const end = ((index + 1) * step).toFixed(4);
    const color = tickColor(Number(list[index]));
    stops.push(`${color} ${start}%`, `${color} ${end}%`);
  }
  root.style.backgroundImage = `linear-gradient(90deg, ${stops.join(', ')})`;
  return root;
}
