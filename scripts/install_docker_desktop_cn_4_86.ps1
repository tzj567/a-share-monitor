$ErrorActionPreference = 'Stop'

$expectedVersion = '4.86.0.236216'
$expectedZipSha256 = '4DC03259DDC556BAD64E2ECA99370C6C7653A6E37C4A772D0BC74919B659C247'
$frontendDir = 'C:\Program Files\Docker\Docker\frontend'
$patchDir = 'C:\Users\20184\AppData\Local\Codex\downloads\DockerDesktop-CN-4.86.0\extracted-verified'
$zipPath = 'C:\Users\20184\AppData\Local\Codex\downloads\DockerDesktop-CN-4.86.0\app-Windows-x86.zip'
$backupRoot = 'C:\Users\20184\Documents\DockerDesktop-CN-Backups'
$resultPath = 'C:\Users\20184\AppData\Local\Codex\downloads\DockerDesktop-CN-4.86.0\install-result.txt'
$dockerLauncher = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'

function Write-InstallResult([string]$status, [string]$backupDir, [string]$details) {
    @(
        "STATUS=$status"
        "BACKUP=$backupDir"
        "DETAILS=$details"
        "TIME=$(Get-Date -Format o)"
    ) | Set-Content -LiteralPath $resultPath -Encoding UTF8
}

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator privileges are required.'
}

$installedExe = Join-Path $frontendDir 'Docker Desktop.exe'
$installedAsar = Join-Path $frontendDir 'resources\app.asar'
$installedUnpacked = Join-Path $frontendDir 'resources\app.asar.unpacked'
$patchExe = Join-Path $patchDir 'Docker Desktop.exe'
$patchAsar = Join-Path $patchDir 'app.asar'
$patchUnpacked = Join-Path $patchDir 'app.asar.unpacked'

foreach ($requiredPath in @($installedExe, $installedAsar, $installedUnpacked, $patchExe, $patchAsar, $patchUnpacked, $zipPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required path is missing: $requiredPath"
    }
}

$installedVersion = (Get-Item -LiteralPath $installedExe).VersionInfo.ProductVersion
$patchVersion = (Get-Item -LiteralPath $patchExe).VersionInfo.ProductVersion
$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash
if ($installedVersion -ne $expectedVersion -or $patchVersion -ne $expectedVersion) {
    throw "Version mismatch. Installed=$installedVersion Patch=$patchVersion Expected=$expectedVersion"
}
if ($zipHash -ne $expectedZipSha256) {
    throw "Patch archive SHA-256 mismatch: $zipHash"
}

$backupDir = Join-Path $backupRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
$backupResources = Join-Path $backupDir 'resources'
New-Item -ItemType Directory -Path $backupResources -Force | Out-Null
Copy-Item -LiteralPath $installedExe -Destination (Join-Path $backupDir 'Docker Desktop.exe')
Copy-Item -LiteralPath $installedAsar -Destination (Join-Path $backupResources 'app.asar')
Copy-Item -LiteralPath $installedUnpacked -Destination (Join-Path $backupResources 'app.asar.unpacked') -Recurse

@(
    "VERSION=$installedVersion"
    "ORIGINAL_EXE_SHA256=$((Get-FileHash -Algorithm SHA256 -LiteralPath $installedExe).Hash)"
    "ORIGINAL_ASAR_SHA256=$((Get-FileHash -Algorithm SHA256 -LiteralPath $installedAsar).Hash)"
    "PATCH_ARCHIVE_SHA256=$zipHash"
) | Set-Content -LiteralPath (Join-Path $backupDir 'manifest.txt') -Encoding UTF8

Write-InstallResult 'BACKUP_COMPLETE' $backupDir 'Original files backed up; stopping Docker Desktop.'

$dockerProcesses = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
if ($dockerProcesses) {
    $dockerProcesses | Stop-Process -Force
}
$backendProcesses = Get-Process -Name 'com.docker.backend' -ErrorAction SilentlyContinue
if ($backendProcesses) {
    $backendProcesses | Stop-Process -Force
}
Start-Sleep -Seconds 3

try {
    Copy-Item -LiteralPath $patchAsar -Destination $installedAsar -Force

    if (Test-Path -LiteralPath $installedUnpacked) {
        Rename-Item -LiteralPath $installedUnpacked -NewName 'app.asar.unpacked.pre-cn'
    }
    Copy-Item -LiteralPath $patchUnpacked -Destination $installedUnpacked -Recurse
    Copy-Item -LiteralPath $patchExe -Destination $installedExe -Force

    $asarMatches = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedAsar).Hash -eq (Get-FileHash -Algorithm SHA256 -LiteralPath $patchAsar).Hash
    $exeMatches = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedExe).Hash -eq (Get-FileHash -Algorithm SHA256 -LiteralPath $patchExe).Hash
    if (-not $asarMatches -or -not $exeMatches) {
        throw 'Post-copy hash verification failed.'
    }

    if (Test-Path -LiteralPath (Join-Path $frontendDir 'resources\app.asar.unpacked.pre-cn')) {
        Remove-Item -LiteralPath (Join-Path $frontendDir 'resources\app.asar.unpacked.pre-cn') -Recurse -Force
    }

    Write-InstallResult 'SUCCESS' $backupDir 'Chinese patch installed and copied hashes verified.'
    Start-Process -FilePath $dockerLauncher
}
catch {
    Copy-Item -LiteralPath (Join-Path $backupResources 'app.asar') -Destination $installedAsar -Force
    if (Test-Path -LiteralPath $installedUnpacked) {
        Remove-Item -LiteralPath $installedUnpacked -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $backupResources 'app.asar.unpacked') -Destination $installedUnpacked -Recurse
    Copy-Item -LiteralPath (Join-Path $backupDir 'Docker Desktop.exe') -Destination $installedExe -Force
    Write-InstallResult 'ROLLED_BACK' $backupDir $_.Exception.Message
    Start-Process -FilePath $dockerLauncher
    throw
}
