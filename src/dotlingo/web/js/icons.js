/**
 * Inline SVG stroke-иконки: 1.5px stroke, round caps, 24x24 viewBox.
 * Иконки только stroke, без fill - наследуют currentColor.
 */

const PATHS = {
  folder:
    '<path d="M3.5 7.5c0-1.1.9-2 2-2h3.2c.6 0 1.2.3 1.6.8l.9 1.2h6.3c1.1 0 2 .9 2 2v8c0 1.1-.9 2-2 2h-12c-1.1 0-2-.9-2-2v-10z"/>',
  document:
    '<path d="M7 3.5h7l4 4v13H7z"/>' +
    '<path d="M14 3.5V7.5h4"/>' +
    '<path d="M10 12.5h5M10 16h5"/>',
  search:
    '<circle cx="11" cy="11" r="6.5"/>' +
    '<path d="M15.8 15.8L21 21"/>',
  list:
    '<path d="M9 6.5h11M9 12h11M9 17.5h11"/>' +
    '<path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
  chip:
    '<rect x="6.5" y="6.5" width="11" height="11" rx="2.5"/>' +
    '<path d="M10 3.5v3M14 3.5v3M10 17.5v3M14 17.5v3M3.5 10h3M3.5 14h3M17.5 10h3M17.5 14h3"/>',
  book:
    '<path d="M4.5 5.5c0-1.1.9-2 2-2H19v16H6.5c-1.1 0-2 .9-2 2v-16z"/>' +
    '<path d="M4.5 19.5c0-1.1.9-2 2-2H19"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/>' +
    '<path d="M12 2.8v3M12 18.2v3M2.8 12h3M18.2 12h3M5.5 5.5l2.1 2.1M16.4 16.4l2.1 2.1M18.5 5.5l-2.1 2.1M7.6 16.4l-2.1 2.1"/>',
  sun:
    '<circle cx="12" cy="12" r="4"/>' +
    '<path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4"/>',
  moon:
    '<path d="M20 13.5A8 8 0 0 1 10.5 4 8 8 0 1 0 20 13.5z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  'chevron-down': '<path d="M6 9.5l6 6 6-6"/>',
  'chevron-right': '<path d="M9.5 6l6 6-6 6"/>',
  download: '<path d="M12 4v11M7.5 10.5l4.5 4.5 4.5-4.5"/><path d="M4.5 19.5h15"/>',
  upload: '<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5"/><path d="M4.5 19.5h15"/>',
  pause: '<path d="M9 5.5v13M15 5.5v13"/>',
  play: '<path d="M8 5.5l11 6.5-11 6.5z"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/>',
  trash:
    '<path d="M4.5 6.5h15"/>' +
    '<path d="M9 6.5V5c0-.8.7-1.5 1.5-1.5h3c.8 0 1.5.7 1.5 1.5v1.5"/>' +
    '<path d="M6.5 6.5l.8 12c.1 1 .9 1.5 1.7 1.5h6c.8 0 1.6-.5 1.7-1.5l.8-12"/>' +
    '<path d="M10 10.5v6M14 10.5v6"/>',
  edit:
    '<path d="M4.5 19.5h4l10-10-4-4-10 10z"/>' +
    '<path d="M13.5 6.5l4 4"/>',
  export:
    '<path d="M14 4.5h5.5V10"/>' +
    '<path d="M19.5 4.5l-8 8"/>' +
    '<path d="M18 14v4.5c0 1.1-.9 2-2 2h-11c-1.1 0-2-.9-2-2v-11c0-1.1.9-2 2-2H10"/>',
  refresh:
    '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3L19.5 9"/>' +
    '<path d="M19.5 4v5h-5"/>',
  warning:
    '<path d="M12 4L2.8 19.5h18.4z"/>' +
    '<path d="M12 10v4.5M12 17.5h.01"/>',
  error:
    '<circle cx="12" cy="12" r="8.5"/>' +
    '<path d="M9 9l6 6M15 9l-6 6"/>',
  info:
    '<circle cx="12" cy="12" r="8.5"/>' +
    '<path d="M12 11v5M12 8h.01"/>',
  'arrow-right': '<path d="M4.5 12h15M13.5 6l6 6-6 6"/>',
  message:
    '<path d="M5 6.5h14a1.5 1.5 0 0 1 1.5 1.5v7.2a1.5 1.5 0 0 1-1.5 1.5H9.2L5 20.2v-3.5A1.5 1.5 0 0 1 3.5 15.2V8A1.5 1.5 0 0 1 5 6.5z"/>',
  globe:
    '<circle cx="12" cy="12" r="8.5"/>' +
    '<path d="M3.5 12h17"/>' +
    '<path d="M12 3.5c2.5 2.3 3.8 5.2 3.8 8.5s-1.3 6.2-3.8 8.5c-2.5-2.3-3.8-5.2-3.8-8.5s1.3-6.2 3.8-8.5z"/>',
};

/** Общий шаблон SVG-элемента. */
function svgWrap(inner) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.5');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  svg.innerHTML = inner;
  return svg;
}

/**
 * Возвращает клон SVG-иконки по имени.
 * @param {string} name
 * @returns {SVGElement}
 */
export function icon(name) {
  const path = PATHS[name];
  if (!path) {
    console.warn(`[icons] неизвестная иконка: ${name}`);
    return svgWrap(PATHS.info);
  }
  return svgWrap(path);
}

/** Список доступных имён (для отладки). */
export const iconNames = Object.keys(PATHS);
