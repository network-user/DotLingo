/**
 * Масштаб окна и размер букв. Оба значения в процентах, шаг 1.
 * Масштаб зумит всю вёрстку. Размер текста умножает только кегль.
 */

export const UI_SCALE_MIN = 75;
export const UI_SCALE_MAX = 160;
export const TEXT_SCALE_MIN = 75;
export const TEXT_SCALE_MAX = 180;
export const SCALE_DEFAULT = 100;

/** @param {unknown} value @param {number} min @param {number} max @param {number} fallback */
export function clampPercent(value, min, max, fallback = SCALE_DEFAULT) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(max, Math.max(min, Math.round(number)));
}

/** @param {number} uiScale @param {number} textScale */
export function applyMetrics(uiScale, textScale) {
  const root = document.documentElement;
  const ui = clampPercent(uiScale, UI_SCALE_MIN, UI_SCALE_MAX);
  const text = clampPercent(textScale, TEXT_SCALE_MIN, TEXT_SCALE_MAX);
  root.style.setProperty('--ui-scale', String(ui / 100));
  root.style.setProperty('--text-scale', String(text / 100));
}
