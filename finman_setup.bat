:: Program can be started as "digilib_setup 3.11" giving the version of python installed locally
@echo off
SETLOCAL ENABLEDELAYEDEXPANSION

:: ====================================================================
:: Configuration Section
:: ====================================================================

:: Name of the virtual environment folder
SET VENV_NAME=venv-finman

:: Path to the requirements file with Python packages
SET REQUIREMENTS_FILE=requirements.txt

REM Default to 3.13 if no parameter supplied
SET PYTHON_VERSION=%1
IF "%PYTHON_VERSION%"=="" SET PYTHON_VERSION=3.13

FOR /F "tokens=1,2 delims=." %%a IN ("%PYTHON_VERSION%") DO (
    SET REQUIRED_MAJOR=%%a
    SET REQUIRED_MINOR=%%b
)

:: ====================================================================
:: Step 1: Check if any Python is installed and accessible
:: ====================================================================

:: Use the Python launcher (py) to check the availability of Python version specified by PYTHON_VERSION
py -%PYTHON_VERSION% --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python %PYTHON_VERSION% is not installed.
    exit /b 1
)

FOR /F "tokens=2 delims= " %%i IN ('py -%PYTHON_VERSION% --version') DO SET FULLVER=%%i


:: Parse major and minor version numbers from FULLVER (expected format: x.y.z)
FOR /F "tokens=1,2 delims=." %%a IN ("!FULLVER!") DO (
    SET MAJOR=%%a
    SET MINOR=%%b
)
IF NOT DEFINED MAJOR (
    echo [ERROR] Could not parse Python version from: !FULLVER!
    exit /b 1
)
IF NOT DEFINED MINOR (
    echo [ERROR] Could not parse Python minor version from: !FULLVER!
    exit /b 1
)

:: Debug output - uncomment for troubleshooting:
echo Detected Python version: !FULLVER! (Major=!MAJOR!, Minor=!MINOR!)

:: ====================================================================
:: Step 2: Warn if Python version is less than 3.11 but continue
:: ====================================================================


:: Note: strip leading zeros (if any) to avoid comparison issues
set /A MAJOR_NUM=MAJOR + 0
set /A MINOR_NUM=MINOR + 0

IF !MAJOR_NUM! LSS %REQUIRED_MAJOR% (
    echo [WARNING] Detected Python version !FULLVER! is below the recommended %REQUIRED_MAJOR%.%REQUIRED_MINOR%.
) ELSE IF !MAJOR_NUM! EQU %REQUIRED_MAJOR% (
    IF !MINOR_NUM! LSS %REQUIRED_MINOR% (
        echo [WARNING] Detected Python version !FULLVER! is below the recommended %REQUIRED_MAJOR%.%REQUIRED_MINOR%.
    ) ELSE (
        echo Detected Python version !FULLVER! - OK
    )
) ELSE (
    echo Detected Python version !FULLVER! - OK
)

:: ====================================================================
:: Step 3: Check existence of requirements file
:: ====================================================================

IF NOT EXIST "%REQUIREMENTS_FILE%" (
    echo [ERROR] Requirements file "%REQUIREMENTS_FILE%" not found!
    goto end
)
echo Using Requirements file "%REQUIREMENTS_FILE%"

:: ====================================================================
:: Step 4: Change working directory to script location
:: ====================================================================

:: Change directory to the location of this batch script to ensure relative paths work
cd /d %~dp0

:: ====================================================================
:: Confirm before creating virtual environment
:: ====================================================================

echo.
echo This will create a virtual environment "%VENV_NAME%" using Python !FULLVER!.
choice /M "Do you want to continue"

IF ERRORLEVEL 2 (
    echo Setup cancelled by user.
    exit /b 0
)
echo.

:: ====================================================================
:: Step 5: Create virtual environment if missing
:: ====================================================================

IF NOT EXIST "%VENV_NAME%\" (
    echo Creating virtual environment: %VENV_NAME%
    py -%PYTHON_VERSION% -m venv %VENV_NAME%
) ELSE (
    echo Virtual environment "%VENV_NAME%" already exists. Skipping creation.
)

:: ====================================================================
:: Step 6: Activate virtual environment
:: ====================================================================

echo Activating virtual environment...
CALL "%VENV_NAME%\Scripts\activate.bat"

IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to activate virtual environment.
    exit /b 1
)

echo.
echo === DIAGNOSTIC: Checking if venv is really activated ===
echo Printing "where python":
where python
echo Printing "where pip":
where pip
echo "Printing Python Version":
python --version
echo "Printing pip Version":
pip --version
python -c "import sys; print('Python executable:', sys.executable)"
python -c "import sys; print('Site packages:', sys.prefix)"
echo ====================================================
echo.

echo.
echo ================================
echo Verify environment above.
echo Continue installation? (Y/N)
echo ================================
set /p confirm=Enter choice: 

if /I NOT "%confirm%"=="Y" (
    echo Aborting setup.
    exit /b
)

:: ====================================================================
:: Step 7: Install/upgrade required packages inside virtual environment
:: ====================================================================

echo Installing packages from %REQUIREMENTS_FILE%...

:: Upgrade pip safely inside venv
"%VENV_NAME%\Scripts\python.exe" -m pip install --upgrade pip

:: Install requirements safely inside venv
"%VENV_NAME%\Scripts\python.exe" -m pip install -r "%REQUIREMENTS_FILE%"

IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install packages.
    exit /b 1
)

:: ====================================================================
:: Script end
:: ====================================================================
:end
echo Done.

ENDLOCAL
pause
