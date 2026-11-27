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
SetupIconFile={#ProjectDir}\assets\spike2_vpx.ico
UninstallDisplayIcon={app}\assets\spike2_vpx.ico

[Files]
Source: "{#ProjectDir}\Spike2Vpx.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\spike2_vpx_setup.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\Play-Spike2Vpx.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\installer\setup_spike2_wsl.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "{#PythonDir}\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc"
Source: "{#ProjectDir}\spike2_vpx\*.py"; DestDir: "{app}\spike2_vpx"; Flags: ignoreversion
Source: "{#ProjectDir}\assets\spike2_vpx.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "{#ProjectDir}\assets\backglasses\*.png"; DestDir: "{app}\assets\backglasses"; Flags: ignoreversion
Source: "{#ProjectDir}\tools\spike2_emu\*"; DestDir: "{app}\tools\spike2_emu"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc,games\*,shots\*,*.log,*.raw"

[Icons]
Name: "{group}\SPIKE 2 VPX"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\Spike2Vpx.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\spike2_vpx.ico"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\Spike2Vpx.pyw"""; WorkingDir: "{app}"; Description: "Open SPIKE 2 VPX and check WSL 2 requirements"; Flags: postinstall nowait runasoriginaluser skipifsilent

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\spike2_emu\vpx\standalone_controller.py"" unregister"; Flags: runhidden waituntilterminated; RunOnceId: "spike2-vpx-controller"

[UninstallDelete]
Type: files; Name: "{app}\storage.ini"

[Code]
var
  StoragePage: TInputQueryWizardPage;

procedure BrowseStorage(Sender: TObject);
var
  Index: Integer;
  Folder: String;
begin
  Index := TNewButton(Sender).Tag;
  Folder := StoragePage.Values[Index];
  if BrowseForFolder('Choose a storage folder', Folder, True) then
    StoragePage.Values[Index] := Folder;
end;

procedure AddStorageFolder(const Caption, PreviousKey: String);
var
  Index: Integer;
  Button: TNewButton;
begin
  Index := StoragePage.Add(Caption, False);
  StoragePage.Values[Index] := GetPreviousData(PreviousKey, '');
  Button := TNewButton.Create(StoragePage);
  Button.Parent := StoragePage.Surface;
  Button.Caption := SetupMessage(msgButtonWizardBrowse);
  Button.Width := ScaleX(80);
  Button.Height := WizardForm.NextButton.Height;
  Button.Left := StoragePage.SurfaceWidth - Button.Width;
  Button.Top := StoragePage.Edits[Index].Top - ScaleY(1);
  Button.Tag := Index;
  Button.OnClick := @BrowseStorage;
  StoragePage.Edits[Index].Width := Button.Left - ScaleX(8);
end;

procedure InitializeWizard;
begin
  StoragePage := CreateInputQueryPage(wpSelectDir, 'SPIKE 2 storage',
    'Choose where the emulator, compact ZIP imports, and first-load SD-card copies are stored.',
    'Choose local Windows folders or leave them blank. The default runtime is under %LOCALAPPDATA%\Spike2Vpx, and the default cache is in WSL. Existing runtimes stay in place.');
  AddStorageFolder('Emulator runtime folder (empty folder for a new runtime)', 'RuntimeFolder');
  AddStorageFolder('SD-card cache location (uses a SPIKE2-VPX-cardcache subfolder)', 'CacheFolder');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Index: Integer;
  Folder: String;
begin
  Result := True;
  if CurPageID <> StoragePage.ID then
    Exit;
  for Index := 0 to 1 do
  begin
    Folder := Trim(StoragePage.Values[Index]);
    if Folder <> '' then
    begin
      StringChangeEx(Folder, '/', '\', True);
      if (Length(Folder) < 3) or (Copy(Folder, 2, 2) <> ':\') or
         (not DirExists(Copy(Folder, 1, 3))) or
         (FileExists(Folder)) or (Pos(':', Copy(Folder, 3, Length(Folder))) > 0) or
         (Pos('*', Folder) > 0) or (Pos('?', Folder) > 0) or
         (Pos('"', Folder) > 0) or (Pos('<', Folder) > 0) or
         (Pos('>', Folder) > 0) or (Pos('|', Folder) > 0) then
      begin
        MsgBox('Choose a folder on a Windows drive, such as D:\Spike2Vpx.', mbError, MB_OK);
        Result := False;
        Exit;
      end;
      Folder := RemoveBackslashUnlessRoot(ExpandFileName(Folder));
    end;
    StoragePage.Values[Index] := Folder;
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'RuntimeFolder', StoragePage.Values[0]);
  SetPreviousData(PreviousDataKey, 'CacheFolder', StoragePage.Values[1]);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
  if CurStep = ssPostInstall then
  begin
    SetArrayLength(Lines, 3);
    Lines[0] := '[Storage]';
    Lines[1] := 'runtime = ' + StoragePage.Values[0];
    Lines[2] := 'cache = ' + StoragePage.Values[1];
    if not SaveStringsToUTF8File(ExpandConstant('{app}\storage.ini'), Lines, False) then
      RaiseException('Could not save SPIKE 2 storage locations.');
  end;
end;
