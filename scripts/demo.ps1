$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $taskPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $taskPython)) { throw "Run uv sync --locked first." }
    $demoPath = "data/demo"
    if (-not (Test-Path -LiteralPath "$demoPath/questions.jsonl")) {
        & $taskPython -m datamate demo --output $demoPath
        if ($LASTEXITCODE -ne 0) { throw "Demo generation failed" }
    }
    & $taskPython -m datamate predict --data-dir $demoPath --output-dir outputs/demo
    if ($LASTEXITCODE -ne 0) { throw "Prediction failed" }
    & $taskPython -m datamate validate outputs/demo/submission.jsonl --questions "$demoPath/questions.jsonl"
    if ($LASTEXITCODE -ne 0) { throw "Submission validation failed" }
    & $taskPython -m datamate evaluate outputs/demo/submission.jsonl --reference "$demoPath/answers.jsonl" --questions "$demoPath/questions.jsonl" --output outputs/demo/metrics.json
    if ($LASTEXITCODE -ne 0) { throw "Evaluation failed" }
} finally {
    Pop-Location
}
