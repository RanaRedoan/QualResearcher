@echo off
chcp 65001 >nul
title QualResearcher - Bangla transcription
cd /d "%~dp0"
rem Python packages live outside this folder (it may be inside Google Drive)
set "VENV=%LOCALAPPDATA%\Kothon\venv"

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python is not installed. Install Python 3.12 or newer from https://www.python.org/downloads/
  echo     During install, tick "Add python.exe to PATH".
  pause
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [!] ffmpeg is not installed. Open PowerShell and run:
  echo       winget install Gyan.FFmpeg
  echo     Then close this window and double-click start.bat again.
  pause
  exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
  echo Creating a private Python environment - first run only...
  python -m venv "%VENV%"
)
call "%VENV%\Scripts\activate.bat"

if not exist "%VENV%\installed.flag" (
  echo Installing packages - first run only, takes a few minutes...
  python -m pip install --upgrade pip
  pip install -r requirements.txt || (echo [!] Package install failed. & pause & exit /b 1)
  echo ok> "%VENV%\installed.flag"
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo [!] Paste your Gemini API key after GEMINI_API_KEY= in the file that opens, save it, then close Notepad.
  notepad ".env"
)

if not exist "%LOCALAPPDATA%\Microsoft\Windows\Fonts\HindSiliguri-Regular.ttf" (
  echo Installing the Hind Siliguri Bangla font for Word - first run only...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_fonts.ps1"
)

python manage.py migrate --noinput >nul
echo.
echo  QualResearcher is running at  http://127.0.0.1:8000
echo  Keep this window open while transcribing. Close it to stop.
echo.
start "" "http://127.0.0.1:8000"
python manage.py runserver 127.0.0.1:8000 --noreload
pause
