@echo off
echo Installing Python 3.11...
winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements

echo Installing Tesseract OCR...
winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements

echo Installing Python packages...
python -m pip install --upgrade pip
python -m pip install PyQt6 pytesseract deep-translator pynput Pillow

echo Installation Completed!
pause
