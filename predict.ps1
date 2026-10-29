param(
    [string]$DataDir = "data/demo",
    [string]$OutputDir = "outputs/baseline",
    [string]$Config = "configs/baseline.toml"
)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    $taskPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $taskPython)) {
        throw "Run uv sync --locked first. See README.md."
    }
    & $taskPython -m datamate predict --data-dir $DataDir --output-dir $OutputDir --config $Config
    if ($LASTEXITCODE -ne 0) { throw "Prediction failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
