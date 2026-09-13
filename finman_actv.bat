@echo off

REM === CONFIGURATION ===
SET VENV_NAME=venv-finman

REM === Check if venv exists ===
IF NOT EXIST "%VENV_NAME%\Scripts\activate.bat" (
    echo [ERROR] Virtual environment "%VENV_NAME%" does not exist or is incomplete.
    echo Please run your setup script to create it first.
    exit /b 1
)

REM === Activate the virtual environment ===
echo Activating virtual environment: %VENV_NAME%
call "%VENV_NAME%\Scripts\activate.bat"

REM === Confirm Python version inside venv ===
python --version

REM === Now you're inside the venv. You can run other commands here ===
echo [INFO] Virtual environment is now active.
echo Type "deactivate" to exit.
