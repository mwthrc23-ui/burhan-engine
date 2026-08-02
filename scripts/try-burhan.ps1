$ErrorActionPreference = "Stop"

$burhanRoot = Split-Path -Parent $PSScriptRoot
$exampleRoot = Join-Path $burhanRoot "examples\python-name-error"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$demoRoot = Join-Path $tempRoot ("burhan-demo-" + [guid]::NewGuid().ToString("N"))
$previousPythonPath = $env:PYTHONPATH

try {
    New-Item -ItemType Directory -Path $demoRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $exampleRoot "app.py") -Destination $demoRoot
    Copy-Item -LiteralPath (Join-Path $exampleRoot "error.txt") -Destination $demoRoot
    $env:PYTHONPATH = Join-Path $burhanRoot "src"

    Write-Host "[1/3] Previewing the repair" -ForegroundColor Cyan
    python -m burhan repair `
        --project $demoRoot `
        --goal "Fix the error with the smallest change; do not change the public interface" `
        --error-file (Join-Path $demoRoot "error.txt")
    if ($LASTEXITCODE -ne 0) { throw "Repair preview failed" }

    Write-Host "`n[2/3] Applying the repair to the temporary copy" -ForegroundColor Cyan
    python -m burhan repair `
        --project $demoRoot `
        --goal "Fix the error with the smallest change; do not change the public interface" `
        --error-file (Join-Path $demoRoot "error.txt") `
        --apply
    if ($LASTEXITCODE -ne 0) { throw "Repair apply failed" }

    Write-Host "`n[3/3] Running the repaired program" -ForegroundColor Cyan
    python (Join-Path $demoRoot "app.py")
    if ($LASTEXITCODE -ne 0) { throw "Repaired program failed" }

    Write-Host "`nBurhan end-to-end demo passed." -ForegroundColor Green
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
