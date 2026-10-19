; Build through build_installer.py: PayloadDir contains only windows_files().
#ifndef AppVersion
  #error AppVersion must be supplied by build_installer.py
#endif

[Setup]
AppId=Netfreak25.DDMYX6.Flasher
AppName=DDMY X6 Flasher
AppVersion={#AppVersion}
AppPublisher=Netfreak25
AppPublisherURL=https://github.com/Netfreak25/DDMY-X6-Flasher
DefaultDirName={autopf}\DDMY-X6-Flasher
DefaultGroupName=DDMY X6 Flasher
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename=DDMY-X6-Flasher-Setup
SetupIconFile={#ProjectDir}\assets\x6-icon.ico
UninstallDisplayIcon={app}\DDMY X6 Flasher.exe
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AlwaysRestart=no
SetupLogging=yes

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DDMY X6 Flasher"; Filename: "{app}\DDMY X6 Flasher.exe"
Name: "{autodesktop}\DDMY X6 Flasher"; Filename: "{app}\DDMY X6 Flasher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DDMY X6 Flasher.exe"; Description: "DDMY X6 Flasher öffnen"; Flags: nowait postinstall skipifsilent unchecked

[Code]
function RunningAppError: String;
var
  Locator, Services, Processes: Variant;
begin
  Result := '';
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('', 'root\CIMV2');
    Processes := Services.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name = ''DDMY X6 Flasher.exe''');
    if Processes.Count > 0 then
      Result := 'Die X6-App läuft noch. Bitte Lesen, Schreiben und Prüfen vollständig abwarten, in Pages trennen und die App schließen. Das Setup beendet keine laufenden Aufträge.';
  except
    Result := 'Der Status der X6-App konnte nicht geprüft werden. Installation gestoppt: ' + GetExceptionMessage;
  end;
end;

function InitializeSetup: Boolean;
var
  Error: String;
begin
  Error := RunningAppError;
  Result := Error = '';
  if not Result then begin
    Log(Error);
    SuppressibleMsgBox(Error, mbError, MB_OK, IDOK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Recheck just before copying; never let Restart Manager terminate the app. }
  Result := RunningAppError;
end;

function InitializeUninstall: Boolean;
var
  Error: String;
begin
  Error := RunningAppError;
  Result := Error = '';
  if not Result then begin
    Log(Error);
    SuppressibleMsgBox(Error, mbError, MB_OK, IDOK);
  end;
end;
