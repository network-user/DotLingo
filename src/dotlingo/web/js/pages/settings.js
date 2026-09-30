/**
 * Настройки приложения: тема, движение, устройство и каталоги данных.
 */

import { call } from '../bridge.js';
import { spinner } from '../components.js';
import * as router from '../router.js';
import * as store from '../store.js';
import {
  button,
  el,
  run,
  toast,
} from './common.js';

let generation = 0;
let unlisten = () => {};

router.registerPage('settings', {
  title: 'Настройки',
  subtitle: 'Тема, устройство и место, где лежат проекты',
  render(host) {
    const ticket = ++generation;
    unlisten();
    unlisten = store.watch('hardware', () => {
      if (ticket === generation) paint(host, ticket);
    });
    paint(host, ticket);
  },
  destroy() {
    generation += 1;
    unlisten();
    unlisten = () => {};
  },
});

async function paint(host, ticket) {
  host.replaceChildren(spinner());
  const [prefs, dirs] = await Promise.all([
    run(() => call('getPreferences')),
    run(() => call('getDataDirs')),
  ]);
  if (ticket !== generation || !host.isConnected) return;
  if (!prefs || !dirs) {
    host.replaceChildren(el('div', { class: 'note note--error', text: 'Настройки не прочитались.' }));
    return;
  }
  const hardware = store.get('hardware');
  const motion = el('input', { type: 'checkbox', checked: Boolean(prefs.reduce_motion) });
  motion.addEventListener('change', async () => {
    const saved = await run(() => call('setPreferences', { reduce_motion: motion.checked }));
    if (saved === undefined) return;
    document.documentElement.dataset.reduceMotion = motion.checked ? 'true' : 'false';
    store.set('reduceMotion', motion.checked);
  });

  host.replaceChildren(
    el('div', { class: 'stack stack--lg' }, [
      el('section', { class: 'panel stack stack--lg' }, [
        el('div', { class: 'panel__title', text: 'Окно' }),
        el('div', { class: 'row row--wrap' }, [
          button({
            label: 'Тёмная',
            variant: prefs.theme === 'light' ? 'ghost' : 'primary',
            onClick: () => setTheme('dark'),
          }),
          button({
            label: 'Светлая',
            variant: prefs.theme === 'light' ? 'primary' : 'ghost',
            onClick: () => setTheme('light'),
          }),
        ]),
        el('label', { class: 'check' }, [
          motion,
          el('span', { text: 'Уменьшить движение' }),
        ]),
      ]),
      el('section', { class: 'panel stack stack--lg' }, [
        el('div', { class: 'row row--between' }, [
          el('div', { class: 'panel__title', text: 'Устройство' }),
          button({
            label: 'Проверить снова',
            iconName: 'refresh',
            size: 'sm',
            onClick: async () => {
              const started = await run(() => call('detectHardware'));
              if (started === undefined) return;
              toast('Проверяю память, диск и runtime', 'info');
            },
          }),
        ]),
        hardwareView(hardware),
      ]),
      el('section', { class: 'panel stack stack--lg' }, [
        el('div', { class: 'panel__title', text: 'Данные' }),
        el('p', { class: 'muted', text: 'Проекты и модели лежат отдельно от программы. Удаление приложения их не стирает.' }),
        dirRow('Проекты', dirs.projectsDir),
        dirRow('Модели', dirs.modelsDir),
        dirRow('Корень', dirs.dataDir),
      ]),
      el('div', { class: 'note', text: 'Оригинал при импорте копируется и больше не меняется. Перевод и экспорт пишутся отдельно, по целевому языку. GPU offload сейчас выключен, число потоков CPU выбирается само, не больше 8.' }),
    ]),
  );
}

function hardwareView(hardware) {
  if (!hardware) {
    return el('p', { class: 'muted', text: 'Проверка ещё идёт или не вернула данные.' });
  }
  const rows = [
    ['Профиль', hardware.profile || 'не определён'],
    ['Потоки CPU', hardware.cpuThreads != null ? String(hardware.cpuThreads) : 'неизвестно'],
    ['RAM', hardware.ramTotalGb != null ? `${hardware.ramTotalGb} ГБ, свободно ${hardware.ramAvailableGb ?? 'неизвестно'}` : 'неизвестно'],
    ['Диск', hardware.diskFreeGb != null ? `${hardware.diskFreeGb} ГБ свободно` : 'неизвестно'],
    ['GPU', (hardware.gpuNames || []).join(', ') || 'не найден'],
    ['llama.cpp', hardware.llamaRuntimeAvailable ? 'найден' : 'не найден'],
  ];
  return el('div', { class: 'stats' }, rows.map(([label, value]) => el('div', { class: 'stat' }, [
    el('div', { class: 'stat__label', text: label }),
    el('div', { class: 'stat__value', text: value }),
  ])));
}

function dirRow(label, path) {
  return el('div', { class: 'field' }, [
    el('span', { class: 'field__label', text: label }),
    el('div', { class: 'row' }, [
      el('input', { class: 'input grow', value: path || '', readOnly: true }),
      button({
        label: 'Открыть',
        iconName: 'export',
        size: 'sm',
        onClick: () => run(() => call('revealPath', path)),
      }),
    ]),
  ]);
}

async function setTheme(theme) {
  const saved = await run(() => call('setPreferences', { theme }));
  if (saved === undefined) return;
  store.set('theme', theme);
}
