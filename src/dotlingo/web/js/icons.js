/**
 * Общие штриховые иконки, 24×24.
 * Обводка 1.75, круглые концы. Точки залиты, а не нарисованы штрихом нулевой длины.
 * Имена стабильны: страницы и кнопки просят глиф по имени.
 */

const PATHS = {
  folder:
    '<path d="M4 19.45V5.35h6.35l2.2 3.55H20v10.55z"/>',
  document:
    '<path d="M6.35 3.75h7.35L18.35 8.65V20.45H6.35z"/>' +
    '<path d="M13.7 3.75V8.65h4.65"/>' +
    '<path d="M9.05 12.25h5.6M9.05 15.7h3.85"/>',
  search:
    '<circle cx="10.55" cy="10.55" r="6.1"/>' +
    '<path d="M16.7 16.7 20.65 20.65"/>',
  list:
    '<circle cx="4.85" cy="6.75" r="1.35" fill="currentColor" stroke="none"/>' +
    '<circle cx="4.85" cy="12" r="1.35" fill="currentColor" stroke="none"/>' +
    '<circle cx="4.85" cy="17.25" r="1.35" fill="currentColor" stroke="none"/>' +
    '<path d="M8.35 6.75H19.6M8.35 12H19.6M8.35 17.25H19.6"/>',
  chip:
    '<rect x="6.65" y="6.65" width="10.7" height="10.7" rx="2.2"/>' +
    '<path d="M12 3.45V6.65M12 17.35v3.2M3.45 12H6.65M17.35 12h3.2"/>',
  book:
    '<path d="M3.23 6.53Q6.83 5.33 10.58 8.78V17.33Q6.68 17.03 3.23 18.53Z"/>' +
    '<path d="M20.78 6.53Q17.18 5.33 13.43 8.78V17.33Q17.33 17.03 20.78 18.53Z"/>',
  settings:
    '<path d="M4 7.2h16M4 12h16M4 16.8h16"/>' +
    '<circle cx="9.05" cy="7.2" r="2.05" fill="currentColor" stroke="none"/>' +
    '<circle cx="15.05" cy="12" r="2.05" fill="currentColor" stroke="none"/>' +
    '<circle cx="11.15" cy="16.8" r="2.05" fill="currentColor" stroke="none"/>',
  sun:
    '<circle cx="12" cy="12" r="4"/>' +
    '<path d="M12 3.15v2.4M12 18.45v2.4M3.15 12h2.4M18.45 12h2.4"/>' +
    '<path d="M5.7 5.7 7.4 7.4M16.6 16.6l1.7 1.7M18.3 5.7 16.6 7.4M7.4 16.6 5.7 18.3"/>',
  moon:
    '<path d="M15.15 4.05c-1.15 1.35-1.8 3.05-1.8 4.95 0 4.35 3.15 7.7 7.05 8.35A8.05 8.05 0 1 1 15.15 4.05z"/>',
  plus: '<path d="M12 4.75v14.5M4.75 12h14.5"/>',
  close: '<path d="M6.85 6.85 17.15 17.15M17.15 6.85 6.85 17.15"/>',
  check: '<path d="M5.35 12.4 9.95 16.95 18.85 7.35"/>',
  'chevron-down': '<path d="M6.5 9.25 12 14.75l5.5-5.5"/>',
  'chevron-left': '<path d="M14.75 6.5 9.25 12l5.5 5.5"/>',
  'chevron-right': '<path d="M9.25 6.5 14.75 12l-5.5 5.5"/>',
  download:
    '<path d="M12 3.65V13.85"/>' +
    '<path d="M7.7 9.6 12 13.85 16.3 9.6"/>' +
    '<path d="M4.75 16.15v2.15c0 .95.8 1.7 1.75 1.7h10.9c.95 0 1.75-.75 1.75-1.7v-2.15"/>',
  upload:
    '<path d="M12 13.85V3.65"/>' +
    '<path d="M7.7 7.9 12 3.65 16.3 7.9"/>' +
    '<path d="M4.75 16.15v2.15c0 .95.8 1.7 1.75 1.7h10.9c.95 0 1.75-.75 1.75-1.7v-2.15"/>',
  pause:
    '<rect x="6.2" y="5.35" width="3.35" height="13.3" rx="1.15" fill="currentColor" stroke="none"/>' +
    '<rect x="14.45" y="5.35" width="3.35" height="13.3" rx="1.15" fill="currentColor" stroke="none"/>',
  play:
    '<path d="M8.85 5.65 18.25 12 8.85 18.35z" fill="currentColor" stroke="none"/>',
  stop: '<rect x="6.55" y="6.55" width="10.9" height="10.9" rx="2"/>',
  trash:
    '<path d="M9.35 6.85V5.6c0-.5.4-.95.95-.95h3.4c.55 0 .95.45.95.95v1.25"/>' +
    '<path d="M4.8 6.85h14.4"/>' +
    '<path d="M7.15 6.85l.85 11.15c.05.7.6 1.2 1.3 1.2h5.4c.7 0 1.25-.5 1.3-1.2l.85-11.15"/>',
  edit:
    '<path d="M13.55 6.15 17.85 10.45 8.65 19.65H4.35v-4.3z"/>' +
    '<path d="M11.25 8.45 15.55 12.75"/>',
  export:
    '<path d="M5.2 9.2V17.7c0 1 .75 1.75 1.75 1.75h8.5c1 0 1.75-.75 1.75-1.75V13.5"/>' +
    '<path d="M5.2 9.2h6.15"/>' +
    '<path d="M13.15 4.55h6.3v6.3"/>' +
    '<path d="M19.2 4.8 12.35 11.65"/>',
  refresh:
    '<path d="M19.2 12A7.2 7.2 0 1 1 15.6 5.76"/>' +
    '<path d="M15.6 5.76 17.95 7.12 17.05 4.2" stroke-linejoin="miter"/>',
  warning:
    '<path d="M12 4.1 3.3 19.4h17.4z"/>' +
    '<path d="M12 9.55v4.15"/>' +
    '<circle cx="12" cy="16.45" r="1.15" fill="currentColor" stroke="none"/>',
  error:
    '<circle cx="12" cy="12" r="8"/>' +
    '<path d="M8.8 8.8 15.2 15.2M15.2 8.8 8.8 15.2"/>',
  info:
    '<circle cx="12" cy="12" r="8"/>' +
    '<path d="M12 11v4.75"/>' +
    '<circle cx="12" cy="8.05" r="1.15" fill="currentColor" stroke="none"/>',
  help:
    '<circle cx="12" cy="12" r="8"/>' +
    '<path d="M9.55 9.4a2.45 2.45 0 0 1 4.55 1.25c0 1.15-.75 1.7-1.6 2.15-.5.28-.65.52-.65 1v.3"/>' +
    '<circle cx="12" cy="16.35" r="0.9" fill="currentColor" stroke="none"/>',
  'arrow-right':
    '<path d="M4.35 12H18.65"/>' +
    '<path d="M13.1 6.65 18.65 12 13.1 17.35"/>',
  message:
    '<path d="M7.05 6.3h10.15A1.85 1.85 0 0 1 19.05 8.15v6.2a1.85 1.85 0 0 1-1.85 1.85H9.45L5.2 19.9V8.15A1.85 1.85 0 0 1 7.05 6.3z"/>',
  globe:
    '<circle cx="12" cy="12" r="8"/>' +
    '<path d="M4 12h16"/>' +
    '<ellipse cx="12" cy="12" rx="3.45" ry="8"/>',
  clock:
    '<circle cx="12" cy="12" r="8"/>' +
    '<path d="M12 7.35V12l3.15 2.05"/>',
};

/** Общий шаблон SVG-элемента. */
function svgWrap(inner) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.75');
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
