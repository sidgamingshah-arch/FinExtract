@echo off
rem FinEx - double-click to stop the app started by Start-FinEx.bat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\stop-finex.ps1"
