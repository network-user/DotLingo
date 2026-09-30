#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef AppDist
  #define AppDist "..\..\dist\DotLingo"
#endif
#ifndef SetupOut
  #define SetupOut "..\..\build\installer"
#endif

[Setup]
AppId={{E86420B8-D53B-4904-A364-5C253D4925E4}
AppName=DotLingo
AppVersion={#AppVersion}
AppPublisher=DotCore
DefaultDirName={localappdata}\Programs\DotLingo
DefaultGroupName=DotLingo
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#SetupOut}
OutputBaseFilename=DotLingo-{#AppVersion}-Setup
SetupIconFile=..\..\src\dotlingo\assets\app_icon.ico
UninstallDisplayIcon={app}\DotLingo.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
DisableDirPage=no
DisableWelcomePage=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "{#AppDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DotLingo"; Filename: "{app}\DotLingo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\DotLingo.exe"
Name: "{autodesktop}\DotLingo"; Filename: "{app}\DotLingo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\DotLingo.exe"; Tasks: desktopicon

[Code]
// WebView2 Runtime нужен pywebview (EdgeChromium). На Win11 и обновлённых Win10
// он уже установлен; иначе направляем на официальный установщик Evergreen.
function WebView2Missing(): Boolean;
var
  version: String;
begin
  Result := not RegQueryStringValue(
    HKEY_CURRENT_USER,
    'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv',
    version
  ) and not RegQueryStringValue(
    HKEY_LOCAL_MACHINE,
    'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv',
    version
  );
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WebView2Missing() then
  begin
    MsgBox(
      'Для запуска DotLingo нужен Microsoft Edge WebView2.' + #13#10 +
      'Он не найден в системе. Установите его с' + #13#10 +
      'https://developer.microsoft.com/microsoft-edge/webview2/' + #13#10 +
      'и запустите DotLingo снова.',
      mbInformation,
      MB_OK
    );
  end;
end;

[Run]
Filename: "{app}\DotLingo.exe"; Description: "Запустить DotLingo"; Flags: postinstall nowait skipifsilent
