@echo off
rem Makes nemo-private.apk, a copy of the game that talks to your server.
cd /d "%~dp0"
if not exist "nemo.apk" (
  echo Could not find nemo.apk next to this file. See PATCHING.md, step 1.
  pause
  exit /b 1
)
rem This shortcut uses port 8080. For another port, see PATCHING.md, step 3.
set /p IP=Type your server's LAN IPv4 address (see PATCHING.md, step 2) and press Enter:
set EXTRA=
rem 10.0.2.2 is how the Android Studio emulator reaches this PC; it needs a graphics workaround.
if "%IP%"=="10.0.2.2" set EXTRA=--emulator
rem Optional: put music0..music3 (.ogg or .mp3) in a "music" folder next to this file.
if exist "music\music0.*" set EXTRA=%EXTRA% --music music
rem Normal play does not need diagnostic logging; see DEVELOPMENT.md for debug options.
python patcher\patch_apk.py nemo.apk http://%IP%:8080 %EXTRA%
pause
