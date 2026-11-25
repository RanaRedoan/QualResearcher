@echo off
chcp 65001 >nul
title QualResearcher - transcribe a whole folder
cd /d "%~dp0"
rem Python packages live outside this folder (it may be inside Google Drive)
set "VENV=%LOCALAPPDATA%\Kothon\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo Run start.bat once first - it installs everything.
  pause
  exit /b 1
)
call "%VENV%\Scripts\activate.bat"

echo Transcribe every recording in a folder. Results go to a "transcripts" sub-folder.
echo Your original audio files are not changed. If Google is busy, it waits and continues by itself.
echo.
set /p FOLDER=Folder path (you can drag the folder into this window):
set FOLDER=%FOLDER:"=%
set /p ITYPE=Interview type [IDI / KII / FGD / CASE / OTHER] (Enter = IDI):
if "%ITYPE%"=="" set ITYPE=IDI
set /p CONTEXT=Study context for the AI, e.g. topic and place names (optional):
set /p EN=Also make an English translation? [y/N] (Enter = Bangla only):
set EXTRA=
if /i "%EN%"=="y" set EXTRA=--translate

python manage.py transcribe_folder "%FOLDER%" --type %ITYPE% --context "%CONTEXT%" %EXTRA%
echo.
pause
