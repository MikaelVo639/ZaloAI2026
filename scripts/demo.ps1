$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $taskPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $taskPython)) { throw "Run uv sync --locked first." }
    & $taskPython -m datamate smoke
    if ($LASTEXITCODE -ne 0) { throw "Demo acceptance failed" }
} finally {
    Pop-Location
}
