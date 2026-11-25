@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Prewedding Toolkit setup ===
set "PY="
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install, then run setup.bat again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  %PY% -m venv .venv
  if errorlevel 1 ( echo [ERROR] could not create .venv & pause & exit /b 1 )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] install failed - check internet connection & pause & exit /b 1 )
echo.
echo Setup finished. You can now use retouch.bat and cut_video.bat
pause
