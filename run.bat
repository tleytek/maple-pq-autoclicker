@echo off
REM Maple PQ Autoclicker launcher (no packages needed, just Python 3).
REM MapleStory runs as administrator, and Windows blocks keys/clicks sent to it
REM from normal programs, so this starts the app as administrator (Windows asks).
cd /d "%~dp0"
set "PYW="
where pyw >nul 2>nul && set "PYW=pyw"
if not defined PYW where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW goto nopython
powershell -NoProfile -Command "Start-Process %PYW% -ArgumentList '\"%~dp0main.py\"' -Verb RunAs"
exit /b 0

:nopython
echo.
echo Python was not found.
echo Install Python 3 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then run this again.
echo.
pause
exit /b 1
