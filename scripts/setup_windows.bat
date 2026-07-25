@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "PYTHON_EXE="
if exist "D:\python\python.exe" set "PYTHON_EXE=D:\python\python.exe"
if not defined PYTHON_EXE where python >nul 2>nul && set "PYTHON_EXE=python"
if not defined PYTHON_EXE where py >nul 2>nul && set "PYTHON_EXE=py"

if not defined PYTHON_EXE (
  echo [ERROR] Python 3.10 or later was not found.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  "%PYTHON_EXE%" -m venv .venv
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip --timeout 120
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt --timeout 120
if errorlevel 1 exit /b 1

echo [OK] Development environment is ready.
exit /b 0
