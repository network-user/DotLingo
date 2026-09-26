# Аудит DotAudio

Проведён только для чтения в `C:\Users\frog2\PycharmProjects\DotAudio`. Код и файлы DotAudio не изменялись. В checkout были чужие untracked `.pytest-agent-*`/`.pytest-tmp-*`; они оставлены. Для `git status` использовался только одноразовый параметр `safe.directory`, глобальные git-настройки не менялись.

Проверены проектные правила и запрошенные пути: `AGENTS.md`; `src/dotaudio/qml/MainMvp.qml`, `Theme.js`, `SetupWizard.qml`, `ModelsPage.qml`, `Icon.qml`, `PillButton.qml`; `src/dotaudio/hardware.py`, `adapt.py`, `modelhub.py`, `model_registry.py`; `docs/ARCHITECTURE.md`, `PRODUCT.md`, `HANDOFF.md`; `deploy/install.ps1`, `deploy/update.ps1`.

## Полезно перенести как принцип

- `src/dotaudio/qml/MainMvp.qml`, `docs/ARCHITECTURE.md`: бизнес-логика отдельно от QML; UI получает события через bridge, а долгие операции не блокируют окно. В DotLingo core не импортирует Tk; UI работает с результатами потоков через main-thread event queue.
- `src/dotaudio/qml/Theme.js`: единые design tokens. Здесь они оформлены в `src/dotlingo/theme.py`.
- `src/dotaudio/qml/SetupWizard.qml`: установка по этапам. В DotLingo добавлены проверка устройства, рекомендация-кандидат, выбор/лицензия, download с отменой и «готово». Автозагрузка модели отключена.
- `src/dotaudio/qml/ModelsPage.qml`, `src/dotaudio/modelhub.py`: карточка модели перед установкой должна показывать точный размер, источник, формат и статус, загрузчик должен давать измеримый byte progress и отмену.
- `src/dotaudio/hardware.py`, `src/dotaudio/adapt.py`: отличать факт наличия устройства от GPU-поддержки конкретного backend; неизвестные значения не подменять типичными числами; проверить конкретную конфигурацию перед рекомендацией.
- `src/dotaudio/qml/Icon.qml`, `PillButton.qml`: единообразные контрольные элементы, hover/focus и состояния с текстом, а не одним цветом.
- `docs/PRODUCT.md`, `HANDOFF.md`: фиксировать фактические ограничения между итерациями, но перепроверять roadmap против shipped-кода.

## Требует адаптации

- Аппаратные поля из `hardware.py` полезны, но пороги и профили Whisper нельзя использовать для LLM-перевода. DotLingo использует размер именно выбранного GGUF, pinned квантование, контекст и память устройства; значения RAM — инженерные оценки, ещё не измеренные.
- Стадии SetupWizard надо дополнить отдельным согласием на конкретную лицензию/ревизию, проверкой SHA-256, resume, атомарной активацией и объяснением неопределённого прогресса. Модель без проверенного runtime не должна называться совместимой.
- `Theme.js` и QML показывают визуальный язык, но его лучше выразить в native Windows-интерфейсе и повторно проверить масштаб 125–200%, клавиатуру и reduced motion.
- Загрузка модели отделена от основного установщика: вес размером в гигабайты не должен попадать внутрь Setup.exe.

## Копировать нельзя

- Численные пороги RAM/VRAM и Whisper профили из `hardware.py`/`adapt.py`: это настройки распознавания аудио, не требований языковой модели.
- Иконки, SVG, QML и Python UI-код без отдельного лицензионного/технического решения: DotAudio здесь только UX-ориентир.
- Git-based установка из `deploy/install.ps1`: требует установленный Git и Python, клонирует checkout и ставит editable package, поэтому не является обычным пользовательским Setup.exe.
- Обновление из `deploy/update.ps1`: Git-based операция с checkout/reset hard может перезаписать локальное дерево; она не реализует staged swap, проверку целостности дистрибутива и rollback.
- Результаты аудио benchmark, численные рекомендации и заявление о готовности из HANDOFF без повторного теста другого продукта.

## Решение для DotLingo

Для первого Windows-поставляемого варианта выбраны PyInstaller `onedir` + Inno Setup per-user. Они собирают Python/Tk и inference DLL внутрь приложения, создают меню «Пуск» и ярлык, а `%LOCALAPPDATA%\DotLingo\projects`/`models` остаются вне каталога установки. Setup.exe в текущей среде не собран и не проверен; это план сборки, а не готовый установщик.
