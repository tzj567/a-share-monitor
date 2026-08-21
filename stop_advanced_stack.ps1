$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeFile = Join-Path $projectRoot "infra\docker-compose.advanced.yml"
$environmentFile = Join-Path $projectRoot ".env.advanced"

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExecutable = if ($dockerCommand) { $dockerCommand.Source } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
if (-not (Test-Path -LiteralPath $dockerExecutable)) {
    throw "Docker Desktop was not found."
}
if (Test-Path -LiteralPath $environmentFile) {
    & $dockerExecutable compose --env-file $environmentFile -f $composeFile down
} else {
    & $dockerExecutable compose -f $composeFile down
}
