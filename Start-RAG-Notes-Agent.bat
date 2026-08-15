@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Run this file from an extracted release package on Windows.
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is required. Install Docker Desktop and run this file again.
  pause
  exit /b 1
)

docker version >nul 2>&1
if errorlevel 1 (
  set "DOCKER_DESKTOP="
  if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
  if not defined DOCKER_DESKTOP if exist "%LOCALAPPDATA%\Docker\Docker Desktop.exe" set "DOCKER_DESKTOP=%LOCALAPPDATA%\Docker\Docker Desktop.exe"
  if not defined DOCKER_DESKTOP if exist "D:\Docker\Docker Desktop.exe" set "DOCKER_DESKTOP=D:\Docker\Docker Desktop.exe"
  if not defined DOCKER_DESKTOP (
    echo Docker Desktop is not running. Start it manually, then run this file again.
    pause
    exit /b 1
  )
  echo Starting Docker Desktop...
  start "" "%DOCKER_DESKTOP%"
)

set /a docker_attempts=0
:wait_for_docker
docker version >nul 2>&1
if not errorlevel 1 goto start_project
set /a docker_attempts+=1
if !docker_attempts! GEQ 90 (
  echo Docker Desktop did not become ready within 180 seconds.
  pause
  exit /b 1
)
timeout /t 2 /nobreak >nul
goto wait_for_docker

:start_project
echo Starting RAG Notes Agent. The first run can take several minutes to build images.
docker compose up -d --build
if errorlevel 1 (
  echo Project startup failed. Check Docker Desktop and port 8000/5173 availability.
  pause
  exit /b 1
)

set /a health_attempts=0
:wait_for_api
curl.exe -fsS http://127.0.0.1:8000/health >nul 2>&1
if not errorlevel 1 goto open_browser
set /a health_attempts+=1
if !health_attempts! GEQ 90 (
  echo API health check timed out. Run "docker compose logs api" for details.
  pause
  exit /b 1
)
timeout /t 2 /nobreak >nul
goto wait_for_api

:open_browser
start "" "http://127.0.0.1:5173"
echo RAG Notes Agent is ready: http://127.0.0.1:5173
exit /b 0
