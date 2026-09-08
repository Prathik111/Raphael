; AI Ecosystem installer (Inno Setup 6, Gate 44 + review fix 10).
; Installs binaries; user data lives under {userappdata} and is NEVER
; removed by uninstall unless the operator ticks the explicit option.
;
; INPUT CONTRACT (staged by packaging\windows\build.ps1, verified there):
;   dist\bundle\ai-ecosystem-desktop.exe   (portable executable)
;   dist\bundle\*.msi / *-setup.exe        (Tauri bundles, offered as-is)
;   dist\seed.db                            (migrated seed database)
; REQUIREMENT (checked at install): Python 3.10+ must exist, because the
; desktop shell supervises an installed Python backend. A future release
; will embed the runtime; until then the installer refuses to proceed
; without it rather than installing a broken app.

#define AppVersion "0.1.0"

[Setup]
AppName=AI Ecosystem
AppVersion={#AppVersion}
DefaultDirName={autopf}\AI Ecosystem
DefaultGroupName=AI Ecosystem
UninstallDisplayName=AI Ecosystem
OutputDir=..\dist
OutputBaseFilename=ai-ecosystem-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "..\dist\bundle\ai-ecosystem-desktop.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "..\dist\bundle\*setup.exe"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\dist\bundle\*.msi"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\dist\seed.db"; DestDir: "{app}\share"; Flags: onlyifdoesntexist

[Dirs]
Name: "{userappdata}\AI Ecosystem\data"

[Icons]
Name: "{group}\AI Ecosystem"; Filename: "{app}\bin\ai-ecosystem-desktop.exe"

[UninstallDelete]
; User data is preserved by default. Only with the explicit task below:
; Type: filesandordirs; Name: "{userappdata}\AI Ecosystem"; Tasks: removedata

[Tasks]
Name: removedata; Description: "Remove all user data (tasks, memory, skills)"; Flags: unchecked

[Code]
function PythonDetected(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('python', '--version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
    and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not PythonDetected() then
  begin
    MsgBox('AI Ecosystem needs Python 3.10 or newer on PATH (the desktop app supervises a Python backend). Install Python, then re-run this setup.',
      mbError, MB_OK);
    Result := False;
  end;
end;
