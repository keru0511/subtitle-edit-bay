#ifndef SourceRoot
  #error SourceRoot must be defined by scripts/build_installer.ps1
#endif

#ifndef AppVersion
  #error AppVersion must be defined by scripts/build_installer.ps1
#endif

#ifndef VersionInfoVersion
  #error VersionInfoVersion must be defined by scripts/build_installer.ps1
#endif

#ifndef OutputDir
  #define OutputDir SourceRoot + "\dist"
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "SubtitleEditBay-Setup"
#endif

#define AppName "Subtitle Edit Bay"
#define AppPublisher "Subtitle Edit Bay"
#define AppUrl "https://github.com/keru0511/subtitle-edit-bay"
#define AppId "{{C5A9C27D-B959-4B80-974A-944DCC919B91}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases/latest
VersionInfoVersion={#VersionInfoVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Subtitle Edit Bay
DefaultGroupName=Subtitle Edit Bay
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#SourceRoot}\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={uninstallexe}
ChangesAssociations=no
ChangesEnvironment=no

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成する"; GroupDescription: "追加アイコン:"; Flags: checkedonce
Name: "initialsetup"; Description: "インストール完了後に初回セットアップを実行する"; GroupDescription: "初回セットアップ:"; Flags: checkedonce

[Dirs]
Name: "{app}\video_import"
Name: "{app}\video_export"
Name: "{app}\out"

[Files]
Source: "{#SourceRoot}\src\*"; DestDir: "{app}\src"; Excludes: "__pycache__\*,*\__pycache__\*,*.pyc,*.pyo"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\assets\*"; DestDir: "{app}\assets"; Excludes: "speaker_colors.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\scripts\setup.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\update.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\apply_installer_update.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\installer\launch.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\dist\SubtitleEditBayLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\setup.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\update.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Subtitle Edit Bay"; Filename: "{code:LauncherExecutable}"; Parameters: "{code:LauncherParameters}"; WorkingDir: "{app}"; Comment: "Subtitle Edit Bayを起動します"
Name: "{group}\初回セットアップ・修復"; Filename: "{app}\setup.bat"; WorkingDir: "{app}"; Comment: "依存関係をセットアップまたは修復します"
Name: "{group}\アップデート"; Filename: "{app}\update.bat"; WorkingDir: "{app}"; Comment: "Subtitle Edit Bayを更新します"
Name: "{group}\アンインストール"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Subtitle Edit Bay"; Filename: "{code:LauncherExecutable}"; Parameters: "{code:LauncherParameters}"; WorkingDir: "{app}"; Comment: "Subtitle Edit Bayを起動します"; Tasks: desktopicon

[Run]
Filename: "{app}\setup.bat"; Description: "初回セットアップを実行する（インターネット接続が必要です）"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec; Tasks: initialsetup

[UninstallDelete]
; The virtual environment is generated and can be safely recreated. User settings,
; custom speaker colours, imported videos, exports and update backups are retained.
Type: filesandordirs; Name: "{app}\.venv"
Type: files; Name: "{app}\VERSION"

[Code]
function LauncherExecutable(Param: String): String;
begin
  if FileExists(ExpandConstant('{app}\SubtitleEditBayLauncher.exe')) then
    Result := ExpandConstant('{app}\SubtitleEditBayLauncher.exe')
  else
    Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function LauncherParameters(Param: String): String;
begin
  if FileExists(ExpandConstant('{app}\SubtitleEditBayLauncher.exe')) then
    Result := ''
  else
    Result := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ExpandConstant('{app}\scripts\launch.ps1') + '"';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\VERSION'), '{#AppVersion}' + #13#10, False);
end;
