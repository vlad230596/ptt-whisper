@echo off
REM Same app, but with a console attached so log lines and tracebacks are visible.
REM Ctrl+Alt+Q quits; closing this window also kills it.
cd /d "%~dp0"
uv run python -m pushtotalk
