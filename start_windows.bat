@echo off
setlocal
cd /d "%~dp0"
title SEMPAT
where python >nul 2>nul
if errorlevel 1 (
  echo Python belum terpasang atau belum masuk PATH.
  echo Unduh dari https://www.python.org/downloads/ lalu centang "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Menyiapkan lingkungan Python pertama kali ^(butuh internet^)...
  python -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, cryptography" 2>nul || ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
".venv\Scripts\python.exe" run.py --host 0.0.0.0 %*
pause
exit /b 0
:fail
echo.
echo Gagal menyiapkan lingkungan. Periksa koneksi internet lalu jalankan lagi.
pause
exit /b 1
