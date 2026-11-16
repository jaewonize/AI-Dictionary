@echo off
rem Portable job runner for the Bali real-data run. Usage: run.cmd <logname> <module> [args...]
setlocal
cd /d "%~dp0..\.."
set AIDIC_VAR_DIR=var/bali
set AIDIC_DATA_DIR=var/bali/data
set AIDIC_PASSED_DIR=var/bali/passed
set AIDIC_ANSWER_KEY=var/bali/answer_key.json
set AIDIC_DEFAULT_TZ=+08:00
if "%AIDIC_HINDSIGHT_URL%"=="" set AIDIC_HINDSIGHT_URL=http://localhost:8888
if "%AIDIC_HINDSIGHT_BANK%"=="" set AIDIC_HINDSIGHT_BANK=bali-trip
if "%AIDIC_COST_CAP_USD%"=="" set AIDIC_COST_CAP_USD=100
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set "UV_EXE=uv"
where uv >nul 2>nul
if not errorlevel 1 goto :found_uv
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" (
  set "UV_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
  goto :found_uv
)
if exist "%USERPROFILE%\.local\bin\uv.exe" (
  set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
  goto :found_uv
)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*") do (
  if exist "%%D\uv.exe" set "UV_EXE=%%D\uv.exe"
)
:found_uv

if not exist "var\bali\logs" mkdir "var\bali\logs"
set "LOG=var\bali\logs\%1.log"
echo START %DATE% %TIME% %* > "%LOG%"
"%UV_EXE%" run python -m %2 %3 %4 %5 %6 %7 %8 %9 >> "%LOG%" 2>&1
set EC=%ERRORLEVEL%
echo EXIT %EC% %DATE% %TIME% >> "%LOG%"
exit /b %EC%
