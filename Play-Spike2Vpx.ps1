# Launch a prepared SPIKE 2 title and its PAD-patched Visual Pinball table.
[CmdletBinding(DefaultParameterSetName = 'GameDir')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'GameDir')][string]$GameDir,
    [Parameter(Mandatory = $true, ParameterSetName = 'Card')][string]$Card,
    [Parameter(Mandatory = $true)][string]$Table,
    [string]$VpxExe,
    [string]$Distro,
    [string]$Python,
    [ValidateRange(1, 3600)][int]$StartupTimeout = 180
)

$ErrorActionPreference = 'Stop'
$localPython = Join-Path $PSScriptRoot 'python/python.exe'
$hasLocalPython = Test-Path -LiteralPath $localPython -PathType Leaf
if (-not $Python) {
    $bundledPython = Join-Path $env:ProgramFiles 'Pinball Asset Decryptor/python/python.exe'
    $Python = if ($hasLocalPython) { $localPython }
              elseif (Test-Path -LiteralPath $bundledPython -PathType Leaf) { $bundledPython }
              else { 'python.exe' }
}
if ($hasLocalPython -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Spike2Vpx.pyw'))) {
    if (-not $Distro -and -not $env:PAD_WSL_DISTRO) { $Distro = 'SPIKE2-VPX' }
    if (-not $VpxExe) {
        $settings = Join-Path $env:LOCALAPPDATA 'Spike2Vpx/setup.json'
        if (Test-Path -LiteralPath $settings -PathType Leaf) {
            try { $VpxExe = (Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json).vpx_exe }
            catch { Write-Verbose 'Setup settings could not be read. Searching for Visual Pinball X.' }
        }
    }
}
$launcher = Join-Path $PSScriptRoot 'tools/spike2_emu/vpx/play_spike2.py'
$launchArgs = @('--table', $Table, '--startup-timeout', $StartupTimeout)
if ($Card) { $launchArgs += @('--card', $Card) }
else { $launchArgs += @('--game-dir', $GameDir) }
if ($VpxExe) { $launchArgs += @('--vpx-exe', $VpxExe) }
if ($Distro) { $launchArgs += @('--distro', $Distro) }

& $Python $launcher @launchArgs
exit $LASTEXITCODE
