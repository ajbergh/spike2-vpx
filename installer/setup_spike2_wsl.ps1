# WSL platform only. The GUI imports the private Linux as the signed-in player.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("Install", "Update")][string]$Mode,
    [Parameter(Mandatory)][string]$Log
)

$ErrorActionPreference = "Stop"

function Invoke-WslPlatformSetup {
    $wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
    if ($Mode -eq "Install") {
        # As in the inherited -WslOnly path, enable WSL 2 without Ubuntu.
        # A successful feature change can explicitly report a pending restart.
        $feature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
        if ($feature.State -eq "EnablePending") { return 3010 }
        if ($feature.State -ne "Enabled") {
            $result = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
            if ($result.RestartNeeded) { return 3010 }
        }
        & $wsl --install --no-distribution --web-download | Out-Host
    } else {
        & $wsl --update --web-download | Out-Host
    }
    return $LASTEXITCODE
}

$transcribing = $false
try {
    Start-Transcript -LiteralPath $Log -Force | Out-Null
    $transcribing = $true
    $code = Invoke-WslPlatformSetup
    Write-Host "WSL platform setup exit code $code"
    exit $code
} catch {
    Write-Host $_
    exit 1
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
}
