@echo off
cd /d "%~dp0"
python -m app.main %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo PIAB app exited with error %ERR%.
  echo If another instance is already open, close it first.
  echo Otherwise try: python -m app.main --force-lock
  echo.
  pause
)
exit /b %ERR%
