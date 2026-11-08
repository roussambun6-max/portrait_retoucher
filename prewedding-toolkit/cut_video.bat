@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Please run setup.bat first. & pause & exit /b 1 )
set "IN=%~1"
if "%IN%"=="" set /p "IN=Drag the video here and press Enter: "
if not defined IN ( echo No file given. & pause & exit /b 1 )
set "IN=%IN:"=%"
set "ST="
set "EN="
set "RA="
set "FD="
set /p "ST=Start time, e.g. 0:30 (Enter = from beginning): "
set /p "EN=End time, e.g. 2:15 (Enter = to the end): "
set /p "RA=Ratio 16:9, 9:16, 1:1, 4:5 (Enter = keep original): "
set /p "FD=Fade in/out seconds, e.g. 0.5 (Enter = none): "
set "ARGS="
if not "%ST%"=="" set "ARGS=%ARGS% --start %ST%"
if not "%EN%"=="" set "ARGS=%ARGS% --end %EN%"
if not "%RA%"=="" set "ARGS=%ARGS% --ratio %RA%"
if not "%FD%"=="" set "ARGS=%ARGS% --fade %FD%"
".venv\Scripts\python.exe" cut_video.py "%IN%" %ARGS%
pause
