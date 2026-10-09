@echo off
title Mouse Macro
cd /d "%~dp0"
py -3 -u macro.py %*
if errorlevel 9009 python -u macro.py %*
pause
