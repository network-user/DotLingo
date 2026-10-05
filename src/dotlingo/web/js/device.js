/**
 * Факты устройства и подбор модели.
 * Один источник для первого запуска и страницы «Настройки».
 */

import { tryCall } from './bridge.js';
import { formatBytes } from './components.js';
import * as store from './store.js';

/**
 * Доля занятой RAM, 0..1. null, если объём неизвестен.
 * @param {object|null} hw
 * @returns {number|null}
 */
export function ramUsedFraction(hw) {
  if (!hw || !Number.isFinite(hw.ramTotalGb) || hw.ramTotalGb <= 0) return null;
  const used = (hw.ramTotalGb - hw.ramAvailableGb) / hw.ramTotalGb;
  if (!Number.isFinite(used)) return null;
  return Math.min(1, Math.max(0, used));
}

/**
 * @param {number|null|undefined} gb
 * @returns {string}
 */
export function formatGb(gb) {
  if (!Number.isFinite(gb)) return '—';
  return formatBytes(gb * 1024 ** 3);
}

/** Модели, которые можно скачать или которые уже лежат на диске. */
export function catalogModels() {
  return (store.get('models') || []).filter(
    (model) => model.installState === 'available' || model.installState === 'installed',
  );
}

/** Файл модели уже на диске. Карточка может прийти с installed или только с installState. */
export function isInstalledModel(model) {
  return model?.installed === true || model?.installState === 'installed';
}

/** На диске уже есть хотя бы один вес. */
export function hasInstalledModel() {
  return (store.get('models') || []).some(isInstalledModel);
}

/**
 * Рекомендация каталога после проверки устройства.
 * @returns {{ model: object|null, reason: string, models: object[] }}
 */
export function recommendedChoice() {
  const models = catalogModels();
  const recommendation = store.get('recommendation');
  const model = recommendation?.id
    ? models.find((item) => item.id === recommendation.id) ?? null
    : null;
  return { model, reason: recommendation?.reason || '', models };
}

/** Вес не помещается по диску или по всей RAM устройства. */
export function placementBlocked(model) {
  return model?.compatibility?.verdict === 'no';
}

/** Потоки CPU и когда слои уходят на GPU. */
export function threadNote() {
  return 'Потоки CPU: число логических процессоров минус один, не больше 8. Слои уходят на GPU, только если свободной VRAM хватает на расчёт модели. Иначе перевод идёт на CPU.';
}

/**
 * Строка GPU. Проверка устройства смотрит только NVIDIA.
 * @param {object|null} hw
 * @returns {string}
 */
export function gpuSummary(hw) {
  const names = hw?.gpuNames ?? [];
  if (!names.length) return 'не обнаружен';
  return names
    .map((name, index) => {
      const vram = hw?.gpuVramGb?.[index];
      const free = hw?.gpuVramFreeGb?.[index];
      const parts = [name];
      if (Number.isFinite(vram) && vram > 0) parts.push(`VRAM ${formatBytes(vram * 1024 ** 3)}`);
      if (Number.isFinite(free) && free >= 0) parts.push(`свободно ${formatBytes(free * 1024 ** 3)}`);
      return parts.join(', ');
    })
    .join('; ');
}

/**
 * @param {number} bps
 * @returns {string}
 */
export function formatSpeed(bps) {
  if (!Number.isFinite(bps) || bps <= 0) return '';
  return `${formatBytes(bps)}/с`;
}

/**
 * Грубая оценка остатка. Пустая строка, если скорость или размер неизвестны.
 * @param {number} bytes
 * @param {number} total
 * @param {number} bps
 * @returns {string}
 */
export function formatEta(bytes, total, bps) {
  if (!Number.isFinite(bps) || bps <= 0 || !Number.isFinite(total) || total <= bytes) return '';
  const seconds = Math.round((total - bytes) / bps);
  if (seconds < 60) return 'меньше минуты';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
}

/** Цепочка, чтобы поздний ответ listModels не затирал более новую рекомендацию. */
let catalogTail = Promise.resolve();

/** @type {string} */
let catalogError = '';

async function loadCatalog() {
  const [data, error] = await tryCall('listModels');
  if (!data) {
    catalogError = error?.message || 'Не удалось прочитать каталог.';
    store.set('modelsLoaded', true);
    return false;
  }
  catalogError = '';
  store.patch({
    models: data.models ?? [],
    recommendation: data.recommendation ?? null,
    modelsLoaded: true,
  });
  store.emit('models_refreshed', data);
  return true;
}

/** Текст последней ошибки каталога, если обновление не удалось. */
export function catalogFailure() {
  return catalogError;
}

/** Обновить каталог и рекомендацию после проверки устройства или загрузки. */
export function refreshCatalog() {
  const run = catalogTail.then(loadCatalog, loadCatalog);
  catalogTail = run.then(
    () => {},
    () => {},
  );
  return run;
}
