; AI Ecosystem installer (Inno Setup 6, Gate 44).
; Installs binaries; user data lives under {userappdata} and is NEVER
; removed by uninstall unless the operator ticks the explicit option.

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
Source: "..\dist\bundle\*"; DestDir: "{app}\bin"; Flags: ignoreversion recursesubdirs
Source: "..\dist\seed.db"; DestDir: "{app}\share"; Flags: onlybelowversion

[Dirs]
Name: "{userappdata}\AI Ecosystem\data"

[Icons]
Name: "{group}\AI Ecosystem"; Filename: "{app}\bin\ai-ecosystem.exe"

[UninstallDelete]
; User data is preserved by default. Only with the explicit task below:
; Type: filesandordirs; Name: "{userappdata}\AI Ecosystem"; Tasks: removedata

[Tasks]
Name: removedata; Description: "Remove all user data (tasks, memory, skills)"; Flags: unchecked
