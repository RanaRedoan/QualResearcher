; Inno Setup script for the QualResearcher Windows installer.
;   iscc /DAppVersion=1.0.0 packaging\installer.iss
; Installs for the current user only (no admin rights needed) into
; %LOCALAPPDATA%\Programs\QualResearcher. Transcripts and settings live in
; %LOCALAPPDATA%\QualResearcher and are kept when the app is updated or removed.

#define AppName "QualResearcher"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E2B7C1D-4F8A-4B3E-9D51-3A7C2F90B14E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Rana Redoan
AppPublisherURL=https://github.com/RanaRedoan
AppSupportURL=https://github.com/RanaRedoan/QualResearcher
AppUpdatesURL=https://github.com/RanaRedoan/QualResearcher/releases/latest
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=QualResearcher-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\QualResearcher.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; remove the previous version's program files so nothing old is left behind on update
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\QualResearcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\QualResearcher.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\QualResearcher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\QualResearcher.exe"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
