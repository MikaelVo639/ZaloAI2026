@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "demoPython=%CD%\.venv\Scripts\python.exe"
if not exist "%demoPython%" goto missing_python
"%demoPython%" -m datamate smoke
if errorlevel 1 goto failed
echo.
echo Demo complete. Results: outputs\demo
set "demoExit=0"
goto finish
:missing_python
echo Python environment missing. Open a terminal here and run: uv sync --locked
set "demoExit=1"
goto finish
:failed
set "demoExit=%errorlevel%"
echo.
echo Demo failed. See the error above.
:finish
popd
if /I not "%~1"=="--no-pause" pause
exit /b %demoExit%
