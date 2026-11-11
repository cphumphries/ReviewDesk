@echo off
rem Start ReviewDesk. Double-click this file. Keep the black window open while you work; close it to stop.
setlocal
title ReviewDesk
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"

rem 0. ReviewDesk's source code (a clone, or GitHub's Download ZIP): this file is in the "launchers" folder, and the
rem    app folder is one folder up. It has no Python of its own, so an installed Python is used.
set "SOURCE="
if not exist "%~dp0app\run_reviewdesk.py" if exist "%~dp0..\app\run_reviewdesk.py" set "SOURCE=1"
if defined SOURCE goto installed
rem    Opened from inside the zip file, Windows copies only this one file somewhere else: nothing else is here
if not exist "%~dp0app\run_reviewdesk.py" goto notextracted

rem 1. The Python that comes with ReviewDesk (in the "python" folder next to this file)
if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" -c "pass" >nul 2>nul
  if not errorlevel 1 goto bundled
  echo The Python that comes with ReviewDesk would not start on this computer
  echo ^(some managed computers block programs in OneDrive folders^).
  echo Trying an installed Python instead...
  echo.
)
:installed
rem 2. Python installed on this computer (the "py" launcher, then "python")
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 goto pylauncher
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not errorlevel 1 goto plainpython

echo.
echo ReviewDesk could not find a Python it can use.
if defined SOURCE goto nopythonsource
echo  - If the "python" folder next to this file is missing, extract the whole
echo    zip file you downloaded again ^(right-click it, choose Extract All^), then
echo    double-click "Start ReviewDesk" in the new folder.
echo  - Or install Python 3 (from the Software Center, or
echo    https://www.python.org/downloads/) and try again.
echo.
pause
exit /b 1

:bundled
"%~dp0python\python.exe" "%~dp0app\run_reviewdesk.py" %*
goto done
:pylauncher
if defined SOURCE goto pylaunchersource
py -3 "%~dp0app\run_reviewdesk.py" %*
goto done
:pylaunchersource
py -3 "%~dp0..\app\run_reviewdesk.py" %*
goto done
:plainpython
if defined SOURCE goto plainpythonsource
python "%~dp0app\run_reviewdesk.py" %*
goto done
:plainpythonsource
python "%~dp0..\app\run_reviewdesk.py" %*
goto done

:done
rem 3: another version of ReviewDesk is running (the message above says what to do)
if errorlevel 3 if not errorlevel 4 (
  echo.
  pause
  exit /b 3
)
if errorlevel 1 (
  echo.
  echo ReviewDesk stopped with an error ^(see above^).
  pause
)
exit /b

:nopythonsource
echo This folder holds ReviewDesk's source code, which has no Python of its own.
echo  - Install Python 3.9 or later (from the Software Center, or
echo    https://www.python.org/downloads/) and try again,
echo  - or download ReviewDesk for Windows, with Python included, from the
echo    Releases page of ReviewDesk's GitHub repository.
echo.
pause
exit /b 1

:notextracted
rem Windows runs a file opened from inside a zip from a folder such as ...\Temp1_ReviewDesk-1.3.0-windows.zip\...
set "here=%~dp0"
if /i "%here:.zip\=%"=="%here%" goto missingapp
echo.
echo ReviewDesk is still inside the zip file you downloaded, so Windows has
echo opened only this one file. To start ReviewDesk:
echo.
echo  1. Close this window.
echo  2. Right-click the zip file you downloaded ^(ReviewDesk-...-windows.zip^)
echo     and choose Extract All, then Extract.
echo  3. In the folder that opens, open the ReviewDesk folder and double-click
echo     "Start ReviewDesk".
echo.
pause
exit /b 1
:missingapp
echo.
echo The "app" folder that should be next to this file is missing, so
echo ReviewDesk cannot start. Extract the whole zip file you downloaded again
echo ^(right-click it, choose Extract All^), then double-click
echo "Start ReviewDesk" in the new folder.
echo.
pause
exit /b 1
