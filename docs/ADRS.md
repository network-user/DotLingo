# Краткие ADR

## ADR-001 · Отделить core от UI, в MVP использовать Tkinter

**Статус:** заменён ADR-008.

**Решение:** форматная логика, storage, очередь и inference не зависят от UI. Windows shell сделан на стандартном Tkinter, фоновые действия сообщают в main thread через очередь.

**Причина:** пользователь просил тонкий desktop UI и Qt-free core. Tk есть в стандартном CPython и PyInstaller умеет упаковывать Tcl/Tk; это уменьшает дополнительный Qt runtime. Нативный UI можно заменить, сохранив core.

**Ограничение:** текущий Python environment содержит Tkinter, но его Tcl/Tk не загружается. На чистой Windows smoke test не выполнен; поставка обязана включить Tcl/Tk и пройти тест на чистой системе.

## ADR-002 · Python 3.12, SQLite и PyInstaller onedir

**Решение:** Python 3.12, `python-docx`, `pypdf`, SQLite; PyInstaller folder bundle, сверху Inno Setup per-user `Setup.exe`.

**Причина:** доступные mature format libraries и Qt-free UI. PyInstaller onedir включает interpreter/libraries; пользователь не ставит отдельно Git/Python. Onedir выбран как диагностируемая/прямо исполняемая папка без распаковки всего бинарника во временный каталог. Документация PyInstaller не рекомендует onefile для ряда сценариев и говорит сначала проверять onedir.

