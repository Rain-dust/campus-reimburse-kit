@echo off
setlocal
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run scripts\setup_windows.bat first.
  exit /b 1
)

".venv\Scripts\python.exe" run_materials_desktop.py
