@echo off
REM Runs Nexus from the source folder (installs dependencies on first run).
cd /d "%~dp0"
if not exist .venv ( py -3 -m venv .venv || python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
start "" .venv\Scripts\pythonw.exe run_nexus.py %*
