@echo off
REM Parxen x Flipkart delivery checker - double-click to start, then open http://localhost:5050
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First-time setup: creating virtual environment...
  python -m venv .venv || goto :err
  .venv\Scripts\python -m pip install -r requirements.txt || goto :err
  .venv\Scripts\python -m playwright install chromium || goto :err
)
start "" http://localhost:5050
.venv\Scripts\python app.py
goto :eof
:err
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
