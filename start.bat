@echo off
rem Starts PyMusicBot on Windows without Docker. Double-click it, or run it from a terminal.
rem
rem The first run asks for the bot token, creates a Python environment in .venv and installs
rem requirements.txt into it. Later runs reinstall only when requirements.txt changed (after an
rem update). Needs Python 3.12 or newer and FFmpeg: see docs\deployment.md.
setlocal
cd /d "%~dp0"

if not exist .env (
    copy .env.example .env >nul
    echo Opening .env in Notepad. Paste your bot token after DISCORD_TOKEN=, then save and close it.
    start /wait notepad .env
)

rem No ( ) blocks below: a %VARIABLE% in a block is read before the block runs.
if exist .venv\Scripts\python.exe goto :install
call :find_python || goto :no_python
echo Setting up PyMusicBot. The first run takes a few minutes.
%PYTHON% -m venv .venv || goto :failed

:install
rem The copy in .venv records what was installed; any difference means reinstall.
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>&1 && goto :run
echo Installing requirements...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check --quiet --require-hashes -r requirements.txt || goto :failed
rem Editable, so updated code in src is used without reinstalling.
.venv\Scripts\python.exe -m pip install --disable-pip-version-check --quiet --no-deps -e . || goto :failed
copy /y requirements.txt .venv\installed-requirements.txt >nul

:run
echo Starting PyMusicBot. Close this window or press Ctrl+C to stop it.
.venv\Scripts\python.exe -m pymusicbot
set CODE=%errorlevel%
if not "%CODE%"=="0" echo PyMusicBot stopped with an error. The messages above say why.
goto :end

:no_python
echo Python 3.12 or newer wasn't found. Install it (see docs\deployment.md), then run this again.
set CODE=1
goto :end

:failed
echo Setup failed. Check your internet connection and the messages above, then run this again.
rem Remove the half-made environment so the next run starts clean.
if exist .venv rmdir /s /q .venv
set CODE=1
goto :end

:find_python
rem py is the Python launcher; python is used when only that is on PATH.
call :try_python py -3 && exit /b 0
call :try_python python && exit /b 0
exit /b 1

:try_python
%* -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1 || exit /b 1
set "PYTHON=%*"
exit /b 0

:end
rem Keep the window open after a double-click so the messages can be read. CI sets CI.
if not defined CI pause
exit /b %CODE%
