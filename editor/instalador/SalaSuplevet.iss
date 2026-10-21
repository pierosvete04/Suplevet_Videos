; Instalador de la Sala de edición (Inno Setup). Lo compila GitHub Actions en cada versión:
;   iscc /DVersion=1.5.1 /DOrigen=..\dist\SalaSuplevet instalador\SalaSuplevet.iss
; Se instala en la carpeta del usuario (no pide permisos de administrador), así la propia sala
; puede actualizarse sola después. Los datos (datos\) nunca se borran: ni al actualizar ni al desinstalar.

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef Origen
  #define Origen "..\dist\SalaSuplevet"
#endif

[Setup]
AppId={{6E1F4A2C-5B7D-4C1E-9A3B-5355504C4556}
AppName=Sala de edición
AppVersion={#Version}
AppPublisher=Piero Svete
DefaultDirName={localappdata}\SalaSuplevet
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..
OutputBaseFilename=SalaSuplevet-Instalador-v{#Version}
SetupIconFile=icono.ico
UninstallDisplayIcon={app}\SalaSuplevet.exe
UninstallDisplayName=Sala de edición
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "{#Origen}\*"; DestDir: "{app}"; Excludes: "datos\*"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "icono.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\datos"; Flags: uninsneveruninstall

[InstallDelete]
; accesos directos con el nombre anterior
Type: files; Name: "{userprograms}\Sala de edición Suplevet.lnk"
Type: files; Name: "{userdesktop}\Sala de edición Suplevet.lnk"
Type: files; Name: "{userprograms}\Estudio Suplevet.lnk"
Type: files; Name: "{userdesktop}\Estudio Suplevet.lnk"

[Icons]
Name: "{userprograms}\Sala de edición"; Filename: "{app}\SalaSuplevet.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icono.ico"
Name: "{userdesktop}\Sala de edición"; Filename: "{app}\SalaSuplevet.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icono.ico"; Tasks: escritorio

[Run]
Filename: "{app}\SalaSuplevet.exe"; Description: "Abrir la Sala de edición ahora"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
