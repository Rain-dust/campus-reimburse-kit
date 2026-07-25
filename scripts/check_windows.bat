@echo off
setlocal
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run scripts\setup_windows.bat first.
  exit /b 1
)

".venv\Scripts\python.exe" -m compileall app core tools materials_desktop.py run_materials_desktop.py
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m unittest discover -s tests
if errorlevel 1 exit /b 1
git diff --check
if errorlevel 1 exit /b 1

echo [OK] All checks passed.
exit /b 0
