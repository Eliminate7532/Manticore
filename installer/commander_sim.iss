; installer/commander_sim.iss - the Inno Setup 6 script for the Manticore alpha installer (Round 29, SONNET_SPEC_R29 section 5.1).
;
; Built by tools\build_installer.py:  iscc /DMyAppVersion=0.28.9 /DSourceDir=...\dist\Manticore /DOutputDir=...\installer_out
;                                     [/DIconFile=...\manticore.ico] [/DTestBuild=1]  installer\commander_sim.iss
; Sonnet could not compile this file (no Inno Setup in its workspace): the first real compile is Karl's. If iscc reports an error,
; send Claude the line it names.
;
; What it does:
;   * installs for the current user only: no administrator rights, into %LOCALAPPDATA%\Programs\Manticore;
;   * one Start-menu shortcut to Manticore.exe; a desktop shortcut is an unticked task;
;   * shows LICENSE on the licence page and installs THIRD_PARTY_NOTICES.txt and licenses\;
;   * installing over an older build first empties the program folder, so no file dropped in the new build lingers. The program
;     folder holds only program files (decks, settings, saves and logs live in the per-user folders), so this is safe;
;   * uninstalling always removes the program folder. The per-user folders (%APPDATA%\Manticore and %LOCALAPPDATA%\Manticore) are
;     removed only if the tester says yes, or with /PURGE on a silent uninstall. Forge's own folder is NEVER touched: a friend may
;     have real Forge installed.
;   * patch 41, self-updates: an installed copy starts this installer itself with
;       /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATE /LOG="<updates>\install.log"   (updater.setup_command)
;     and closes. CloseApplications=force lets Inno's Restart Manager close Manticore and its Java if they still hold a file it
;     must replace, and the last [Run] line starts the new Manticore.exe after a silent /UPDATE (the "Run Manticore" box is
;     skipped in silent mode). Before patch 41 a hidden PowerShell helper did the waiting and the restart; on Karl's Surface it
;     never got as far as starting this installer.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Manticore"
#endif
#ifndef OutputDir
  #define OutputDir "..\installer_out"
#endif

[Setup]
; The AppId is generated once and NEVER changed: installing over an older build (and the uninstaller's record) depends on it.
AppId={{1FE78D6B-8FAA-4EFE-8CE1-FD920DC97828}
AppName=Manticore
AppVersion={#MyAppVersion}
AppVerName=Manticore {#MyAppVersion}
DefaultDirName={userpf}\Manticore
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
; Patch 37: x64compatible, not x64. "x64" (Inno's old name for x64os) refused Windows 11 on Arm - Karl's Surface Pro 11, a
; Snapdragon X, said "This program does not support the version of Windows your computer is running" (Inno's
; WindowsVersionNotSupported message for a processor it isn't allowed on). x64compatible is x64 Windows plus Windows 11 on Arm,
; which runs this x64 build (Python, pygame, Java) through its x64 emulation. Windows 10 on Arm can't run x64 programs, so it
; stays refused.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#SourceDir}\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=Manticore-{#MyAppVersion}-setup
UninstallDisplayIcon={app}\Manticore.exe
UninstallDisplayName=Manticore
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Patch 41: close what still holds a file being replaced (Manticore, the java.exe of its jre\) instead of failing on it; in a
; silent install Inno closes them without asking. *.pyd and *.jar are added to Inno's default filter (exe, dll, chm).
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd,*.jar
; A log of every install in %TEMP% ("Setup Log <date> #NNN.txt"); a self-update writes updates\install.log instead (/LOG=).
SetupLogging=yes
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
#ifdef TestBuild
AppComments=Alpha TEST build
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; Install-over: empty the program folder first. UNVERIFIED (SONNET_SPEC_R29 section 5.1): that this leaves Inno's uninstaller working,
; since unins000.exe / unins000.dat are written again during the install. The Sandbox install-over check (section 7.1) tests it;
; if it breaks, list the folders to delete here one by one instead.
Type: filesandordirs; Name: "{app}\*"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Manticore"; Filename: "{app}\Manticore.exe"
Name: "{autodesktop}\Manticore"; Filename: "{app}\Manticore.exe"; Tasks: desktopicon

[Run]
; The "Run Manticore" box on the last page, ticked.
Filename: "{app}\Manticore.exe"; Description: "Run Manticore"; Flags: nowait postinstall skipifsilent
; The testers' first page (START_HERE.txt beside the program), in their text editor; ticked.
Filename: "{app}\START_HERE.txt"; Description: "Read START_HERE (first game, bug reports, known issues)"; Flags: shellexec postinstall skipifsilent nowait
; Patch 41: a silent self-update (/SILENT ... /UPDATE, from updater.launch_installer) starts the new Manticore itself. A visible
; one (/UPDATE alone, after a silent one didn't finish) leaves it to the ticked "Run Manticore" box above.
Filename: "{app}\Manticore.exe"; WorkingDir: "{app}"; Flags: nowait; Check: SilentSelfUpdate

[UninstallDelete]
; The program folder always goes (anything left in it, such as a stray log, is not the tester's data).
Type: filesandordirs; Name: "{app}"

[Code]
var
  PurgeUserData: Boolean;

function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

{ Patch 41: the self-update's relaunch ([Run], last line). }
function SilentSelfUpdate(): Boolean;
begin
  Result := HasSwitch('/UPDATE') and (HasSwitch('/SILENT') or HasSwitch('/VERYSILENT'));
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  { A silent uninstall keeps the tester's data unless /PURGE is given. An interactive one asks, with "No" as the default
    answer; /SUPPRESSMSGBOXES (what the Sandbox uses) also answers No. }
  PurgeUserData := HasSwitch('/PURGE');
  if not PurgeUserData then
    PurgeUserData := SuppressibleMsgBox(
      'Also delete my decks, settings and saved games?' + #13#10 + #13#10 +
      'Choose No to keep them (for example, to reinstall later). They are in the Manticore folders under your Windows user profile.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and PurgeUserData then
  begin
    DelTree(ExpandConstant('{userappdata}\Manticore'), True, True, True);
    DelTree(ExpandConstant('{localappdata}\Manticore'), True, True, True);
  end;
end;
