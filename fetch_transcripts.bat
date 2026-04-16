@echo off
powershell -ExecutionPolicy Bypass -NoExit -Command "& { Set-Location '%~dp0'; pip install gspread google-auth youtube-transcript-api -q; python '%~dp0fetch_transcripts.py' }"
