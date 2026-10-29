@echo off
REM Starts Self-Actors Hub ZW with the local AI model (llama3.2:1b on Ollama).
REM Double-click this file, or type start.bat in the terminal.
cd /d "%~dp0"
set AI_PROVIDER=ollama
set OLLAMA_MODEL=llama3.2:1b
python run.py
pause
