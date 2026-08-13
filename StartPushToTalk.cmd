@echo off
REM Starts push-to-talk dictation with no console window.
REM Hold F8 to dictate. Ctrl+Alt+F8 lists microphones and media sessions,
REM Ctrl+Alt+T toggles test mode, Ctrl+Alt+Q quits.
REM
REM Uses the venv's pythonw.exe directly, so startup costs nothing. After changing
REM dependencies in pyproject.toml, run `uv sync` once.
REM For a console with live logs and tracebacks, use Dev.cmd instead.
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m pushtotalk
