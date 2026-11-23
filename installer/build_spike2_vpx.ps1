# Build with a full x64 python.org installation (including Tcl/Tk) and Inno Setup 6.
param(
    [string]$Python = "python.exe",
    [string]$InnoSetupPath = "",
    [string]$AppVersion = "0.1.0"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
if (-not $InnoSetupPath) {
    $InnoSetupPath = (Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue |
                     Select-Object -First 1 -ExpandProperty Source)
}
if (-not $InnoSetupPath) {
    foreach ($baseDir in @("${env:ProgramFiles(x86)}", $env:ProgramFiles, "$env:LOCALAPPDATA\Programs")) {
        if (-not $baseDir) { continue }
        $candidate = Join-Path $baseDir "Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $InnoSetupPath = $candidate
            break
        }
    }
}
if (-not $InnoSetupPath -or -not (Test-Path -LiteralPath $InnoSetupPath -PathType Leaf)) {
    throw "Set -InnoSetupPath to the Inno Setup 6 ISCC.exe executable."
}
$info = & $Python -I -c "import json,platform,sys,tkinter; print(json.dumps([sys.base_prefix,platform.machine(),sys.winver]))"
if ($LASTEXITCODE -ne 0) { throw "A full x64 Python with Tcl/Tk is required." }
$base, $arch, $version = $info | ConvertFrom-Json
if ($arch -ne 'AMD64') { throw "Build with x64 Python." }

$stage = Join-Path $PSScriptRoot ('build\spike2-vpx-' + [guid]::NewGuid().ToString('N'))
$pythonDir = Join-Path $stage "python"
New-Item -ItemType Directory -Path $pythonDir -Force | Out-Null
$files = @("python.exe", "pythonw.exe", "python*.dll", "vcruntime*.dll", "LICENSE.txt", "DLLs", "tcl")
foreach ($item in $files) {
    Copy-Item -Path (Join-Path $base $item) -Destination $pythonDir -Recurse
}
$lib = Join-Path $pythonDir "Lib"
New-Item -ItemType Directory -Path $lib | Out-Null
Get-ChildItem -LiteralPath (Join-Path $base "Lib") -Exclude site-packages, __pycache__ |
    Copy-Item -Destination $lib -Recurse
$requirements = Join-Path $project "requirements-windows.txt"
$site = Join-Path $lib "site-packages"
& $Python -I -m pip install --only-binary=:all: --target $site -r $requirements
if ($LASTEXITCODE -ne 0) { throw "SPIKE 2 Python dependencies could not be staged." }

$pth = Join-Path $pythonDir ("python" + $version.Replace(".", "") + "._pth")
@(".", "DLLs", "Lib", "Lib\site-packages", "..", "import site") | Set-Content -LiteralPath $pth -Encoding ASCII
& (Join-Path $pythonDir "python.exe") -c "import pythoncom,win32com.client,olefile,sounddevice,tkinter; from Crypto.Hash import MD2; MD2.new(); tkinter.Tcl().eval('info patchlevel')"
if ($LASTEXITCODE -ne 0) { throw "The standalone Python bundle failed its import check." }

$output = Join-Path $PSScriptRoot "Output"
& $InnoSetupPath "/DProjectDir=$project" "/DPythonDir=$pythonDir" "/DAppVersion=$AppVersion" "/O$output" (Join-Path $PSScriptRoot "spike2_vpx.iss")
if ($LASTEXITCODE -ne 0) { throw "SPIKE 2 VPX installer compilation failed." }
