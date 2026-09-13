REM ------------------------
REM ENV CONFIG (DEV)
REM ------------------------
set DATA_ROOT=Z:\Disk2
set FINMAN_CONFIG=dev

REM Activate virtual environment (your custom script)
call finman_actv.bat

REM Start FastAPI app
python finman.py ^
        --reload ^
        --reload-dir . ^
        --reload-include="*.html" ^
        --reload-include="*.js" ^
        --reload-include="*.json" ^
        --reload-exclude="cache/**"
        
