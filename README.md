# DotLingo

<p>
  <img src="https://img.shields.io/badge/Python-3.12--3.13-3776AB?style=flat" alt="Python 3.12-3.13" />
  <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-555?style=flat" alt="Windows 10/11 x64" />
  <img src="https://img.shields.io/badge/Category-Desktop%20App-orange?style=flat" alt="Desktop application" />
  <!-- loc:start --><img src="https://img.shields.io/badge/lines_of_code-21575-lightgrey?style=flat" alt="21575 lines of code" /><!-- loc:end -->
</p>

<img src="docs/cover.svg" width="720" alt="DotLingo" />

<!-- audit:start -->
<p>
  <a href="docs/audit/latest.md"><img src="https://img.shields.io/badge/security_audit-passed_with_warnings-dbab09?style=flat" alt="security audit passed with warnings - full, leaks + code" /></a>
  <a href="docs/audit/2026-10-08-amber-ledger.md"><img src="https://img.shields.io/badge/date-2026--10--08-555?style=flat" alt="audit date" /></a>
</p>
<!-- audit:end -->

DotLingo переводит документы локально на Windows. Оригинал хранится отдельной копией и не переписывается; перевод идёт через GGUF в отдельном процессе llama.cpp, очередь и тексты лежат в SQLite проекта. Окно открывает pywebview на Edge WebView2 и отдаёт веб-слой без сборки по локальному HTTP: ES-модули с `file://` не грузятся.

## Что внутри

- **Каталог:** 4 GGUF в `src/dotlingo/models.json`. Hy-MT2 1.8B Q4_K_M, 1.8B Q8_0, 7B Q4_K_M и TranslateGemma 4B Q4_K_M. Файл качается после согласия и сверяется по ревизии, размеру и SHA-256. Safetensors Google не скачиваются. Qwen и STQ 1.25/2 бита в этот список не входят.
- **Рынок:** 6 репозиториев Hugging Face в `market.py`: HY-MT1.5 1.8B и 7B, Hy-MT2 1.8B и 7B, Qwen3 4B и 8B. Кнопка читает карточки и дерево файлов, не веса. На репозиторий остаётся до трёх GGUF, по роли скорость, баланс и точность. Перед скачиванием ревизия, размер и SHA-256 сверяются ещё раз.
- **Свой файл:** выбранный локальный GGUF копируется в каталог моделей после отдельного подтверждения. Проверяются magic, размер и SHA-256. Языки и контекст, которые задаёт пользователь, совместимость не подтверждают.
- **Устройство:** RAM, диск, CPU, GPU и runtime смотрятся локально и кэшируются. Повтор запускается из настроек. Рекомендация берёт самую крупную модель каталога, если всей RAM устройства хватает на расчёт плюс 1 ГБ, а на диске есть размер файла и ещё 10%.
- **Проекты и очередь:** несколько целевых языков, пауза, отмена, восстановление прерванного. Проект можно переименовать и удалить.
- **Редактор:** оригинал рядом с переводом, карта документа, поиск, фильтры пустых, машинных и правленых блоков. Черновик модели хранится отдельно от правки. Пропуски глоссария подсвечиваются.
- **Диалог:** страница без проекта, перевод или разговор, до 3 вложений. Модель та же, что у очереди документов, и параллельно с ней не работает.
- **Глоссарий:** термины проекта, правка и удаление на месте. Совпадения внутри фрагмента закрываются маркерами.
- **Форматы:** импорт TXT, Markdown, DOCX, EPUB и PDF, потолок 512 МБ. Скан читается системным Windows OCR, перевод пишется поверх страницы. Экспорт TXT, Markdown, DOCX, EPUB и PDF. DOCX и EPUB пишутся только из такого же исходника.
- **Первый запуск:** один экран и одна кнопка. Она проверяет машину, выбирает модель и начинает загрузку; лицензия названа в подписи. Ошибка, пустой каталог или отказ сети всё равно открывают приложение.

Подробности: [локальная установка](docs/LOCAL_SETUP.md), [модели](docs/MODEL_MATRIX.md), [архитектура](docs/ARCHITECTURE.md), [форматы](docs/FORMAT_MATRIX.md), [сборка Windows](docs/BUILD_WINDOWS.md).

## Запуск

