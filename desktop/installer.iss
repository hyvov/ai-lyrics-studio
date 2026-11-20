#define AppVersion "0.2.3"
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
function NeedsWebView: Boolean;
var Version: String;
begin
  Result := not ((RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')));
end;
