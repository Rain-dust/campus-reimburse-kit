@echo off
setlocal
if not exist ".venv\Scripts\python.exe" (
  echo Please run scripts\setup_windows.bat first.
  exit /b 1
)
set "PADDLE_WHEELS=https://www.paddlepaddle.org.cn/whl/windows/mkl/avx/stable.html"

echo Install the verified Windows CPU PaddlePaddle/PaddleOCR combination.
.venv\Scripts\python.exe -m pip install --find-links "%PADDLE_WHEELS%" paddlepaddle==2.6.1 --timeout 180 --retries 3
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt --timeout 180 --retries 3
if errorlevel 1 goto :failed
echo OCR installation finished. Set RM_OCR_PROVIDER=paddle before starting the assistant.
exit /b 0

:failed
echo OCR installation failed. Check the PaddlePaddle Windows installation guide and retry.
exit /b 1
