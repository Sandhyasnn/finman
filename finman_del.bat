@echo off
setlocal

REM === CONFIGURATION ===
SET VENV_NAME=venv-finman

REM === Confirm existence ===
IF NOT EXIST %VENV_NAME% (
    echo [INFO] Virtual environment "%VENV_NAME%" does not exist. Nothing to delete.
    exit /b 0
)

REM === Prompt for confirmation ===
echo This will delete the virtual environment folder: %VENV_NAME%
set /p CONFIRM=Are you sure? (Y/N): 
IF /I NOT "%CONFIRM%"=="Y" (
    echo [INFO] Deletion cancelled.
    exit /b 0
)

REM === Delete the virtual environment ===
echo Deleting "%VENV_NAME%"...
rmdir /s /q %VENV_NAME%

IF EXIST %VENV_NAME% (
    echo [ERROR] Failed to delete virtual environment.
    exit /b 1
)

echo [SUCCESS] Virtual environment "%VENV_NAME%" has been deleted.
endlocal
pause
