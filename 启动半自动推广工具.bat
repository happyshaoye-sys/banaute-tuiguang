@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Starting MetoX Semi-Automatic Promotion Tool...
echo The launcher will select an available local port and open the browser.
echo Keep this window open while using the tool.
"%~dp0TuiGuangTool.exe"
set "METOX_EXIT_CODE=%ERRORLEVEL%"
if not "%METOX_EXIT_CODE%"=="0" (
  echo.
  echo MetoX startup failed. Please keep this window and report the message above.
  pause
)
exit /b %METOX_EXIT_CODE%
