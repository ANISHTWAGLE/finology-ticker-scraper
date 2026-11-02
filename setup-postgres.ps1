$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    New-Item -ItemType Directory -Force -Path '.secrets' | Out-Null
    if (-not (Test-Path -LiteralPath '.secrets/postgres_password')) {
        $passwordBytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($passwordBytes) } finally { $rng.Dispose() }
        [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.secrets/postgres_password'), [Convert]::ToBase64String($passwordBytes))
    }
    docker compose up -d --wait postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed.' }
    & '.\.venv\Scripts\python.exe' -m screener_scraper.database
    if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
} finally {
    Pop-Location
}
