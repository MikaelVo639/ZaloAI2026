@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "demoPython=%CD%\.venv\Scripts\python.exe"
if not exist "%demoPython%" goto missing_python
if exist "data\demo\questions.jsonl" goto predict
"%demoPython%" -m datamate demo --output data/demo
if errorlevel 1 goto failed
:predict
"%demoPython%" -m datamate predict --data-dir data/demo --output-dir outputs/demo
if errorlevel 1 goto failed
"%demoPython%" -m datamate validate outputs/demo/submission.jsonl --questions data/demo/questions.jsonl
if errorlevel 1 goto failed
"%demoPython%" -m datamate evaluate outputs/demo/submission.jsonl --reference data/demo/answers.jsonl --questions data/demo/questions.jsonl --output outputs/demo/metrics.json
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
