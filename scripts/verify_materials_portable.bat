@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "APP_NAME=RMReimbursementMaterials"
set "APP_EXE=dist\%APP_NAME%\%APP_NAME%.exe"
set "TEMPLATE_DIR=dist\%APP_NAME%\_internal\inventory_templates"
set "SMOKE_OUTPUT=%TEMP%\rm-materials-smoke-%RANDOM%-%RANDOM%.txt"

if not exist "%APP_EXE%" (
  echo [ERROR] Portable release executable not found: %APP_EXE%
  exit /b 1
)

set /a TEMPLATE_COUNT=0
for %%F in ("%TEMPLATE_DIR%\*.xlsx") do if exist "%%~fF" set /a TEMPLATE_COUNT+=1
if not "%TEMPLATE_COUNT%"=="2" (
  echo [ERROR] Built release is missing the two official inventory templates.
  exit /b 1
)

"%APP_EXE%" --smoke > "%SMOKE_OUTPUT%" 2>&1
set "SMOKE_EXIT=%ERRORLEVEL%"
findstr /X /C:"LOCAL_MATERIALS_SMOKE_OK" "%SMOKE_OUTPUT%" >nul
set "MARKER_EXIT=%ERRORLEVEL%"

if not "%SMOKE_EXIT%"=="0" goto :failed
if not "%MARKER_EXIT%"=="0" goto :failed

del "%SMOKE_OUTPUT%" >nul 2>nul
echo [OK] Built executable passed the offline smoke test.
exit /b 0

:failed
echo [ERROR] Built executable smoke test failed. Output follows:
type "%SMOKE_OUTPUT%"
del "%SMOKE_OUTPUT%" >nul 2>nul
exit /b 1
