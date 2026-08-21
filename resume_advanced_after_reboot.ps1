$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDirectory = Join-Path $env:APPDATA "AStockMonitor\logs"
$logPath = Join-Path $logDirectory "advanced-stack-startup.log"
$dockerExecutable = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$desktopExecutable = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$startupShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "AStockMonitor-ResumeAdvanced.lnk"
$succeeded = $false

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Start-Transcript -Path $logPath -Append
try {
    Write-Output "[$(Get-Date -Format s)] Resuming the A-share advanced data stack"
    if (-not (Test-Path -LiteralPath $dockerExecutable) -or -not (Test-Path -LiteralPath $desktopExecutable)) {
        throw "Docker Desktop files are missing. Reinstall Docker Desktop."
    }

    $desktop = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
    if (-not $desktop) {
        Start-Process -FilePath $desktopExecutable -WindowStyle Hidden
    }

    $ready = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        & $dockerExecutable info --format '{{.ServerVersion}}' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) {
        throw "Docker Linux Engine did not become ready within five minutes."
    }

    & (Join-Path $projectRoot "start_advanced_stack.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "The advanced stack startup script returned an error."
    }

    $flinkReady = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        try {
            $overview = Invoke-RestMethod -Uri "http://localhost:8081/overview" -TimeoutSec 5
            if ($overview) {
                $flinkReady = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 5
        }
    }
    if (-not $flinkReady) {
        throw "Flink did not become ready within five minutes."
    }

    Write-Output "[$(Get-Date -Format s)] Advanced data stack started successfully"
    $succeeded = $true
} catch {
    Write-Error $_
    throw
} finally {
    Stop-Transcript
    if ($succeeded -and (Test-Path -LiteralPath $startupShortcut)) {
        [System.IO.File]::Delete($startupShortcut)
    }
}
