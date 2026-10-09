@echo off
REM Maple PQ Autoclicker launcher (no packages needed, just Python 3).
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 main.py & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw main.py & exit /b 0)
echo.
echo Python was not found.
echo Install Python 3 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then run this again.
echo.
pause
exit /b 1
