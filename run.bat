@echo off
rem One-click launcher for the website scraper.
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set /p SITE_URL=Enter the website URL to scrape (or press Enter to use the URL inside scrape.py):

if "%SITE_URL%"=="" (
    "%PY%" scrape.py
) else (
    "%PY%" scrape.py "%SITE_URL%"
)

echo.
pause
