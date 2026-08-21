$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeFile = Join-Path $projectRoot "infra\docker-compose.advanced.yml"
$environmentFile = Join-Path $projectRoot ".env.advanced"
$exampleFile = Join-Path $projectRoot ".env.advanced.example"

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExecutable = if ($dockerCommand) { $dockerCommand.Source } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
if (-not (Test-Path -LiteralPath $dockerExecutable)) {
    throw "Docker Desktop was not found. Install and start Docker Desktop first."
}
if (-not (Test-Path -LiteralPath $environmentFile)) {
    Copy-Item -LiteralPath $exampleFile -Destination $environmentFile
    $alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._~-'
    $bytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
    } finally {
        $random.Dispose()
    }
    $generatedPassword = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
    $content = Get-Content -LiteralPath $environmentFile -Raw
    $content = $content.Replace('TDENGINE_PASSWORD=change-this-password', "TDENGINE_PASSWORD=$generatedPassword")
    Set-Content -LiteralPath $environmentFile -Value $content -Encoding UTF8
    $env:STOCK_MONITOR_TDENGINE_PASSWORD = $generatedPassword
    python -c "from stock_monitor.config import SecretStore; import os; SecretStore().set('tdengine_password', os.environ['STOCK_MONITOR_TDENGINE_PASSWORD'])"
    Remove-Item Env:STOCK_MONITOR_TDENGINE_PASSWORD
    Write-Host "Generated a strong TDengine password and saved it to Windows Credential Manager."
}

$passwordLine = Get-Content -LiteralPath $environmentFile | Where-Object { $_ -match '^TDENGINE_PASSWORD=' } | Select-Object -First 1
$tdenginePassword = if ($passwordLine) { $passwordLine.Substring('TDENGINE_PASSWORD='.Length).Trim() } else { '' }
if ($tdenginePassword -eq 'change-this-password' -or $tdenginePassword -notmatch '^[A-Za-z0-9._~-]{12,128}$') {
    throw "TDENGINE_PASSWORD must be at least 12 characters and use only letters, digits, dot, underscore, tilde, or hyphen."
}

& $dockerExecutable compose --env-file $environmentFile -f $composeFile up -d --build
if ($LASTEXITCODE -ne 0) { throw "The advanced data stack failed to start. Review the Docker output above." }
& $dockerExecutable compose --env-file $environmentFile -f $composeFile ps
Write-Host "Advanced data stack started. Flink: http://localhost:8081  TDengine: http://localhost:6041"
