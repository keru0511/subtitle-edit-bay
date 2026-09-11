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
Name: "legacymigration"; Description: "BAT/ZIP版の設定とworkspace参照を引き継ぐ"; GroupDescription: "旧版からの移行:"; Flags: unchecked checkedonce

[Dirs]
Name: "{app}\video_import"
Name: "{app}\video_export"
Name: "{app}\out"

[Files]
Source: "{#SourceRoot}\src\*"; DestDir: "{app}\src"; Excludes: "__pycache__\*,*\__pycache__\*,*.pyc,*.pyo"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\assets\*"; DestDir: "{app}\assets"; Excludes: "speaker_colors.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\scripts\setup.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\runtime_activation.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\setup_state.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\windows_path_identity.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\runtime_contract.py"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\update.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\apply_installer_update.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\windows_signing_identity.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\validate_runtime.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\installer\launch.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceRoot}\dist\SubtitleEditBayLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\setup.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\update.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Subtitle Edit Bay"; Filename: "{app}\SubtitleEditBayLauncher.exe"; WorkingDir: "{app}"; Comment: "Subtitle Edit Bayを起動します"
Name: "{group}\初回セットアップ・修復"; Filename: "{app}\SubtitleEditBayLauncher.exe"; Parameters: "--setup"; WorkingDir: "{app}"; Comment: "依存関係をセットアップまたは修復します"
Name: "{group}\アップデート"; Filename: "{app}\SubtitleEditBayLauncher.exe"; Parameters: "--update"; WorkingDir: "{app}"; Comment: "Subtitle Edit Bayを更新します"
Name: "{group}\アンインストール"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Subtitle Edit Bay"; Filename: "{app}\SubtitleEditBayLauncher.exe"; WorkingDir: "{app}"; Comment: "Subtitle Edit Bayを起動します"; Tasks: desktopicon

[Run]
Filename: "{app}\SubtitleEditBayLauncher.exe"; Parameters: "--setup {code:SetupParameters}"; Description: "初回セットアップを実行する（インターネット接続が必要です）"; WorkingDir: "{app}"; Flags: postinstall skipifsilent; Tasks: initialsetup
; Silent GUI updates intentionally skip the optional task above. The update
; helper always runs scripts\setup.ps1 inside its application/runtime transaction.

[UninstallDelete]
; The virtual environment is generated and can be safely recreated. User settings,
; custom speaker colours, imported videos, exports and update backups are retained.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.venv.staging"
Type: filesandordirs; Name: "{app}\.venv.previous"
Type: filesandordirs; Name: "{app}\.local\runtimes"
Type: files; Name: "{app}\VERSION"

[Code]
const
  FILE_SHARE_READ = $00000001;
  FILE_SHARE_WRITE = $00000002;
  FILE_SHARE_DELETE = $00000004;
  OPEN_EXISTING = 3;
  FILE_FLAG_BACKUP_SEMANTICS = $02000000;
  INVALID_HANDLE_VALUE = -1;

function CreateFile(
  FileName: String; DesiredAccess, ShareMode, SecurityAttributes,
  CreationDisposition, FlagsAndAttributes, TemplateFile: LongWord
): Integer;
  external 'CreateFileW@kernel32.dll stdcall';
function GetFinalPathNameByHandle(
  FileHandle: Integer; FilePath: String; FilePathLength, Flags: LongWord
): LongWord;
  external 'GetFinalPathNameByHandleW@kernel32.dll stdcall';
function CloseHandle(Handle: Integer): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

var
  LegacyWorkspacePage: TInputDirWizardPage;
  MigrationComponentsPage: TInputOptionWizardPage;

function FinalDirectoryPath(Path: String): String;
var
  DirectoryHandle: Integer;
  Buffer: String;
  Length: LongWord;
  ExpandedPath: String;
  ParentPath: String;
