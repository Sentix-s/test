@echo off
rem TikTok -> текст. Первый запуск ставит всё нужное (пару минут), дальше открывает окно сразу.
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if exist ".venv\Scripts\python.exe" goto installed
echo [1/2] Создаю окружение Python...
for %%V in (3.12 3.13 3.11 3.10 3) do (
    if not exist ".venv\Scripts\python.exe" py -%%V -m venv .venv >nul 2>nul
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv >nul 2>nul
if not exist ".venv\Scripts\python.exe" goto nopython

:installed
if exist ".venv\.installed" goto update
echo [2/2] Устанавливаю загрузчик видео и распознавание речи, это один раз...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
echo ok> ".venv\.installed"
goto run

:update
rem TikTok часто меняет сайт, свежий yt-dlp это чинит. Без интернета просто пропускаем.
echo Проверяю обновления загрузчика...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade "yt-dlp[default,curl-cffi]" >nul 2>nul

:run
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" tiktok_transcriber.py
) else (
    ".venv\Scripts\python.exe" tiktok_transcriber.py %*
    pause
)
exit /b 0

:nopython
echo.
echo Не найден Python. Установите Python 3.12 с https://www.python.org/downloads/
echo и при установке отметьте галочку "Add python.exe to PATH". Потом запустите start.bat снова.
pause
exit /b 1

:failed
echo.
echo Не удалось установить зависимости. Проверьте интернет и запустите start.bat ещё раз.
echo Подробности выше.
pause
exit /b 1
