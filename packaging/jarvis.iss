; Inno Setup script: builds JARVIS-Setup-<version>.exe from the PyInstaller folder.
;   iscc /DAppVersion=0.2.0 packaging\jarvis.iss
; Installs per user (no admin rights), into %LOCALAPPDATA%\Programs\JARVIS.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C9C2E-5B7A-4E4B-9C4E-4A5256495331}
AppName=JARVIS
AppVersion={#AppVersion}
AppVerName=JARVIS {#AppVersion}
AppPublisher=JARVIS local assistant
DefaultDirName={localappdata}\Programs\JARVIS
DefaultGroupName=JARVIS
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=JARVIS-Setup-{#AppVersion}
SetupIconFile=jarvis.ico
UninstallDisplayIcon={app}\JARVIS.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "autostart"; Description: "Запускать JARVIS вместе с Windows (в трее)"; GroupDescription: "Запуск:"; Flags: unchecked

[Files]
Source: "dist\JARVIS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JARVIS"; Filename: "{app}\JARVIS.exe"
Name: "{group}\JARVIS — настройка (мастер)"; Filename: "{app}\JARVIS.exe"; Parameters: "gui --setup"
Name: "{group}\JARVIS — консоль"; Filename: "{cmd}"; Parameters: "/k ""{app}\jarvis-cli.exe"" --help"; WorkingDir: "{app}"
Name: "{group}\Удалить JARVIS"; Filename: "{uninstallexe}"
Name: "{userdesktop}\JARVIS"; Filename: "{app}\JARVIS.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "JARVIS"; \
  ValueData: """{app}\JARVIS.exe"" gui --minimized"; Tasks: autostart

[Run]
Filename: "{app}\JARVIS.exe"; Description: "Запустить JARVIS"; Flags: nowait postinstall skipifsilent

[Code]
// The app can enable autostart itself (System page); always remove it on uninstall.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'JARVIS');
end;
