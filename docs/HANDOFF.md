# Передача DotLingo MVP

Снимок работы: 2026-09-26. Checkout `C:\Users\frog2\PycharmProjects\DotLingo`. В исходном checkout не было `.git`; по просьбе пользователя репозиторий создан в этой же папке на ветке `main`. Файлы DotAudio не менялись.

## Реализовано

- Desktop UI на Tkinter с проектами, документами, очередью, параллельным review, моделями, глоссарием и настройками.
- Qt-free слой форматов/storage/segmentation/task queue; SQLite хранит состояние. Документ импортируется в отдельную копию, сохраняет SHA-256, а перед переводом и экспортом проверяется неизменность.
- Сегментация длинных блоков, небольшой предыдущий контекст, glossary markers, правила и контекст проекта; результат каждого фрагмента сохраняется сразу.
- TXT, Markdown, DOCX и EPUB поддерживают законченный импорт/review/export; PDF поддерживает только извлечение текстового слоя в TXT/Markdown. Ошибка scan явная, OCR не запускается.
- Изолированный inference worker с отменой/паузой, persistent queue, восстановлением незавершённых задач и понятными неуспешными статусами.
- Пинованные GGUF-кандидаты Qwen3 с размером/hash/revision, согласие перед загрузкой, partial/resume, проверка диска/размера/SHA/magic, атомарная активация. Многогигабайтные веса не скачивались.
- Windows onedir + Inno Setup build recipe и per-user установка, пользовательские данные отдельно от binaries.

## Выполненные проверки

Последний полный прогон: `21 passed, 3 skipped`. Два PDF-теста пропущены, потому что `pypdf` не установлен в активном Python; GUI smoke пропущен из-за сбоя системного Tcl/Tk (`Can't find a usable init.tcl`). `compileall`, проверка `models.json`, Ruff и синтаксический разбор `build.ps1` прошли.

В этой среде есть PyMuPDF, но нет pypdf, `llama_cpp`, PyInstaller, Inno Setup/ISCC и рабочего Tcl/Tk runtime. Поэтому здесь нельзя проверить UI, PDF parser pypdf, сборку/установщик и настоящий локальный inference. Ни один model weight не загружен. Качество перевода, скорость, RAM/VRAM и стабильность Qwen не измерялись. Не заявлять эти возможности как уже проверенные.

## Известные ограничения и следующий шаг

1. Установить pypdf в dev/build environment и выполнить PDF fixtures; проверить scan, encrypted/corrupt PDF и порядок extraction.
2. Собрать Windows onedir и Setup.exe на Windows 10/11 x64 с доступом к pip/Inno; проверить чистую VM по `BUILD_WINDOWS.md`, Tcl/Tk assets и размер bundle.
3. Согласовать конкретный pinned model artifact с пользователем, загрузить его только после явного consent и запустить end-to-end offline перевод. Провести EN↔RU слепое сравнение по `MODEL_MATRIX.md`.
4. Проверить реальные отказные сценарии RAM/VRAM, зависший backend, закрытие процесса и параметры модели. GPU пока отключён.
5. Перед релизом определить сертификат подписи, издателя и процедуру обновления. Staged update/rollback сейчас отсутствует; имеющийся Setup-рецепт не является автообновлятором.
