#define AppVersion "0.2.5"
#define AppName "FunnySinger"
#ifndef Payload
  #define Payload "..\build\release"
#endif
#ifndef Output
  #define Output "..\dist"
#endif

[Setup]
AppId={{5F81BBAA-68CC-4A1D-A947-2E9A0FC876DD}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=FunnySinger
DefaultDirName={localappdata}\Programs\CiShengStudio
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=FunnySinger-{#AppVersion}-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableDirPage=no
DisableProgramGroupPage=yes
SetupIconFile=app.ico
UninstallDisplayIcon={app}\FunnySinger.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
LicenseFile=..\docs\installer-notice.txt
InfoAfterFile=..\docs\installer-after.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\FunnySinger.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\FunnySinger.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\support\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装微软 WebView2 显示组件…"; Check: NeedsWebView; Flags: waituntilterminated runhidden
Filename: "{app}\FunnySinger.exe"; Description: "打开FunnySinger，选择需要的模型"; Flags: nowait postinstall skipifsilent

[Code]
var StoragePage: TInputDirWizardPage;

procedure InitializeWizard;
var PreviousPath: String;
begin
  StoragePage := CreateInputDirPage(wpSelectDir, '选择数据保存位置',
    '工程和模型保存在哪里？',
    '选择一个用于保存工程、音频、模型和下载组件的文件夹。升级时请继续使用原位置；选择新位置不会移动原有数据。', False, '');
  StoragePage.Add('数据文件夹：');
  PreviousPath := GetPreviousData('DataRoot', ExpandConstant('{localappdata}\CiShengStudio'));
  StoragePage.Values[0] := PreviousPath;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var AppPath, DataPath: String;
begin
  Result := True;
  if CurPageID = StoragePage.ID then begin
    AppPath := AddBackslash(ExpandFileName(WizardDirValue));
    DataPath := AddBackslash(ExpandFileName(StoragePage.Values[0]));
    if (Trim(StoragePage.Values[0]) = '') or (CompareText(Copy(DataPath, 1, Length(AppPath)), AppPath) = 0) then begin
      MsgBox('请选择程序安装目录以外的数据文件夹，以便卸载时保留工程和模型。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataRoot', ExpandFileName(StoragePage.Values[0]));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SetIniString('Storage', 'Home', ExpandFileName(StoragePage.Values[0]), ExpandConstant('{app}\storage.ini'));
end;

function NeedsWebView: Boolean;
var Version: String;
begin
  Result := not ((RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')));
end;
