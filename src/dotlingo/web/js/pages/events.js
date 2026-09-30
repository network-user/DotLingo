/**
 * Входящие события моста: тосты на границах задач и обновление стора.
 * Вешается один раз при импорте, до старта pywebview-колбэков.
 */

import { toast } from '../components.js';
import * as store from '../store.js';

window.DL = window.DL || {};
window.DL.push_event = (name, payload) => {
  store.emit(name, payload);
};

store.on('task_event', (payload) => {
  store.pushTaskEvent(payload);
  const status = payload?.task?.status;
  const name = payload?.documentName || 'Документ';
  if (status === 'failed') {
    toast(payload?.task?.message || `${name}: перевод остановился с ошибкой`, 'error', 7000);
  } else if (status === 'complete') {
    toast(`${name}: перевод сохранён`, 'success');
  }
});

store.on('documents_imported', (payload) => {
  const imported = payload?.imported || [];
  const errors = payload?.errors || [];
  if (imported.length === 1) toast(`Импортирован: ${imported[0].name}`, 'success');
  else if (imported.length > 1) toast(`Импортировано документов: ${imported.length}`, 'success');
  for (const doc of imported) {
    for (const warning of doc.warnings || []) {
      toast(`${doc.name}: ${warning}`, 'warning', 8000);
    }
  }
  for (const item of errors) toast(`${item.name}: ${item.error}`, 'error', 8000);
  if (!imported.length && !errors.length) toast('Файлы не импортированы', 'warning');
  store.emit('documents_changed');
});

store.on('export_done', (payload) => {
  if (payload?.ok) toast('Экспорт сохранён', 'success');
  else if (payload?.error) toast(payload.error, 'error', 7000);
});

store.on('download_progress', (payload) => {
  store.set('download', payload);
});

store.on('download_done', (payload) => {
  store.set('download', null);
  toast(
    payload?.ok ? 'Модель загружена и проверена' : payload?.error || 'Загрузка не завершилась',
    payload?.ok ? 'success' : 'error',
    7000,
  );
  store.emit('models_changed');
});

store.on('model_verified', (payload) => {
  toast(
    payload?.ok ? 'Файл модели совпал с карточкой' : payload?.error || 'Проверка не прошла',
    payload?.ok ? 'success' : 'error',
    7000,
  );
});

store.on('custom_model_imported', (payload) => {
  toast(
    payload?.ok ? 'Модель скопирована в каталог моделей' : payload?.error || 'Импорт не выполнен',
    payload?.ok ? 'success' : 'error',
    7000,
  );
  if (payload?.ok) store.emit('models_changed');
});

store.on('hardware_detected', (payload) => {
  store.set('hardware', payload);
  store.emit('models_changed');
});
