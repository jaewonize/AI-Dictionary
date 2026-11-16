@echo off
rem Docker 없이 Windows 환경에서 Hindsight 1.2.1 서버(내장 PostgreSQL pg0 + 다국어 bge-m3 + Opus 5.5)를 바로 띄운다.
setlocal
cd /d "%~dp0..\.."

set "UVX_EXE=uvx"
where uvx >nul 2>nul
if not errorlevel 1 goto :found_uvx
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uvx.exe" (
  set "UVX_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Links\uvx.exe"
  goto :found_uvx
)
if exist "%USERPROFILE%\.local\bin\uvx.exe" (
  set "UVX_EXE=%USERPROFILE%\.local\bin\uvx.exe"
  goto :found_uvx
)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*") do (
  if exist "%%D\uvx.exe" set "UVX_EXE=%%D\uvx.exe"
)
:found_uvx

rem workspace\.env 에 ANTHROPIC_API_KEY 가 있으면 자동으로 읽어 넘긴다(화면에 키는 출력하지 않는다).
set "ENV_FILE=.env"
if "%HINDSIGHT_API_LLM_API_KEY%"=="" if exist "%ENV_FILE%" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if /I "%%A"=="ANTHROPIC_API_KEY" if not "%%B"=="" set "HINDSIGHT_API_LLM_API_KEY=%%B"
    if /I "%%A"=="CLAUD_API_KEY" if "%HINDSIGHT_API_LLM_API_KEY%"=="" if not "%%B"=="" set "HINDSIGHT_API_LLM_API_KEY=%%B"
  )
)

if "%HINDSIGHT_API_LLM_API_KEY%"=="" (
  echo [안내] workspace\.env 에 ANTHROPIC_API_KEY 가 없어 은행 가져오기 및 recall 전용 모드로 띄웁니다.
  set "HINDSIGHT_API_LLM_API_KEY=recall-only-no-key"
  set "HINDSIGHT_API_SKIP_LLM_VERIFICATION=true"
)

if "%HINDSIGHT_API_DATABASE_URL%"=="" set "HINDSIGHT_API_DATABASE_URL=pg0"
if "%HINDSIGHT_API_VECTOR_EXTENSION%"=="" set "HINDSIGHT_API_VECTOR_EXTENSION=pgvector"
if "%HINDSIGHT_API_TEXT_SEARCH_EXTENSION%"=="" (
  if /I "%HINDSIGHT_API_DATABASE_URL%"=="pg0" (
    set "HINDSIGHT_API_TEXT_SEARCH_EXTENSION=native"
    set "HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE=simple"
  ) else (
    set "HINDSIGHT_API_TEXT_SEARCH_EXTENSION=pgroonga"
  )
)
if "%HINDSIGHT_API_EMBEDDINGS_PROVIDER%"=="" set "HINDSIGHT_API_EMBEDDINGS_PROVIDER=local"
if "%HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL%"=="" set "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-m3"
if "%HINDSIGHT_API_LLM_PROVIDER%"=="" set "HINDSIGHT_API_LLM_PROVIDER=anthropic"
if "%HINDSIGHT_API_LLM_MODEL%"=="" set "HINDSIGHT_API_LLM_MODEL=claude-opus-5-5"
if "%HINDSIGHT_API_LLM_MAX_CONCURRENT%"=="" set "HINDSIGHT_API_LLM_MAX_CONCURRENT=2"
if "%HINDSIGHT_API_LLM_MAX_RETRIES%"=="" set "HINDSIGHT_API_LLM_MAX_RETRIES=8"
if "%HINDSIGHT_API_LLM_INITIAL_BACKOFF%"=="" set "HINDSIGHT_API_LLM_INITIAL_BACKOFF=5"
if "%HINDSIGHT_API_LLM_MAX_BACKOFF%"=="" set "HINDSIGHT_API_LLM_MAX_BACKOFF=60"
if "%HINDSIGHT_API_LLM_PROMPT_CACHE_ENABLED%"=="" set "HINDSIGHT_API_LLM_PROMPT_CACHE_ENABLED=false"
if "%HINDSIGHT_API_ENABLE_OBSERVATIONS%"=="" set "HINDSIGHT_API_ENABLE_OBSERVATIONS=false"
if "%HINDSIGHT_API_REFLECT_WALL_TIMEOUT%"=="" set "HINDSIGHT_API_REFLECT_WALL_TIMEOUT=600"
if "%HINDSIGHT_API_PORT%"=="" set "HINDSIGHT_API_PORT=8888"

echo Hindsight 1.2.1 (Docker 없음, DB=%HINDSIGHT_API_DATABASE_URL%, 검색=%HINDSIGHT_API_TEXT_SEARCH_EXTENSION%, 포트=%HINDSIGHT_API_PORT%) 기동 중...
echo 첫 실행 시 패키지·내장 PostgreSQL(pg0)·bge-m3 임베딩 모델을 자동으로 내려받아 몇 분 걸릴 수 있습니다.
"%UVX_EXE%" --python 3.11 --from "hindsight-api==1.2.1" hindsight-api
