[CmdletBinding()]
param(
    [string]$RepoRoot = (Join-Path $PSScriptRoot ".."),
    [string]$DestinationRoot = (Join-Path $env:USERPROFILE ".codex\skills")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-NormalizedPath {
    param([string]$LiteralPath)
    return [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $LiteralPath).Path)
}

function Test-IsChildPath {
    param(
        [string]$ParentPath,
        [string]$ChildPath
    )

    $normalizedParent = $ParentPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $normalizedChild = $ChildPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    if ($normalizedParent.Equals($normalizedChild, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $normalizedParent + [System.IO.Path]::DirectorySeparatorChar
    return $normalizedChild.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

$repoRootPath = Resolve-NormalizedPath -LiteralPath $RepoRoot
$sourceSkillPath = Resolve-NormalizedPath -LiteralPath (Join-Path $repoRootPath "skills\deepseek-memory-bridge")
if (-not (Test-IsChildPath -ParentPath $repoRootPath -ChildPath $sourceSkillPath)) {
    throw "Source skill directory must stay inside the repository checkout."
}

$destinationSkillPath = [System.IO.Path]::GetFullPath((Join-Path $DestinationRoot "deepseek-memory-bridge"))
$itemsToCopy = @("SKILL.md", "agents", "assets", "references", "scripts")

New-Item -ItemType Directory -Path $destinationSkillPath -Force | Out-Null
foreach ($item in $itemsToCopy) {
    $sourceItem = Join-Path $sourceSkillPath $item
    $destinationItem = Join-Path $destinationSkillPath $item
    if (-not (Test-Path -LiteralPath $sourceItem)) {
        continue
    }
    if (Test-Path -LiteralPath $destinationItem) {
        Remove-Item -LiteralPath $destinationItem -Recurse -Force
    }
    Copy-Item -LiteralPath $sourceItem -Destination $destinationItem -Recurse -Force
}

Write-Host "Installed deepseek-memory-bridge to $destinationSkillPath"
