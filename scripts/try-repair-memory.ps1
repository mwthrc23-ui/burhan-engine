$ErrorActionPreference = "Stop"

$burhanRoot = Split-Path -Parent $PSScriptRoot
$exampleRoot = Join-Path $burhanRoot "examples\repair-memory"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$demoRoot = Join-Path $tempRoot ("burhan-memory-demo-" + [guid]::NewGuid().ToString("N"))
$database = Join-Path $demoRoot "repair-memory.sqlite3"
$previousPythonPath = $env:PYTHONPATH

try {
    New-Item -ItemType Directory -Path $demoRoot | Out-Null
    $env:PYTHONPATH = Join-Path $burhanRoot "src"

    Write-Host "[1/3] Importing a documented repair episode" -ForegroundColor Cyan
    python -m burhan memory-add `
        --database $database `
        --episode (Join-Path $exampleRoot "episode-send-api.json")
    if ($LASTEXITCODE -ne 0) { throw "Memory import failed" }

    Write-Host "`n[2/3] Searching the repair memory" -ForegroundColor Cyan
    python -m burhan memory-search `
        --database $database `
        --error-file (Join-Path $exampleRoot "error.txt") `
        --language python `
        --framework pytest `
        --dependency demo-client
    if ($LASTEXITCODE -ne 0) { throw "Memory search failed" }

    Write-Host "`n[3/3] Attaching memory evidence to a fresh analysis" -ForegroundColor Cyan
    python -m burhan analyze `
        --project (Join-Path $exampleRoot "project") `
        --goal "Diagnose the API error using documented repair memory" `
        --error-file (Join-Path $exampleRoot "error.txt") `
        --memory $database `
        --dependency demo-client
    if ($LASTEXITCODE -ne 0) { throw "Memory-assisted analysis failed" }

    Write-Host "`nBurhan repair-memory demo passed." -ForegroundColor Green
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    if (Test-Path -LiteralPath $demoRoot) {
        $resolvedDemo = [System.IO.Path]::GetFullPath($demoRoot)
        if (-not $resolvedDemo.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refused to delete a path outside the system temp directory"
        }
        Remove-Item -LiteralPath $resolvedDemo -Recurse -Force
    }
}
