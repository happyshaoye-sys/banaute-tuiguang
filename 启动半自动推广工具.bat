@echo off
chcp 65001 >nul
cd /d "%~dp0"
set TGTOOL_PORT=8765
echo Starting Semi-Automatic Promotion Tool...
echo Browser URL: http://127.0.0.1:8765
echo Keep this window open while using the tool.
"%~dp0TuiGuangTool.exe"
pause
