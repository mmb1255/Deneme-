@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python futures_scanner.py --loop 300
pause
