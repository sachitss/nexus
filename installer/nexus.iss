; Inno Setup script for Nexus Compare & Editor (Windows 10/11, per-user or per-machine)
; Build first:  build_windows.bat   (creates dist\Nexus\Nexus.exe)
; Then:         "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\nexus.iss
#define AppName "Nexus Compare & Editor"
#define AppVersion "0.3.0"
#define AppExe "Nexus.exe"

[Setup]
AppId={{6C1E3F5A-2B7D-4E61-9C0B-7A3D2E8F4B11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Ing.-Büro Sachit Shrestha
AppPublisherURL=https://github.com/sachitss/nexus
AppSupportURL=https://github.com/sachitss/nexus/issues
AppUpdatesURL=https://github.com/sachitss/nexus/releases
AppCopyright=© 2026 Ing.-Büro Sachit Shrestha · Team Nepal Solutions
AppComments=Powered by Ing.-Büro Sachit Shrestha
VersionInfoCompany=Ing.-Büro Sachit Shrestha
VersionInfoDescription={#AppName} setup
VersionInfoCopyright=© 2026 Ing.-Büro Sachit Shrestha
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\Nexus
DefaultGroupName=Nexus
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=Nexus-{#AppVersion}-setup
SetupIconFile=..\nexus\resources\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "sendto"; Description: "Add 'Nexus Compare' to the Explorer 'Send to' menu"; Flags: unchecked

[Files]
Source: "..\dist\Nexus\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\samples\*"; DestDir: "{app}\samples"; Flags: recursesubdirs ignoreversion skipifsourcedoesntexist
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\User guide"; Filename: "{app}\docs\user-guide.md"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{usersendto}\Nexus Compare"; Filename: "{app}\{#AppExe}"; Parameters: "-c"; Tasks: sendto

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
