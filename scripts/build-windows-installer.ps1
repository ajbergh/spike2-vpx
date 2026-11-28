<#
.SYNOPSIS
Builds installer\Output\SPIKE2-VPX-Setup.exe from the local checkout.

.EXAMPLE
.\scripts\build-windows-installer.ps1 -AppVersion 0.3.0
#>
[CmdletBinding()]
param(
    [string]$Python = "python.exe",
    [string]$InnoSetupPath = "",
    [string]$AppVersion = ""
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $project "installer"
$options = @{ Python = $Python }
if ($AppVersion) {
    $options.AppVersion = $AppVersion
}
if ($InnoSetupPath) {
    $options.InnoSetupPath = $InnoSetupPath
}

& (Join-Path $installer "build_spike2_vpx.ps1") @options
Write-Host "Installer: $(Join-Path $installer 'Output\SPIKE2-VPX-Setup.exe')"
