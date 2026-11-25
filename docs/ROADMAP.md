# План разработки

| Фаза | Содержание | Статус |
|---|---|---|
| 1 | Архитектура, структура, зависимости, Ollama + Qwen, агентный цикл с инструментами, память, разрешения, текстовый чат | **готово** |
| 2 | Голос: openWakeWord (ONNX), Silero VAD, faster-whisper, Piper TTS; SLEEP → ACTIVE → SLEEP, таймер 5/10/15/20/30/60 с, «Всё, спасибо», подавление собственного голоса | следующая |
| 3 | PySide6: главное окно (ядро, визуализация голоса, чат, история, задачи, drag&drop, статус), трей + компактное окно, 6 разделов, кнопка питания, Ctrl+Alt+J, автозапуск, аварийная остановка | |
| 4 | Windows Integration Engine (приложения, окна, мышь/клавиатура, настройки, процессы, PowerShell с классификацией риска, скриншоты), файловый менеджер с undo, Playwright | |
| 5 | Word/Excel/PowerPoint через COM (+ python-docx/openpyxl/python-pptx), почта (Gmail API / MS Graph, OAuth, keyring), Defender | |
| 6 | Семантическая память (эмбеддинги), анализ экрана (VLM `qwen2.5vl:7b` по запросу), перевод речи, планировщик многоэтапных задач | |
| 7 | Performance Manager (ECO/BALANCED/PERFORMANCE, NVML, приоритеты), нагрузочные тесты, PyInstaller + Inno Setup | |
