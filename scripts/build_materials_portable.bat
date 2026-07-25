@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "PYTHON_EXE=.venv\Scripts\python.exe"
set "PYINSTALLER_EXE=.venv\Scripts\pyinstaller.exe"
set "APP_NAME=RMReimbursementMaterials"

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Run scripts\setup_windows.bat first.
  exit /b 1
)

if not exist "%PYINSTALLER_EXE%" (
  echo [INFO] Installing desktop build dependencies.
  "%PYTHON_EXE%" -m pip install -r requirements-build.txt --timeout 180
  if errorlevel 1 exit /b 1
)

"%PYTHON_EXE%" -c "import paddle, paddleocr, cv2, pypdfium2" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Local OCR runtime is missing; installing the verified Windows CPU stack.
  call "%~dp0install_local_ocr.bat"
  if errorlevel 1 exit /b 1
)

"%PYTHON_EXE%" tools\prepare_local_ocr_assets.py
if errorlevel 1 exit /b 1

"%PYINSTALLER_EXE%" --noconfirm --clean packaging\materials_desktop.spec
if errorlevel 1 exit /b 1

if not exist "dist\%APP_NAME%\%APP_NAME%.exe" (
  echo [ERROR] Portable build did not produce the expected executable.
  exit /b 1
)

copy /Y "docs\PORTABLE_USER_GUIDE.md" "dist\%APP_NAME%\README.md" >nul
if errorlevel 1 (
  echo [ERROR] Could not copy the portable user guide.
  exit /b 1
)

echo [OK] Portable offline release folder: dist\%APP_NAME%\
echo [OK] User guide: dist\%APP_NAME%\README.md
echo [OK] The release folder includes local OCR models and does not download them for end users.
exit /b 0
