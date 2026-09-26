# DotLingo

<p>
  <img src="https://img.shields.io/badge/Runtime-Python_3.12--3.13-3776AB?style=flat" alt="Python 3.12-3.13" />
  <img src="https://img.shields.io/badge/Platform-Windows_10%2F11_x64-555?style=flat" alt="Windows 10/11 x64" />
  <img src="https://img.shields.io/badge/Category-Desktop_App-orange?style=flat" alt="Desktop application" />
  <!-- loc:start --><img src="https://img.shields.io/badge/lines_of_code-3786-lightgrey?style=flat" alt="3786 lines of code" /><!-- loc:end -->
</p>

<img src="docs/cover.svg" width="720" alt="DotLingo" />

DotLingo - настольное приложение для локального перевода документов и книг. Проекты, исходники и переводы хранятся отдельно; модельные веса пользователь загружает сам после явного согласия.

## Что внутри

- **Проекты и очередь:** сохранённые настройки, история задач, пауза, отмена и восстановление прерванных задач.
- **Редактор:** оригинал и перевод рядом, переход по разделам, поиск и ручная правка перевода.
- **Импорт:** TXT, Markdown, DOCX, EPUB и PDF с текстовым слоем. Для PDF нужен дополнительный пакет `pypdf`; сканы и OCR пока не поддерживаются.
- **Экспорт:** TXT, Markdown, DOCX и EPUB. PDF можно извлечь в TXT или Markdown; запись обратно в PDF не реализована.
- **Модели:** в реестре пять записей. Qwen3 1.7B и 4B доступны для явной загрузки; остальные записи требуют проверки. Связка runtime и модели ещё не прошла реальное испытание на Windows.
- **Сборка:** сценарий PyInstaller и Inno Setup находится в `deploy/windows/build.ps1`. `Setup.exe` пока не собран и чистая Windows-установка не проверена.

Подробности: [форматы](docs/FORMAT_MATRIX.md), [модели](docs/MODEL_MATRIX.md), [архитектура и ADR](docs/ARCHITECTURE.md), [аудит DotAudio](docs/DOTAUDIO_AUDIT.md), [сборка Windows](docs/BUILD_WINDOWS.md).

## Запуск

Нужен CPython 3.12 или 3.13 и рабочий Tcl/Tk. В PowerShell из корня репозитория:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,pdf]"
python -m dotlingo
```

Расширенная инструкция по Windows, inference и данным: [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md). Для первого окна модель не нужна; для перевода отдельно установите CPU runtime и загрузите модель в приложении. Модельные веса не скачиваются автоматически.

## Команды

| Действие | Команда |
|----------|---------|
| Запустить приложение | `python -m dotlingo` |
| Запустить тесты | `python -m pytest -q` |
| Проверить стиль | `python -m ruff check src tests` |
| Собрать Windows Setup.exe | `.\deploy\windows\build.ps1` |

## Стек

<p>
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Tkinter-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Tkinter" />
  <img src="https://img.shields.io/badge/psutil-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="psutil" />
  <img src="https://img.shields.io/badge/python--docx-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="python-docx" />
  <img src="https://img.shields.io/badge/pypdf-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="pypdf" />
  <img src="https://img.shields.io/badge/llama--cpp--python-222222?style=for-the-badge&logo=python&logoColor=white" alt="llama-cpp-python" />
  <img src="https://img.shields.io/badge/PyInstaller-222222?style=for-the-badge&logo=python&logoColor=white" alt="PyInstaller" />
  <img src="https://img.shields.io/badge/pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white" alt="pytest" />
  <img src="https://img.shields.io/badge/Ruff-D7FF64?style=for-the-badge" alt="Ruff" />
</p>

## Тесты

Тесты покрывают форматы, сегментацию и глоссарий, очередь и сохранение состояния, загрузку модели и UI smoke-сценарий. Для запуска установите extra `dev`; для чтения PDF нужен extra `pdf`. PDF fixture-тест требует PyMuPDF (`fitz`), а UI smoke-тест - рабочий Tcl/Tk.

## Архитектура

UI на Tkinter вызывает отдельные модули ядра. SQLite хранит проект и очередь; перевод выполняется через inference worker. Импорт форматов и их экспорт разделены.

```text
src/dotlingo/
├── app.py                 # Tkinter UI
├── storage.py             # проекты, исходники, переводы и история
├── task_queue.py          # очередь, пауза, отмена и восстановление
├── formats.py             # чтение и отдельные format-writer адаптеры
├── segmentation.py        # сегменты и контекст
├── glossary.py            # глоссарий
├── engine.py              # inference worker
├── models.py              # реестр и совместимость моделей
├── model_download.py      # проверяемая загрузка весов
└── hardware.py            # сведения об устройстве
```

- Оригинал хранится отдельно; его хеш проверяется перед переводом и экспортом.
- Загрузка модели использует закреплённую ревизию и SHA-256; незавершённый файл не активируется.
- PDF поддерживает чтение текстового слоя без переноса вёрстки. OCR и PDF-экспорт отсутствуют.

## Лицензия

© 2026 DotCore. Все права защищены.

Проприетарный код. Использование, копирование, изменение и распространение запрещены без письменного разрешения автора. Исходный код открыт только для ознакомления. См. [LICENSE](LICENSE).
