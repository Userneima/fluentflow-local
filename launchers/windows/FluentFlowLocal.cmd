@echo off
rem FluentFlow Local Windows launcher shim. Keep this tiny so double-clicking
rem works even when .ps1 files are associated with an editor instead of PowerShell.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FluentFlowLocal.ps1"
endlocal
