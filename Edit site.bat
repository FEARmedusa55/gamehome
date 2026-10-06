@echo off
rem Double-click to open the site editor in your browser.
rem Keep this window open while you edit; close it to stop the editor.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py tools\editor.py
) else (
  python tools\editor.py
)
if errorlevel 1 (
  echo.
  echo The editor did not start. Read the message above.
  echo If it says Python was not found, install it from python.org and
  echo tick "Add python.exe to PATH" during setup.
  pause
)
