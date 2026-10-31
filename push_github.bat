@echo off
rem ============================================================
rem  Unggah (push) SEMPAT ke GitHub dengan satu klik.
rem    push_github.bat        -> commit semua perubahan lalu push
rem    push_github.bat tag    -> + buat tag submission-24h (akhir sprint)
rem ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title SEMPAT - push ke GitHub
set "REPO=https://github.com/imon064/SEMPAT-hackathon.git"

where git >nul 2>nul
if errorlevel 1 (
  echo Git belum terpasang. Halaman unduhan Git akan dibuka.
  echo Pasang dengan pilihan bawaan, lalu klik dua kali skrip ini lagi.
  start "" "https://git-scm.com/download/win"
  pause
  exit /b 1
)

rem --- identitas commit, ditanya sekali saja ---
set "GNAME="
for /f "delims=" %%a in ('git config --global user.name 2^>nul') do set "GNAME=%%a"
if not defined GNAME (
  set /p "GNAME=Nama untuk commit, misal Brian Armando: "
  git config --global user.name "!GNAME!"
)
set "GMAIL="
for /f "delims=" %%a in ('git config --global user.email 2^>nul') do set "GMAIL=%%a"
if not defined GMAIL (
  set /p "GMAIL=Email akun GitHub: "
  git config --global user.email "!GMAIL!"
)

rem --- repo lokal + alamat GitHub ---
if not exist ".git" (
  git init -b main >nul 2>nul || (git init >nul && git symbolic-ref HEAD refs/heads/main)
  echo Repo git lokal dibuat.
)
git remote get-url origin >nul 2>nul
if errorlevel 1 (git remote add origin "%REPO%") else (git remote set-url origin "%REPO%")

rem --- kumpulkan file ---
git add -A
rem Pengaman: data rahasia, lingkungan Python, dan cloudflared TIDAK boleh ikut terunggah.
set "BAD="
for /f "delims=" %%f in ('git ls-files --cached -- data .venv tools/cloudflared.exe tools/cloudflared') do set "BAD=1"
if defined BAD (
  git rm -r --cached --quiet --ignore-unmatch data .venv tools/cloudflared.exe tools/cloudflared >nul
  echo Folder data, .venv, dan cloudflared dikeluarkan dari unggahan.
)

echo.
echo File yang akan di-commit:
git status --short
echo.

git diff --cached --quiet
if errorlevel 1 (
  set "MSG="
  set /p "MSG=Pesan commit, tekan Enter untuk otomatis: "
  if not defined MSG set "MSG=Update SEMPAT %date% %time:~0,5%"
  git commit -q -m "!MSG!"
  if errorlevel 1 goto :fail
  echo Commit dibuat.
) else (
  echo Tidak ada perubahan baru untuk di-commit.
)

git branch -M main
echo.
echo Mengunggah ke %REPO% ...
echo Jika muncul jendela login GitHub, masuk dengan akun yang punya akses ke repo.
git push -u origin main
if errorlevel 1 (
  echo Push ditolak. Mencoba mengambil perubahan dari GitHub lalu mengunggah ulang...
  git pull --rebase origin main
  if errorlevel 1 goto :fail
  git push -u origin main
  if errorlevel 1 goto :fail
)

if /i "%~1"=="tag" (
  git rev-parse -q --verify refs/tags/submission-24h >nul
  if not errorlevel 1 (
    echo Tag submission-24h sudah ada dan tidak diubah.
  ) else (
    git tag submission-24h
    git push origin submission-24h
    if errorlevel 1 goto :fail
    echo Tag submission-24h terkirim. Lanjutkan di GitHub: Releases - Draft a new release - pilih tag itu - Publish.
  )
)

echo.
echo SELESAI. Buka: https://github.com/imon064/SEMPAT-hackathon
pause
exit /b 0

:fail
echo.
echo GAGAL. Baca pesan di atas. Penyebab umum: belum login GitHub, akun tidak punya akses ke repo, atau internet putus.
pause
exit /b 1
