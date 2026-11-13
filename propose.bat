@echo off
rem  Share the proposal page with a temporary Cloudflare link and watch it live.
rem  Usage:  propose.bat            (public trycloudflare.com link)
rem          propose.bat --local    (this computer only)
setlocal
cd /d "%~dp0"
title Propose
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 propose.py %*
  goto :end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python propose.py %*
  goto :end
)
echo.
echo   Python 3 is needed. Install it from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup, then run propose.bat again.
echo.
pause
:end
endlocal
