; PaperWorkShell installer. Project-owned code: MIT; see ../../LICENSE.
; Generated include contains only validated, build-owned values and exact file entries.
#ifndef PWSGeneratedConfig
  #error PWSGeneratedConfig is required; use tools.build_windows_installer.
#endif
#include PWSGeneratedConfig
; Keep the native library explicit without email-shaped import declarations in source.
#define PWSKernel32 "kernel32.dll"

[Setup]
AppId={{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}
AppName=PaperWorkShell
AppVersion={#PWSVersion}
AppVerName=PaperWorkShell {#PWSVersion} ({#PWSChannelLabel})
AppPublisher=Janne Vuorela
AppPublisherURL=https://github.com/PikkuJanne/PaperWorkShell
VersionInfoCompany=Janne Vuorela
VersionInfoProductName=PaperWorkShell
VersionInfoDescription=PaperWorkShell {#PWSChannelLabel} installer
VersionInfoCopyright=Copyright (c) 2026 Janne Vuorela
SetupArchitecture=x64
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.22000
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\PaperWorkShell
DisableDirPage=yes
UsePreviousAppDir=no
DefaultGroupName=PaperWorkShell
DisableProgramGroupPage=yes
UsePreviousGroup=no
UsePreviousTasks=no
UsePreviousLanguage=no
ChangesAssociations=no
ChangesEnvironment=no
CloseApplications=no
RestartApplications=no
; Script owns this mutex through install/remove, including after the data-choice helper.
AllowCancelDuringInstall=yes
AlwaysRestart=no
RestartIfNeededByRun=no
WizardStyle=modern dynamic
WizardResizable=yes
Compression=zip/9
SolidCompression=no
DiskSpanning=no
SetupLogging=no
Uninstallable=yes
CreateUninstallRegKey=yes
UninstallDisplayIcon={app}\runtime\PaperWorkShell.exe
UninstallLogMode=append
LicenseFile={#PWSLicenseFile}
InfoBeforeFile={#PWSInformationFile}
OutputDir={#PWSOutputDirectory}
OutputBaseFilename={#PWSOutputName}
#if PWSSignedBuild
SignedUninstaller=yes
SignedUninstallerDir={#PWSSignedUninstallerDirectory}
SignTool=pws_approved_signer
#else
SignedUninstaller=no
#endif

#include PWSGeneratedFiles

[Icons]
Name: "{autoprograms}\PaperWorkShell"; Filename: "{app}\runtime\PaperWorkShell.exe"; WorkingDir: "{app}\runtime"; AppUserModelID: "io.github.pikkujanne.paperworkshell"
Name: "{autoprograms}\PaperWorkShell notices"; Filename: "{app}\runtime\PaperWorkShell.exe"; Parameters: "--notices"; WorkingDir: "{app}\runtime"
Name: "{autoprograms}\PaperWorkShell Updates"; Filename: "{app}\runtime\PaperWorkShell.exe"; Parameters: "--update"; WorkingDir: "{app}\runtime"

[UninstallDelete]
Type: files; Name: "{app}\update-state-v1"
Type: files; Name: "{app}\update-state-pending-v1.tmp"

[Code]
const
  ReparsePoint = $400;
  InvalidAttributes = $FFFFFFFF;

type
  FileInformation = record
    Attributes: LongWord;
    CreationLow, CreationHigh: LongWord;
    AccessLow, AccessHigh: LongWord;
    WriteLow, WriteHigh: LongWord;
    VolumeSerial, SizeHigh, SizeLow, NumberOfLinks: LongWord;
    IndexHigh, IndexLow: LongWord;
  end;

var
  LifecycleMutex: NativeInt;
  InstallationLease: NativeInt;

function FileAttributes(const FileName: String): LongWord;
  external 'GetFileAttributesW@{#PWSKernel32} stdcall';
function OpenAttributes(const FileName: String; Access, Share: LongWord;
  Security: NativeInt; Creation, Flags: LongWord; Template: NativeInt): NativeInt;
  external 'CreateFileW@{#PWSKernel32} stdcall';
function FileIdentity(Handle: NativeInt; var Information: FileInformation): Boolean;
  external 'GetFileInformationByHandle@{#PWSKernel32} stdcall';
function CloseAttributeHandle(Handle: NativeInt): Boolean;
  external 'CloseHandle@{#PWSKernel32} stdcall';
function CreateLifecycleMutex(Security: NativeInt; InitialOwner: Boolean; const Name: String): NativeInt;
  external 'CreateMutexW@{#PWSKernel32} stdcall';
function LastSystemError: LongWord;
  external 'GetLastError@{#PWSKernel32} stdcall';
function ReleaseLifecycleMutex(Handle: NativeInt): Boolean;
  external 'ReleaseMutex@{#PWSKernel32} stdcall';

function AcquireLifecycle: Boolean;
var
  Information: FileInformation;
begin
  InstallationLease := -1;
  LifecycleMutex := CreateLifecycleMutex(0, True, 'Local\io.github.pikkujanne.paperworkshell.App');
  Result := (LifecycleMutex <> 0) and (LastSystemError <> 183);
  if not Result and (LifecycleMutex <> 0) then begin
    CloseAttributeHandle(LifecycleMutex);
    LifecycleMutex := 0;
  end;
  if not Result then Exit;
  InstallationLease := OpenAttributes(ExpandConstant('{localappdata}\PaperWorkShell.lifecycle-v1.lock'),
    $C0000000, 0, 0, 4, $00200000, 0);
  Result := (InstallationLease <> -1) and FileIdentity(InstallationLease, Information) and
    (Information.NumberOfLinks = 1) and ((Information.Attributes and (ReparsePoint or $10)) = 0);
  if not Result and (InstallationLease <> -1) then begin
    CloseAttributeHandle(InstallationLease);
    InstallationLease := -1;
  end;
end;

procedure ReleaseLifecycle;
begin
  if InstallationLease > 0 then begin
    CloseAttributeHandle(InstallationLease);
    InstallationLease := -1;
  end;
  if LifecycleMutex <> 0 then begin
    ReleaseLifecycleMutex(LifecycleMutex);
    CloseAttributeHandle(LifecycleMutex);
    LifecycleMutex := 0;
  end;
end;

function HasPrefix(const Value, Prefix: String): Boolean;
begin
  Result := Copy(Uppercase(Value), 1, Length(Prefix)) = Uppercase(Prefix);
end;

function UnsafeSwitches: Boolean;
var
  I: Integer;
  Value: String;
begin
  Result := False;
  for I := 1 to ParamCount do begin
    Value := ParamStr(I);
    if HasPrefix(Value, '/DIR') or HasPrefix(Value, '/LOADINF') or
       HasPrefix(Value, '/SAVEINF') or HasPrefix(Value, '/LOG=') or
       HasPrefix(Value, '/ALLUSERS') or HasPrefix(Value, '/TYPE') or
       HasPrefix(Value, '/COMPONENTS') or HasPrefix(Value, '/TASKS') or
       HasPrefix(Value, '/MERGETASKS') or HasPrefix(Value, '/GROUP') or
       HasPrefix(Value, '/FORCECLOSEAPPLICATIONS') or
       HasPrefix(Value, '/RESTARTAPPLICATIONS') or HasPrefix(Value, '/NOCANCEL') or
       HasPrefix(Value, '/NOREDIRECTIONGUARD') then begin
      Result := True;
      Exit;
    end;
  end;
end;

function SafeAncestors(Path: String): Boolean;
var
  Attributes: LongWord;
  Parent: String;
begin
  Result := False;
  while Length(Path) > 3 do begin
    Attributes := FileAttributes(Path);
    if (Attributes <> InvalidAttributes) and ((Attributes and ReparsePoint) <> 0) then Exit;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Exit;
    Path := Parent;
  end;
  Result := True;
end;

function FixedDirectory: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\PaperWorkShell');
end;

function SafeTarget(const Path: String): Boolean;
var
  Handle: NativeInt;
  Information: FileInformation;
begin
  Result := False;
  if not SafeAncestors(Path) or DirExists(Path) then Exit;
  if not FileExists(Path) then begin
    Result := True;
    Exit;
  end;
  Handle := OpenAttributes(Path, $80, $7, 0, 3, $2200000, 0);
  if Handle = -1 then Exit;
  try
    Result := FileIdentity(Handle, Information) and (Information.NumberOfLinks = 1) and
      ((Information.Attributes and ReparsePoint) = 0);
  finally
    CloseAttributeHandle(Handle);
  end;
end;

function SafePayload: Boolean;
begin
  Result := False;
#include PWSGeneratedChecks
  Result := True;
end;

function UpdateSlotsCleared: Boolean;
var
  Root: String;
begin
  Root := AddBackslash(FixedDirectory);
  Result := SafeTarget(Root + 'update-state-v1') and
    SafeTarget(Root + 'update-state-pending-v1.tmp') and
    (FileAttributes(Root + 'update-transport') = InvalidAttributes) and
    (FileAttributes(Root + 'update-slot-A') = InvalidAttributes) and
    (FileAttributes(Root + 'update-slot-B') = InvalidAttributes) and
    (FileAttributes(Root + 'update-slot-C') = InvalidAttributes);
end;

function ShowFailure(const Text: String): Boolean;
begin
  SuppressibleMsgBox(Text, mbError, MB_OK, IDOK);
  Result := False;
end;

function SilentRecipientAgreement: Boolean;
#if PWSCompleteRuntime
var
  I, Count: Integer;
  Value: String;
  Matches: Boolean;
#endif
begin
  Result := True;
#if PWSCompleteRuntime
  if not WizardSilent then Exit;
  Count := 0;
  Matches := False;
  for I := 1 to ParamCount do begin
    Value := ParamStr(I);
    if HasPrefix(Value, '/ACCEPTMICROSOFTRUNTIME=') then begin
      Count := Count + 1;
      Matches := CompareText(Value, '/ACCEPTMICROSOFTRUNTIME={#PWSRecipientTermsSHA256}') = 0;
    end;
  end;
  Result := (Count = 1) and Matches;
#endif
end;

function InitializeSetup: Boolean;
begin
  Result := False;
  if IsAdmin then begin
    ShowFailure('PWS_INSTALL_PRIVILEGE: Run this per-user installer normally, without administrator privileges.');
    Exit;
  end;
  if UnsafeSwitches then begin
    ShowFailure('PWS_INSTALL_OPTION: This installer does not support changing its destination, data scope, or application-close policy.');
    Exit;
  end;
  if not SilentRecipientAgreement then begin
    ShowFailure('PWS_INSTALL_MICROSOFT_TERMS: Review the included Microsoft-only runtime terms. For a quiet installation, explicitly accept the exact published terms hash or run the interactive installer. No application files were installed.');
    Exit;
  end;
  if not AcquireLifecycle then begin
    ShowFailure('PWS_INSTALL_BUSY: Close PaperWorkShell and its data-choice review, then try again.');
    Exit;
  end;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Marker: String;
begin
  Result := '';
  NeedsRestart := False;
  if (CompareText(WizardDirValue, FixedDirectory) <> 0) or not SafeAncestors(FixedDirectory) then begin
    Result := 'PWS_INSTALL_DESTINATION: The installation folder is unavailable or redirected. No application files were installed.';
    Exit;
  end;
  if not SafePayload then begin
    Result := 'PWS_INSTALL_TARGET: An application target is linked, redirected, or inaccessible. Existing files were preserved.';
    Exit;
  end;
  Marker := AddBackslash(FixedDirectory) + 'runtime\paperworkshell-installed-v1.json';
  if FileExists(Marker) then begin
    if GetSHA256OfFile(Marker) <> '{#PWSMarkerSHA256}' then begin
      Result := 'PWS_INSTALL_CANDIDATE: A different candidate is installed. Keep it intact; use its documented update or removal path.';
      Exit;
    end;
  end else if DirExists(FixedDirectory) then begin
    Result := 'PWS_INSTALL_EXISTING_FOLDER: The installation folder already exists without this candidate identity. Existing files were preserved.';
    Exit;
  end;
#if !PWSCompleteRuntime
  if not FileExists(ExpandConstant('{sys}\vcruntime140.dll')) or
     not FileExists(ExpandConstant('{sys}\vcruntime140_1.dll')) or
     not FileExists(ExpandConstant('{sys}\msvcp140.dll')) then begin
    Result := 'PWS_INSTALL_RUNTIME_PENDING: This internal candidate requires the Microsoft Visual C++ runtime. Its bundled redistribution is not yet approved. No application files were installed.';
    Exit;
  end;
#endif
end;

function InitializeUninstall: Boolean;
var
  ExitStatus: Integer;
begin
  Result := False;
  if IsAdmin then begin
    ShowFailure('PWS_REMOVE_PRIVILEGE: Run application removal normally, without administrator privileges.');
    Exit;
  end;
  if UnsafeSwitches or not SafeAncestors(ExpandConstant('{app}')) or not SafePayload then begin
    ShowFailure('PWS_REMOVE_OPTION: The removal scope is unavailable or unsupported. Application files and cases were preserved.');
    Exit;
  end;
  if not UpdateSlotsCleared then begin
    ShowFailure('PWS_REMOVE_UPDATES: Open PaperWorkShell Updates and remove installed updates before uninstalling PaperWorkShell. Your saved cases are kept separately.');
    Exit;
  end;
  if UninstallSilent then begin
    Result := AcquireLifecycle;
    if Result then Result := SafePayload and UpdateSlotsCleared;
    if not Result then ShowFailure('PWS_REMOVE_BUSY: Close PaperWorkShell and try application removal again.');
    Exit;
  end;
  if not Exec(ExpandConstant('{app}\runtime\PaperWorkShell.exe'), '--review-uninstall',
      ExpandConstant('{app}\runtime'), SW_SHOWNORMAL, ewWaitUntilTerminated, ExitStatus) then begin
    ShowFailure('PWS_REMOVE_REVIEW: The data-choice review could not start. Application files and cases were preserved.');
    Exit;
  end;
  if ExitStatus <> 0 then Exit;
  Result := AcquireLifecycle;
  if Result then Result := SafePayload and UpdateSlotsCleared;
  if not Result then ShowFailure('PWS_REMOVE_BUSY: Close PaperWorkShell and try application removal again.');
end;

procedure DeinitializeSetup;
begin
  ReleaseLifecycle;
end;

procedure DeinitializeUninstall;
begin
  ReleaseLifecycle;
end;

function UninstallNeedRestart: Boolean;
begin
  Result := False;
end;
