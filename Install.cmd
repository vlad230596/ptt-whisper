@echo off
REM Double-click this on a machine that has never run push-to-talk dictation.
REM
REM It installs uv if missing, fetches Python 3.13 and the dependencies, downloads the
REM 2.9 GB model, generates the tray icons, puts `ptt` on your PATH, enables autostart and
REM then checks the whole thing end to end -- including loading the model and decoding on
REM the GPU. Everything it does is idempotent, so re-running it is also the repair command.
REM
REM Requires an NVIDIA GPU. Do not put this directory in a path with spaces or inside
REM OneDrive; both are refused rather than silently half-working.

powershell -ExecutionPolicy Bypass -File "%~dp0deploy\Setup.ps1" -- --add-to-path --autostart --start

echo.
echo   Note: `ptt` works in terminals opened from now on. In one that was already open,
echo   type .\ptt instead -- its PATH is the one it started with.
echo.
pause
