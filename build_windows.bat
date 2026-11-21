@echo off
REM Builds dist\Nexus\Nexus.exe. Needs Python 3.10+ from python.org on PATH.
setlocal
cd /d "%~dp0"
if not exist .venv ( py -3 -m venv .venv || python -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean nexus.spec
if errorlevel 1 ( echo BUILD FAILED & exit /b 1 )
echo.
echo Done: %cd%\dist\Nexus\Nexus.exe
endlocal
REM Optional installer (needs Inno Setup 6): 
REM   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\nexus.iss
