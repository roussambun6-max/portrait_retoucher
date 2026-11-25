@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Please run setup.bat first. & pause & exit /b 1 )
set "IN=%~1"
if "%IN%"=="" set /p "IN=Drag a video or a folder of clips here and press Enter: "
if not defined IN ( echo No file given. & pause & exit /b 1 )
set "IN=%IN:"=%"
set "ST="
set "EN="
set "RA="
set "FD="
set "XM="
echo.
echo Cut standard (automatic): drop the first 0.5s, start when the action starts,
echo end before camera shake or a pan-out, skip shaky and behind-the-scenes clips.
echo.
if exist "%IN%\*" echo Folder: every clip is checked. Good cuts and cut_report.txt go to the _cut folder.
if exist "%IN%\*" goto ask_look
set /p "ST=Start time, e.g. 0:30 (Enter = automatic): "
set /p "EN=End time, e.g. 2:15 (Enter = automatic): "
:ask_look
set /p "RA=Ratio 16:9, 9:16, 1:1, 4:5 (Enter = keep original): "
set /p "FD=Fade in/out seconds, e.g. 0.5 (Enter = none): "
set /p "XM=Also make a Premiere Pro XML? y/N: "
set "ARGS="
if not "%ST%"=="" set "ARGS=%ARGS% --start %ST%"
if not "%EN%"=="" set "ARGS=%ARGS% --end %EN%"
if not "%RA%"=="" set "ARGS=%ARGS% --ratio %RA%"
if not "%FD%"=="" set "ARGS=%ARGS% --fade %FD%"
if /i "%XM%"=="y" set "ARGS=%ARGS% --xml"
".venv\Scripts\python.exe" cut_video.py "%IN%" %ARGS%
pause
