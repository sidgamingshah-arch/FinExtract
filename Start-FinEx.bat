@echo off
rem FinEx - double-click to install (first run) and start the app. Options: -WithOcr -Reinstall -NoBrowser
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start-finex.ps1" %*
