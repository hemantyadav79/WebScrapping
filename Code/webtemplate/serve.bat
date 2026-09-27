@echo off
rem Start a local server so the website can read listings.json live.
cd /d "%~dp0"
echo Opening Property Explorer at http://localhost:8000 ...
start "" http://localhost:8000
python -m http.server 8000