begin
  ExpandedPath := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  if not DirExists(ExpandedPath) then
  begin
    ParentPath := ExtractFileDir(ExpandedPath);
    if CompareText(ParentPath, ExpandedPath) = 0 then
      RaiseException('フォルダーの実体を確認できません: ' + Path);
    Result := AddBackslash(FinalDirectoryPath(ParentPath)) + ExtractFileName(ExpandedPath);
    Exit;
  end;
  DirectoryHandle := CreateFile(
    ExpandedPath,
    0,
    FILE_SHARE_READ or FILE_SHARE_WRITE or FILE_SHARE_DELETE,
    0,
    OPEN_EXISTING,
    FILE_FLAG_BACKUP_SEMANTICS,
    0
  );
  if DirectoryHandle = INVALID_HANDLE_VALUE then
    RaiseException('フォルダーの実体を確認できません: ' + Path);
  try
    SetLength(Buffer, 32768);
    Length := GetFinalPathNameByHandle(DirectoryHandle, Buffer, 32768, 0);
    if (Length = 0) or (Length >= 32768) then
      RaiseException('フォルダーの最終パスを確認できません: ' + Path);
    SetLength(Buffer, Length);
    if CompareText(Copy(Buffer, 1, 8), '\\?\UNC\') = 0 then
      Result := '\\' + Copy(Buffer, 9, Length - 8)
    else if CompareText(Copy(Buffer, 1, 4), '\\?\') = 0 then
      Result := Copy(Buffer, 5, Length - 4)
    else
      Result := Buffer;
    Result := RemoveBackslashUnlessRoot(Result);
  finally
    CloseHandle(DirectoryHandle);
  end;
end;

procedure InitializeWizard;
begin
  LegacyWorkspacePage := CreateInputDirPage(
    wpSelectTasks,
    'BAT/ZIP版からの移行',
    '以前使っていたSubtitle Edit Bayフォルダーを指定してください。',
    '旧フォルダーは変更・削除されません。Python環境はInstaller用に新しく構築します。',
    False,
    ''
  );
  LegacyWorkspacePage.Add('旧BAT/ZIP版フォルダー:');
  LegacyWorkspacePage.Values[0] := ExpandConstant('{param:LEGACYWORKSPACE|}');

  MigrationComponentsPage := CreateInputOptionPage(
    LegacyWorkspacePage.ID,
    '引き継ぐデータ',
    '旧フォルダーから引き継ぐ項目を選択してください。',
    '既存のInstallerデータは上書きせず、projectや素材は旧フォルダーに残します。',
    False,
    False
  );
  MigrationComponentsPage.Add('アプリ設定（capability検証付き）');
  MigrationComponentsPage.Add('話者色');
  MigrationComponentsPage.Add('project・素材・出力の場所を登録');
  MigrationComponentsPage.Values[0] := True;
  MigrationComponentsPage.Values[1] := True;
  MigrationComponentsPage.Values[2] := True;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := ((PageID = LegacyWorkspacePage.ID) or (PageID = MigrationComponentsPage.ID)) and
    not WizardIsTaskSelected('legacymigration');
end;

function MigrationSourceError: String;
var
  LegacyPath: String;
  InstallPath: String;
begin
  Result := '';
  if not WizardIsTaskSelected('legacymigration') then
    Exit;
  LegacyPath := LegacyWorkspacePage.Values[0];
  if (LegacyPath = '') or
    not FileExists(AddBackslash(LegacyPath) + 'setup.bat') or
    not FileExists(AddBackslash(LegacyPath) + 'start.bat') or
    not DirExists(AddBackslash(LegacyPath) + 'src') then
  begin
    Result := 'setup.bat、start.bat、srcフォルダーを含む旧Subtitle Edit Bayフォルダーを指定してください。';
    Exit;
  end;
  InstallPath := ExpandConstant('{app}');
  if CompareText(FinalDirectoryPath(LegacyPath), FinalDirectoryPath(InstallPath)) = 0 then
    Result := '移行元にはインストール先とは異なる旧BAT/ZIP版フォルダーを指定してください。インストールはまだ開始されていません。';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ValidationError: String;
begin
  Result := True;
  if (CurPageID <> MigrationComponentsPage.ID) or not WizardIsTaskSelected('legacymigration') then
    Exit;
  ValidationError := MigrationSourceError;
  if ValidationError <> '' then
  begin
    if WizardSilent then
      { Do not block a silent run with the interactive page error. The same
        validation runs again in PrepareToInstall and terminates Setup. }
      Result := True
    else
    begin
      MsgBox(ValidationError, mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := MigrationSourceError;
  if (Result <> '') and WizardSilent then
  begin
    { A non-empty result leaves silent Setup waiting on the preparing page.
      Queue a close so the result is first recorded as a preparation failure;
      Inno Setup then exits with ecPrepareToInstallFailed without a dialog. }
    Log('SILENT_MIGRATION_REJECTION: ' + Result);
    if not PostMessage(WizardForm.Handle, $0010, 0, 0) then
      RaiseException('Failed to terminate Setup after silent migration validation failure.');
  end;
end;

function JsonEscape(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

function JsonBoolean(Value: Boolean): String;
begin
  if Value then
    Result := 'true'
  else
    Result := 'false';
end;

procedure SavePendingMigrationRequest;
var
  PendingDirectory: String;
  PendingPath: String;
  Payload: String;
  Lines: TArrayOfString;
begin
  PendingDirectory := ExpandConstant('{app}\.local\migration');
  PendingPath := PendingDirectory + '\pending-request.json';
  if WizardIsTaskSelected('legacymigration') then
  begin
    ForceDirectories(PendingDirectory);
    Payload := '{"schema_version":1,"source":"' + JsonEscape(ExpandFileName(LegacyWorkspacePage.Values[0])) +
      '","skip_runtime_config":' + JsonBoolean(not MigrationComponentsPage.Values[0]) +
      ',"skip_speaker_colors":' + JsonBoolean(not MigrationComponentsPage.Values[1]) +
      ',"skip_workspace_reference":' + JsonBoolean(not MigrationComponentsPage.Values[2]) + '}';
    SetArrayLength(Lines, 1);
    Lines[0] := Payload;
    if not SaveStringsToUTF8FileWithoutBOM(PendingPath, Lines, False) then
      RaiseException('保留中の移行要求を保存できませんでした。セットアップは開始されていません。');
  end
  else if not WizardSilent then
    DeleteFile(PendingPath);
end;

function SetupParameters(Param: String): String;
begin
  Result := '';
  if WizardIsTaskSelected('legacymigration') then
  begin
    Result := '--migration-source "' + LegacyWorkspacePage.Values[0] + '"';
    if not MigrationComponentsPage.Values[0] then
      Result := Result + ' --skip-runtime-config';
    if not MigrationComponentsPage.Values[1] then
      Result := Result + ' --skip-speaker-colors';
    if not MigrationComponentsPage.Values[2] then
      Result := Result + ' --skip-workspace-reference';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SavePendingMigrationRequest;
    SaveStringToFile(ExpandConstant('{app}\VERSION'), '{#AppVersion}' + #13#10, False);
  end;
end;
