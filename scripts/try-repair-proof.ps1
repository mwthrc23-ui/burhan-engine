$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$dockerImage = "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"

Push-Location $projectRoot
try {
    & python -m burhan repair-proof `
        --project examples\python-name-error `
        --goal "prove repair without changing the original" `
        --error-file examples\python-name-error\error.txt `
        --trust-local-tests `
        --backend docker `
        --docker-image $dockerImage `
        --test-program python `
        --test-arg app.py
    $proofExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $proofExitCode
