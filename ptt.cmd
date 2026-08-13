@echo off
REM Command-line control for the dictation app.
REM
REM   ptt start            launch it in the background
REM   ptt stop             ask it to quit  (add --force if it will not)
REM   ptt restart
REM   ptt status           running? autostart on?
REM   ptt autostart on     start with Windows (per user, no admin needed)
REM   ptt autostart off
REM   ptt devices          list microphones and resolve MIC from config.py
REM   ptt run              run in the foreground with logs on the console
"%~dp0.venv\Scripts\python.exe" -m pushtotalk %*
