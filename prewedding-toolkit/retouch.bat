@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Please run setup.bat first. & pause & exit /b 1 )
set "IN=%~1"
if "%IN%"=="" set /p "IN=Drag the photo folder (or photo) here and press Enter: "
if not defined IN ( echo No file given. & pause & exit /b 1 )
set "IN=%IN:"=%"
set "SM="
set "RA="
set /p "SM=Skin smoothing 0-100 (Enter = 40): "
set /p "RA=Crop ratio 3:2, 4:5, 1:1, 16:9, 9:16 (Enter = keep original): "
set "ARGS="
if not "%SM%"=="" set "ARGS=%ARGS% --smooth %SM%"
if not "%RA%"=="" set "ARGS=%ARGS% --ratio %RA%"
".venv\Scripts\python.exe" retouch_photos.py "%IN%" %ARGS%
pause
