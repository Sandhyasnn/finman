REM ------------------------
REM ENV CONFIG (DEV)
REM ------------------------
set FINMAN_CONFIG=dev

REM Load local authentication settings
call env_var.bat

REM Activate virtual environment (your custom script)
call finman_actv.bat

REM Start FastAPI app
REM dont add JSON files below since the rtesearch system generate it everytime causing a perpetual retart 
REM of the server

python finman.py ^
        --reload ^
        --reload-dir . ^
        --reload-include="*.html" ^
        --reload-include="*.js" ^
        --reload-exclude="cache/**"
        
