; Script Inno Setup 6 : programme d'installation de Shelly Configurator.
; Compilé par build_setup.bat (ISCC.exe setup.iss). Chemins relatifs à ce fichier.

#define MyAppName "Shelly Configurator"
; Version : passée par build_setup.bat (lue dans shelly_configurator\__init__.py)
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExe "ShellyConfigurator.exe"
; Architecture : x64 (défaut) ou x86, passée par build_setup.bat (ISCC /DArch=x86)
#ifndef Arch
  #define Arch "x64"
#endif

[Setup]
AppId={{6F3B2C1E-8A44-4C1B-9E1D-5B7A2C9D0F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=ShellyConfigurator-Setup-{#MyAppVersion}-{#Arch}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Windows 7 SP1 minimum. La version x64 exige Windows 64 bits ; la version x86 s'installe partout.
MinVersion=6.1sp1
#if Arch == "x64"
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#endif
; Installation pour tous (admin) par défaut, ou pour l'utilisateur seul au choix
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
UninstallDisplayIcon={app}\{#MyAppExe}
CloseApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\{#Arch}\ShellyConfigurator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; La configuration, les journaux et les sauvegardes de l'utilisateur (%APPDATA%\ShellyConfigurator)
; sont volontairement conservés à la désinstallation.

[Code]
{ Python 3.8 a besoin du runtime C universel (UCRT). Présent d'office à partir de Windows 10 ;
  sous Windows 7 / 8.1, il est apporté par la mise à jour KB2999226 (Windows Update). }
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not FileExists(ExpandConstant('{sys}\ucrtbase.dll')) then
  begin
    SuppressibleMsgBox('Le runtime C universel de Windows (ucrtbase.dll) est absent de ce poste.' + #13#10#13#10 +
      'Installez la mise à jour Windows KB2999226 (ou faites les mises à jour Windows), ' +
      'puis relancez ce programme d''installation.', mbCriticalError, MB_OK, IDOK);
    Result := False;
  end;
end;

