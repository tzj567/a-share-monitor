$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
python -m pip install -e ".[desktop-build,streaming]"
python -m PyInstaller --noconfirm --clean desktop.spec
Write-Host "桌面程序已生成：$projectRoot\dist\A股量化监控.exe"
