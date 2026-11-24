[Setup]
AppId=SPIKE2-VPX
AppName=SPIKE 2 VPX
AppVersion={#AppVersion}
DefaultDirName={autopf}\SPIKE 2 VPX
DefaultGroupName=SPIKE 2 VPX
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19044
OutputBaseFilename=SPIKE2-VPX-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\python\pythonw.exe

[Files]
Source: "{#ProjectDir}\Spike2Vpx.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\spike2_vpx_setup.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\Play-Spike2Vpx.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\installer\setup_spike2_wsl.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "{#PythonDir}\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc"
Source: "{#ProjectDir}\spike2_vpx\*.py"; DestDir: "{app}\spike2_vpx"; Flags: ignoreversion
Source: "{#ProjectDir}\assets\backglasses\*.png"; DestDir: "{app}\assets\backglasses"; Flags: ignoreversion
Source: "{#ProjectDir}\tools\spike2_emu\*"; DestDir: "{app}\tools\spike2_emu"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc,games\*,shots\*,*.log,*.raw"

[Icons]
Name: "{group}\SPIKE 2 VPX"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\Spike2Vpx.pyw"""; WorkingDir: "{app}"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\Spike2Vpx.pyw"""; WorkingDir: "{app}"; Description: "Open SPIKE 2 VPX"; Flags: postinstall nowait runasoriginaluser skipifsilent

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\spike2_emu\vpx\standalone_controller.py"" unregister"; Flags: runhidden waituntilterminated; RunOnceId: "spike2-vpx-controller"
