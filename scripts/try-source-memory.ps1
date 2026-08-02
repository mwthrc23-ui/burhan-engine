$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$database = Join-Path $projectRoot "data\repair-memory.sqlite3"

Push-Location $projectRoot
try {
    python -m burhan source-import-swebench `
        --database $database `
        --offset 0 `
        --length 100

    python -m burhan source-import-bugsinpy `
        --database $database `
        --project PySnooper `
        --bug 1

    python -m burhan source-import-github-pr `
        --database $database `
        --repo astropy/astropy `
        --pr 7336

    python -m burhan source-search `
        --database $database `
        --error "AttributeError: 'NoneType' object has no attribute 'to'"
}
finally {
    Pop-Location
}