Нужны CPython 3.12 или 3.13 и Microsoft Edge WebView2. На Windows 11 и обновлённой Windows 10 runtime обычно уже стоит. Иначе - [Evergreen-установщик](https://developer.microsoft.com/microsoft-edge/webview2/). В PowerShell из корня репозитория:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,pdf]"
python -m dotlingo
```

Для перевода отдельно ставится extra `inference`: закреплён `llama-cpp-python==0.3.35`. Веса сами не скачиваются. Свой GGUF копируется в каталог моделей только после выбора файла и подтверждения.

Пользовательские данные лежат в `%LOCALAPPDATA%\DotLingo`. Для тестов и сборки есть `--data-dir`.

## Команды

| Действие | Команда |
|----------|---------|
| Запустить приложение | `python -m dotlingo` |
| Headless-проверка моста | `python -m dotlingo --smoke-test` |
| Запустить тесты | `python -m pytest -q` |
| Проверить стиль | `python -m ruff check src tests` |
| Собрать иконку | `python scripts/build_icon.py` |
| Собрать Windows Setup.exe | `.\deploy\windows\build.ps1` |

Сборка Setup.exe требует CPython 3.12 x64, CPU wheel `llama-cpp-python` и Inno Setup 6. Скрипт ставит `.[pdf,build]`, затем закреплённое CPU-колесо после проверки SHA-256. Иконке нужен Pillow из того же extra `build`.

## Стек

<p>
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/pywebview-222222?style=for-the-badge" alt="pywebview" />
  <img src="https://img.shields.io/badge/setuptools-555555?style=for-the-badge" alt="setuptools" />
  <img src="https://img.shields.io/badge/PyInstaller-222222?style=for-the-badge" alt="PyInstaller" />
  <img src="https://img.shields.io/badge/Pillow-555555?style=for-the-badge" alt="Pillow" />
  <img src="https://img.shields.io/badge/psutil-2d3748?style=for-the-badge" alt="psutil" />
  <img src="https://img.shields.io/badge/python--docx-2d3748?style=for-the-badge" alt="python-docx" />
  <img src="https://img.shields.io/badge/pypdf-2d3748?style=for-the-badge" alt="pypdf" />
  <img src="https://img.shields.io/badge/llama--cpp--python-222222?style=for-the-badge" alt="llama-cpp-python" />
  <img src="https://img.shields.io/badge/pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white" alt="pytest" />
  <img src="https://img.shields.io/badge/Ruff-D7FF64?style=for-the-badge&logo=ruff&logoColor=black" alt="Ruff" />
</p>

## Тесты

В `tests/` 21 модуль: форматы, сегментация, глоссарий, очередь, хранилище, загрузка, рынок, мост, диалоги, мастер, окно и остановка процесса. Headless-проверка моста - `python -m dotlingo --smoke-test`. PDF-fixture собирается через PyMuPDF (`fitz`) и пропускается, если пакета нет; само приложение читает PDF через `pypdf`. Приёмка Setup.exe на чистой Windows VM в репозитории не зафиксирована.

## Архитектура

pywebview держит окно 1280x820 (минимум 1020x680) и раздаёт `web/` своим HTTP-сервером. Мост `api.py` вызывает ядро без UI. События очереди и прогресс загрузки уходят в JS через `evaluate_js`. У каждого проекта свой `project.sqlite` в режиме WAL. Inference запускается отдельным процессом: ошибка DLL или нехватка RAM не роняют окно.

```text
src/dotlingo/
├── app.py                 # окно, HTTP-раздача, --smoke-test, --data-dir
├── api.py                 # мост js_api: конверт, worker-потоки, push
├── web/                   # vanilla JS, без сборки
│   ├── index.html
│   ├── styles/            # tokens.css и листы страниц
│   └── js/                # bridge, store, router, 8 страниц и мастер
├── hardware.py            # RAM, диск, CPU, GPU, план размещения
├── runtime_install.py     # колесо llama.cpp по кнопке, не в Setup.exe
├── models.py              # каталог и свои GGUF
├── models.json            # 4 закреплённых веса
├── model_download.py      # согласие, SHA-256, атомарная активация
├── market.py              # 6 репозиториев Hugging Face по кнопке
├── task_queue.py          # очередь перевода
├── engine.py              # отдельный процесс llama.cpp
├── scratch.py             # диалог без проекта, та же модель
├── dialogs.py             # сохранённые диалоги и вложения
├── storage.py             # SQLite на проект, копия оригинала
├── formats.py             # импорт и экспорт
├── segmentation.py        # фрагменты
├── glossary.py            # маркеры терминов
├── languages.py           # подписи и коды языков модели
├── paths.py               # %LOCALAPPDATA%\DotLingo
└── preferences.py         # тема, мастер, последний проект
```

- Оригинал не меняется. Перевод и экспорт разделены по целевому языку. DOCX и EPUB экспортируются только из того же исходного формата.
- Ответ моста - `{ok, data}` либо `{ok: false, error, code}`. Долгий вызов сразу возвращает `{started: true}` и докладывает push-событием. После начала активации веса отмена загрузки не принимается.
- Рекомендация модели смотрит всю RAM устройства с запасом 1 ГБ и свободное место с запасом 10% размера файла. Свободная RAM вердикт не меняет. Качество пар не измеряется.
- GPU получает слои, только если свободная VRAM дискретной карты покрывает расчёт и запас 1.4 ГБ. Если старт на GPU падает из-за памяти, процесс один раз поднимается на CPU с окном не длиннее 4096 и батчем 256. Потоки CPU: число логических минус один, не больше 8. В исходном запуске сборку llama.cpp можно поставить кнопкой. Setup.exe CUDA не скачивает.
- Темы переключаются атрибутом `[data-theme]` и пишутся в `preferences.json`. Новые экраны берут цвета из `tokens.css`.

## Лицензия

© 2026 DotCore. Все права защищены.

Проприетарный код. Использование, копирование, изменение и распространение запрещены без письменного разрешения автора. Исходный код открыт только для ознакомления. См. [LICENSE](LICENSE).
