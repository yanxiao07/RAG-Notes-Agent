@echo off
setlocal EnableExtensions

rem Stop only this project's containers. Docker volumes are intentionally preserved.
cd /d "%~dp0"
docker compose down
if errorlevel 1 (
  echo Project shutdown failed. Ensure Docker Desktop is running.
  pause
  exit /b 1
)
echo RAG Notes Agent containers have stopped. Docker data volumes were preserved.
exit /b 0