**Риски:** inference DLL увеличивают приложение. Код не подписан, SmartScreen может показывать предупреждение; новая подпись не сразу гарантирует репутацию. PyInstaller не устраняет антивирусные предупреждения. Размер bundle не измерен. [PyInstaller режимы](https://pyinstaller.org/en/stable/operating-mode.html), [SmartScreen и подпись](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

## ADR-003 · Runtime выбирается отдельно для формата модели

**Решение:** первый доступный adapter поддерживает закреплённые GGUF Qwen3 через `llama-cpp-python==0.3.35` (build candidate). Веса скачиваются отдельно.

**Причина:** один backend не работает со всеми форматами. Официальный upstream `llama-cpp-python` указывает, что pip install может компилировать C/C++/llama.cpp; также upstream публикует отдельные CPU/GPU wheel indexes и backend-specific requirements. Build script использует отдельный CPU wheel index и включает runtime в пакет. Никакой CUDA runtime не скачивается на клиент.

**Ограничение:** exact binding, interpreter, GGUF, CPU/GPU и PyInstaller DLL combination пока не запускались здесь. Windows model adapter остаётся experimental до smoke test.

Решение о каталоге Qwen3 заменено ADR-009. GPU offload больше не выключен жёстко: число слоёв считается по свободной VRAM, а нехватка памяти при старте возвращает задачу на CPU. Этот путь не измерен.

## ADR-004 · Проектный документ как normalized tree + отдельный writer

**Решение:** импортёр отдаёт блоки/locators; сегментер, glossary и task queue не знают исходный контейнер. Export writer принимает оригинал и карту переводов и пишет отдельный artifact.

**Причина:** поддерживает долгие главы и повторную генерацию без изменения исходника; можно добавлять HTML/RTF/ODT/PPTX/XLSX отдельными форматными адаптерами.

**Ограничение:** нормализация flatten-ит некоторые inline markup. В матрице отдельно описаны поддержка чтения, экспорта и format loss.

## ADR-005 · Данные и веса в LocalAppData, не в `{app}`

**Решение:** бинарники в `%LOCALAPPDATA%\Programs\DotLingo`, проекты/настройки/модели в `%LOCALAPPDATA%\DotLingo`. Inno работает без elevation; uninstall удаляет только файлы приложения/ярлыки.

**Причина:** веса многогигабайтные и должны переживать uninstall/update. `PrivilegeRequired=lowest` соответствует per-user path. Inno per-user режим создаёт menu shortcuts для текущего пользователя.

**Ограничение:** Inno script добавлен, но ISCC отсутствует, поэтому Setup.exe и install/uninstall на чистой Windows не проверены.

`pypdf` выбран вместо PyMuPDF для чтения текстового слоя PDF. PyMuPDF предлагается под AGPL-3.0 либо коммерческой лицензией Artifex; для standalone-сборки без решения по лицензии продукта это неподходящее умолчание. Книжная полоса (шрифт ModernMT) собирается обратно в PDF через `pdfminer.six`, `reportlab` и `pypdfium2`: текст закрывается цветом бумаги и набирается заново. Прочий PDF по-прежнему уходит в TXT или Markdown.

## ADR-006 · Модельная загрузка безопасна и добровольна

**Решение:** model registry поставляется внутри приложения; разрешены только pinned repo/file/revision, HTTPS Hugging Face domain/redirect allowlist и GGUF; проверяются disk space, content range/length, byte count, SHA-256 и magic до atomic activation.

**Причина:** не выполнять произвольный код из downloaded model и не скачивать веса автоматически. Пользователь выбирает лицензию/модель/объём и подтверждает ещё раз.

**Ограничение:** каталог не подписан удалённым manifest; новые модели требуют обновления приложения. Обновление app пока только описано, не реализовано.

## ADR-007 · Пока без OCR и layout-preserving PDF

**Решение:** PDF parser требует извлекаемый текст на каждой странице. При вероятном скане сообщает номера страниц; OCR недоступен.

**Причина:** OCR должен иметь отдельные language packs, binary provenance, загрузку и ручную проверку качества. Tesseract OCR код под Apache-2.0, но основной проект указывает, что для новых версий нет официального Windows installer, а данные языков — отдельные файлы. Не включать зависимость молча. [Tesseract Windows/download notes](https://tesseract-ocr.github.io/tessdoc/Downloads.html), [install/language data](https://tesseract-ocr.github.io/tessdoc/Installation.html).

## ADR-008 · Переход UI с Tkinter на pywebview

**Статус:** принято 2026-09-30.

**Контекст:** Tkinter-монолит в `app.py` (3700+ строк) ограничивал дизайн: нет blur, скруглений и анимаций, тему приходилось перекрашивать рекурсивной заменой цветов виджетов, набор виджетов устарел. Ядро (formats, storage, очередь, inference) уже UI-независимо и переделки не требовало.

**Решение:** pywebview 5 (на Windows - Edge WebView2 через pythonnet) и статический vanilla JS на ES-модулях без сборочного шага (`src/dotlingo/web/`). Локальный HTTP-сервер pywebview раздаёт `web/`; `http_server=True` обязателен, потому что ES-модули не загружаются с `file://` (CORS). Мост `js_api` в `api.py`: camelCase-методы, единый конверт `{ok, data}` / `{ok: false, error, code}`; результаты фоновых действий приходят push-событиями через `evaluate_js`. Дизайн - Apple-glass монохром с чёрным приоритетом; темы переключаются атрибутом CSS `[data-theme]`, `theme.py` удалён.

**Последствия:** добавлены зависимость `pywebview` и требование WebView2 runtime на чистых Windows 10 (Win11 и обновлённые Win10 уже содержат его; установщик проверяет реестр и показывает подсказку). UI-тесты выполняются через мост без окна: `python -m dotlingo --smoke-test`. PyInstaller собирает webview/pythonnet/clr_loader через `collect_all`; `tkinter` исключён из бандла.

## ADR-009 · Каталог по умолчанию - переводческие Hy-MT2

**Статус:** принято 2026-10-02. Заменяет модельную часть ADR-003.

**Контекст:** Qwen3 в каталоге были общими многоязычными моделями. Для переводчика документов нужен специализированный GGUF, который открывается тем же `llama-cpp-python`.

**Решение:** скачиваются только официальные GGUF Tencent Hy-MT2: 1.8B Q4_K_M, 1.8B Q8_0 и 7B Q4_K_M. Ревизия, размер и SHA-256 закреплены. Промпт и сэмплинг берутся из карточки: одна user-реплика, только перевод, temperature 0.7, top_p 0.6, top_k 20, repetition penalty 1.05, контекст приложения 8192. Правленые пары проекта подмешиваются как короткие примеры. GPU-слои считаются по свободной VRAM; при нехватке памяти старт повторяется на CPU.

**Не входит:** STQ 1.25/2-bit (нужен отдельный кернел), Hy-MT2-30B-A3B (`hy_v3`), TranslateGemma (safetensors и условия Gemma). Качество EN↔RU и Windows inference этим решением не объявляются проверенными.
