; SkyGuard QA cumulative upgrade wizard: SGQA-23.3.35 (or any later release up to 23.11.1) to the latest release.
;
; Welcome -> Detect -> Backup -> Install -> Verify -> Finish. Per user, no administrator rights (RequestExecutionLevel
; user), offline. The work is done by install_patch.py with the application's own Python (the same installer the
; ZIP's manual APPLY_PATCH.cmd uses): it verifies every file (SHA-256), backs up, copies, migrates, verifies and
; restores everything automatically if any step fails. Nothing machine-wide is changed: no machine registry, no system PATH,
; no services, no writes to protected folders (a protected application folder gets a patched side-by-side copy).
;
; Built on the build machine by packaging/setup/build_release.py:
;   makensis -DVERSION=23.11.2 -DPAYLOAD=<patch folder> -DOUTFILE=<Setup.exe> SkyGuardSetup.nsi
;
; Unattended (for IT, or a test):
;   Setup.exe /S /APP=<installation folder> [/SIDEBYSIDE] [/NOSTART] [/KEEPRUNNING]
;   exit code 0 = upgraded and verified, 1 = failed (previous version restored), 2 = no installation found,
;   3 = version not supported (nothing changed), 4 = SkyGuard QA is running and /KEEPRUNNING was given

Unicode true
Target amd64-unicode     ; 64-bit, like the application (64-bit Windows 10 1909 or later)
ManifestDPIAware true
ManifestSupportedOS all
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64

!ifndef VERSION
  !error "VERSION is required (-DVERSION=23.11.2)"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD is required (-DPAYLOAD=<patch folder>)"
!endif
!ifndef OUTFILE
  !define OUTFILE "SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe"
!endif

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinMessages.nsh"
!include "TextFunc.nsh"
!include "WordFunc.nsh"

Name "SkyGuard QA ${VERSION} Upgrade"
Caption "SkyGuard QA ${VERSION} Upgrade"
OutFile "${OUTFILE}"
BrandingText "SkyGuard QA ${VERSION} - offline, no administrator rights"
ShowInstDetails show
InstallDir "$LOCALAPPDATA\Programs\AIAgenticTestAgent"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "SkyGuard QA"
VIAddVersionKey "FileDescription" "SkyGuard QA ${VERSION} cumulative upgrade (from 23.3.35)"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "SkyGuard QA"

Var AppDir        ; folder the person chose (holds app or qa_platform)
Var App           ; the folder that holds qa_platform
Var Py            ; the application's own python.exe
Var Ini           ; detect result
Var Supported
Var Found
Var Version
Var DataDir
Var RootDir
Var Writable
Var Running
Var Port
Var Message
Var SideBySide
Var NoStart
Var KeepRunning
Var LogFile
Var VerifyLog
Var Result        ; ok | failed
Var FinishTitle
Var FinishText
Var Launcher
Var Warnings      ; WARN lines of the verification (an optional part that does not work, for example the OCR engine)
Var TargetApp     ; the application folder that was patched (the side-by-side copy when protected)
; Detect page controls
Var hDir
Var hResult
Var hStop

!define MUI_ABORTWARNING
!define MUI_ABORTWARNING_TEXT "Cancel the upgrade? Nothing has been changed yet."
!define MUI_WELCOMEPAGE_TITLE "Upgrade SkyGuard QA to ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "This wizard upgrades an installed SkyGuard QA (version 23.3.35 or any later release up to 23.11.1) to ${VERSION} in one step. It includes every release in between.$\r$\n$\r$\n\
What you get: the AI Agent understands answers to Missing information (for example First Name; muhammad), keeps them and asks only for what is still missing; and everything from 23.4.0 to 23.11.1 (including the repaired AI Agent, editable Python tests, Smart Wait and Defect Review).$\r$\n$\r$\n\
It works offline, needs no administrator rights and changes nothing outside the SkyGuard QA folders. Your data is backed up first and is never deleted; if anything fails, the previous version is restored automatically.$\r$\n$\r$\n\
Close SkyGuard QA if it is open, then press Next."
!insertmacro MUI_PAGE_WELCOME
Page custom DetectPage DetectLeave
Page custom BackupPage BackupLeave
!define MUI_PAGE_HEADER_TEXT "Installing"
!define MUI_PAGE_HEADER_SUBTEXT "Checking the files, backing up, copying, migrating and verifying."
!define MUI_INSTFILESPAGE_FINISHHEADER_TEXT "Upgrade finished"
!define MUI_INSTFILESPAGE_FINISHHEADER_SUBTEXT "Press Next to see the verification."
!define MUI_INSTFILESPAGE_ABORTHEADER_TEXT "Upgrade stopped"
!define MUI_INSTFILESPAGE_ABORTHEADER_SUBTEXT "The previous version was kept."
!insertmacro MUI_PAGE_INSTFILES
Page custom VerifyPage
!define MUI_FINISHPAGE_TITLE "$FinishTitle"
!define MUI_FINISHPAGE_TEXT "$FinishText"
!define MUI_FINISHPAGE_TEXT_LARGE  ; room for the whole text above the two check boxes
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start SkyGuard QA now"
!define MUI_FINISHPAGE_RUN_FUNCTION StartApp
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Open the installation log"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION OpenLog
!define MUI_PAGE_CUSTOMFUNCTION_PRE FinishPre
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

; ---- detection ------------------------------------------------------------------------------------------------------

Function FindApp ; $AppDir -> $App, $Py (empty when not found)
  StrCpy $App ""
  StrCpy $Py ""
  ${If} ${FileExists} "$AppDir\app\qa_platform\__init__.py"
    StrCpy $App "$AppDir\app"
  ${ElseIf} ${FileExists} "$AppDir\qa_platform\__init__.py"
    StrCpy $App "$AppDir"
  ${EndIf}
  ${If} $App != ""
    ${If} ${FileExists} "$App\runtime\python\python.exe"
      StrCpy $Py "$App\runtime\python\python.exe"
    ${ElseIf} ${FileExists} "$App\.venv\Scripts\python.exe"
      StrCpy $Py "$App\.venv\Scripts\python.exe"
    ${EndIf}
  ${EndIf}
FunctionEnd

Function Detect ; runs install_patch.py detect (reads only) and loads its answer
  StrCpy $Supported "0"
  StrCpy $Found "0"
  StrCpy $Version ""
  Call FindApp
  ${If} $App == ""
    StrCpy $Message "No SkyGuard QA installation was found in this folder. Choose the folder that holds 'app' (or 'qa_platform'), for example $LOCALAPPDATA\Programs\AIAgenticTestAgent."
    Return
  ${EndIf}
  ${If} $Py == ""
    StrCpy $Found "1"
    StrCpy $Message "SkyGuard QA was found in $App, but its Python (runtime\python\python.exe or .venv\Scripts\python.exe) is missing, so it cannot be upgraded safely. Nothing will be changed."
    Return
  ${EndIf}
  Delete "$Ini"
  nsExec::ExecToStack '"$Py" -E -s "$PLUGINSDIR\patch\install_patch.py" detect --app "$AppDir" --python "$Py" --ini "$Ini"'
  Pop $0
  Pop $1
  ${IfNot} ${FileExists} "$Ini"
    StrCpy $Message "The installation could not be read with its own Python ($Py). Nothing will be changed. Details: $1"
    Return
  ${EndIf}
  ReadINIStr $Found "$Ini" "detect" "found"
  ReadINIStr $Supported "$Ini" "detect" "supported"
  ReadINIStr $Version "$Ini" "detect" "version"
  ReadINIStr $DataDir "$Ini" "detect" "data"
  ReadINIStr $RootDir "$Ini" "detect" "root"
  ReadINIStr $Writable "$Ini" "detect" "writable"
  ReadINIStr $Running "$Ini" "detect" "running"
  ReadINIStr $Port "$Ini" "detect" "port"
  ReadINIStr $Message "$Ini" "detect" "message"
  ${If} $Writable == "0"
    StrCpy $SideBySide "1"
  ${EndIf}
FunctionEnd

Function .onInit
  InitPluginsDir
  StrCpy $Ini "$PLUGINSDIR\detect.ini"
  StrCpy $Result "not run"
  StrCpy $SideBySide "0"
  StrCpy $NoStart "0"
  StrCpy $KeepRunning "0"
  ; the installer and its helpers are needed before the Install page (detection); the payload follows in the section
  SetOutPath "$PLUGINSDIR\patch"
  File "${PAYLOAD}\install_patch.py"
  File "${PAYLOAD}\stop_skyguard.ps1"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/APP=" $R1
  ${IfNot} ${Errors}
    StrCpy $AppDir $R1
  ${Else}
    ; the usual places, in order: the per-user default, next to Setup.exe, its parent
    StrCpy $AppDir "$INSTDIR"
    ${IfNot} ${FileExists} "$AppDir\app\qa_platform\__init__.py"
      ${If} ${FileExists} "$EXEDIR\app\qa_platform\__init__.py"
        StrCpy $AppDir "$EXEDIR"
      ${ElseIf} ${FileExists} "$EXEDIR\..\app\qa_platform\__init__.py"
        GetFullPathName $AppDir "$EXEDIR\.."
      ${ElseIf} ${FileExists} "$PROFILE\AIAgenticTestAgent\app\qa_platform\__init__.py"
        StrCpy $AppDir "$PROFILE\AIAgenticTestAgent"
      ${EndIf}
    ${EndIf}
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/SIDEBYSIDE" $R1
  ${IfNot} ${Errors}
    StrCpy $SideBySide "1"
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/NOSTART" $R1
  ${IfNot} ${Errors}
    StrCpy $NoStart "1"
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/KEEPRUNNING" $R1
  ${IfNot} ${Errors}
    StrCpy $KeepRunning "1"
  ${EndIf}
  ${If} ${Silent}
    Call Detect
    ${If} $App == ""
      SetErrorLevel 2
      Quit
    ${EndIf}
    ${If} $Supported != "1"
      SetErrorLevel 3
      Quit
    ${EndIf}
    ${If} $Running == "1"
    ${AndIf} $KeepRunning == "1"
      SetErrorLevel 4
      Quit
    ${EndIf}
  ${EndIf}
FunctionEnd

; ---- Detect page ----------------------------------------------------------------------------------------------------

Function DetectPage
  !insertmacro MUI_HEADER_TEXT "Detect the installation" "Setup finds SkyGuard QA and checks that its version can be upgraded."
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 12u "SkyGuard QA installation folder:"
  Pop $0
  ${NSD_CreateDirRequest} 0 14u 68% 13u "$AppDir"
  Pop $hDir
  ${NSD_CreateBrowseButton} 70% 13u 14% 15u "Browse..."
  Pop $0
  ${NSD_OnClick} $0 OnBrowse
  ${NSD_CreateButton} 85% 13u 15% 15u "Detect"
  Pop $0
  ${NSD_OnClick} $0 OnDetect
  ${NSD_CreateLabel} 0 36u 100% 100u ""
  Pop $hResult
  Call ShowDetect
  nsDialogs::Show
FunctionEnd

Function OnBrowse
  ${NSD_GetText} $hDir $0
  nsDialogs::SelectFolderDialog "Choose the SkyGuard QA folder (the one that holds 'app')" "$0"
  Pop $0
  ${If} $0 != "error"
    ${NSD_SetText} $hDir "$0"
    Call OnDetect
  ${EndIf}
FunctionEnd

Function OnDetect
  ${NSD_GetText} $hDir $AppDir
  Call ShowDetect
FunctionEnd

Function ShowDetect
  ${NSD_SetText} $hResult "Detecting..."
  Call Detect
  GetDlgItem $0 $HWNDPARENT 1
  ${If} $Supported == "1"
    StrCpy $1 "Installed version: $Version$\r$\nTarget version: ${VERSION}$\r$\nApplication: $App$\r$\nData (kept): $DataDir$\r$\n$\r$\n$Message"
    EnableWindow $0 1
  ${Else}
    StrCpy $1 "Cannot upgrade this folder.$\r$\n$\r$\n$Message$\r$\n$\r$\nSupported versions: 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1 (and ${VERSION} again, to repair)."
    EnableWindow $0 0
  ${EndIf}
  ${NSD_SetText} $hResult "$1"
FunctionEnd

Function DetectLeave
  ${NSD_GetText} $hDir $0
  ${If} $0 != $AppDir
    StrCpy $AppDir $0
    Call ShowDetect
    Abort
  ${EndIf}
  ${If} $Supported != "1"
    Abort
  ${EndIf}
FunctionEnd

; ---- Backup page ----------------------------------------------------------------------------------------------------

Function BackupPage
  !insertmacro MUI_HEADER_TEXT "Backup" "Everything Setup changes is backed up first."
  Call Detect ; again: the application may have been started or closed since
  nsDialogs::Create 1018
  Pop $0
  StrCpy $1 "Before anything is changed, Setup saves to $RootDir\patch-backups:$\r$\n  - every application file it replaces,$\r$\n  - the database and the AI Agent knowledge database (consistent SQLite copies),$\r$\n  - settings.json.$\r$\n$\r$\nYour data folder ($DataDir) is never deleted. If any step fails, the previous version is restored automatically. Later, ROLLBACK_PATCH.cmd in $RootDir\patch-tools\${VERSION} undoes the upgrade."
  ${If} $SideBySide == "1"
    StrCpy $1 "$1$\r$\n$\r$\nThe application folder cannot be written by this user. Setup installs a patched copy in $LOCALAPPDATA\SkyGuardQA\app-${VERSION} and leaves the original as it is (same data folder)."
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 96u "$1"
  Pop $0
  ${If} $Running == "1"
    ${NSD_CreateCheckbox} 0 104u 100% 12u "SkyGuard QA is running (port $Port). Close it for me before upgrading."
    Pop $hStop
    ${NSD_Check} $hStop
  ${Else}
    StrCpy $hStop ""
    ${NSD_CreateLabel} 0 104u 100% 12u "SkyGuard QA is not running."
    Pop $0
  ${EndIf}
  GetDlgItem $0 $HWNDPARENT 1
  SendMessage $0 ${WM_SETTEXT} 0 "STR:&Install"
  nsDialogs::Show
FunctionEnd

Function BackupLeave
  StrCpy $KeepRunning "0"
  ${If} $hStop != ""
    ${NSD_GetState} $hStop $0
    ${If} $0 != ${BST_CHECKED}
      MessageBox MB_OK|MB_ICONEXCLAMATION "Close SkyGuard QA first (or tick the box so Setup closes it), then press Install."
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

; ---- Install --------------------------------------------------------------------------------------------------------

Section "Upgrade" SecUpgrade
  SetOutPath "$PLUGINSDIR\patch"
  DetailPrint "Unpacking the upgrade files..."
  SetDetailsPrint textonly
  File /r "${PAYLOAD}\*.*"
  SetDetailsPrint both
  DetailPrint "Upgrade files unpacked."
  ${GetTime} "" "L" $0 $1 $2 $3 $4 $5 $6
  CreateDirectory "$RootDir\logs"
  StrCpy $LogFile "$RootDir\logs\setup-${VERSION}-$2$1$0-$4$5$6.txt"
  ClearErrors
  FileOpen $9 "$LogFile" a
  ${If} ${Errors}
    CreateDirectory "$LOCALAPPDATA\SkyGuardQA\logs"
    StrCpy $LogFile "$LOCALAPPDATA\SkyGuardQA\logs\setup-${VERSION}-$2$1$0-$4$5$6.txt"
    FileOpen $9 "$LogFile" a
  ${EndIf}
  FileWrite $9 "SkyGuard QA ${VERSION} Setup: $AppDir (found $Version)$\r$\n"
  FileClose $9
  StrCpy $VerifyLog "$LogFile.verify.txt"

  ${If} $Running == "1"
    DetailPrint "Closing SkyGuard QA..."
    nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\patch\stop_skyguard.ps1" -App "$App" -Port $Port'
    Pop $0
  ${EndIf}

  StrCpy $0 ""
  ${If} $SideBySide == "1"
    StrCpy $0 " --side-by-side"
  ${EndIf}
  DetailPrint "Upgrading $Version to ${VERSION} (log: $LogFile)"
  nsExec::ExecToLog '"$Py" -E -s "$PLUGINSDIR\patch\install_patch.py" apply --app "$AppDir" --data "$DataDir" --python "$Py" --log "$LogFile"$0'
  Pop $1
  StrCpy $TargetApp "$App"
  ${If} $SideBySide == "1"
    StrCpy $TargetApp "$LOCALAPPDATA\SkyGuardQA\app-${VERSION}"
  ${EndIf}
  ${If} $1 != "0"
    StrCpy $Result "failed"
    DetailPrint "The upgrade did not complete. The previous version was restored; your data was kept."
    SetErrorLevel 1
    Goto done
  ${EndIf}

  DetailPrint "Verifying the upgraded installation..."
  nsExec::ExecToLog '"$Py" -E -s "$PLUGINSDIR\patch\install_patch.py" verify --app "$TargetApp" --data "$DataDir" --python "$Py" --log "$VerifyLog"'
  Pop $1
  ${If} $1 != "0"
    StrCpy $Result "failed"
    DetailPrint "Verification reported a problem; see $VerifyLog."
    SetErrorLevel 1
    Goto done
  ${EndIf}

  ; the tools to verify again or roll back later, without the payload
  StrCpy $2 "$RootDir\patch-tools\${VERSION}"
  ${If} $SideBySide == "1"
    StrCpy $2 "$TargetApp\patch-tools\${VERSION}"
  ${EndIf}
  CreateDirectory "$2"
  CopyFiles /SILENT "$PLUGINSDIR\patch\install_patch.py" "$2"
  CopyFiles /SILENT "$PLUGINSDIR\patch\PATCH_MANIFEST.json" "$2"
  CopyFiles /SILENT "$PLUGINSDIR\patch\APPLY_PATCH.ps1" "$2"
  CopyFiles /SILENT "$PLUGINSDIR\patch\ROLLBACK_PATCH.cmd" "$2"
  CopyFiles /SILENT "$PLUGINSDIR\patch\VERIFY_PATCH.cmd" "$2"
  StrCpy $Result "ok"
  SetErrorLevel 0
  DetailPrint "SkyGuard QA ${VERSION} is installed and verified."
  ${If} ${Silent}
  ${AndIf} $NoStart != "1"
    Call StartApp
  ${EndIf}
  done:
SectionEnd

; ---- Verify page ----------------------------------------------------------------------------------------------------

Function VerifyPage
  ${If} $Result == "ok"
    !insertmacro MUI_HEADER_TEXT "Verify" "Every check of the upgraded installation."
  ${Else}
    !insertmacro MUI_HEADER_TEXT "Verify" "The upgrade did not complete: the previous version was restored."
  ${EndIf}
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateListBox} 0 0 100% 120u ""
  Pop $1
  StrCpy $3 "$VerifyLog"
  ${If} $Result != "ok"
    StrCpy $3 "$LogFile"
  ${EndIf}
  StrCpy $Warnings 0
  ClearErrors
  FileOpen $2 "$3" r
  ${IfNot} ${Errors}
    ${Do}
      ClearErrors
      FileRead $2 $4
      ${If} ${Errors}
        ${ExitDo}
      ${EndIf}
      ${TrimNewLines} $4 $4
      ${If} $4 != ""
        ${NSD_LB_AddString} $1 "$4"
        ClearErrors
        ${WordFind} "$4" " WARN " "E#" $5
        ${IfNot} ${Errors}
          IntOp $Warnings $Warnings + 1
        ${EndIf}
      ${EndIf}
    ${Loop}
    FileClose $2
  ${Else}
    ${NSD_LB_AddString} $1 "No log was written: $3"
  ${EndIf}
  ${NSD_CreateLabel} 0 124u 100% 12u "Log: $3"
  Pop $0
  nsDialogs::Show
FunctionEnd

; ---- Finish ---------------------------------------------------------------------------------------------------------

Function FinishPre
  ${If} $Result == "ok"
    StrCpy $FinishTitle "SkyGuard QA ${VERSION} is installed"
    ${If} $Warnings > 0
      StrCpy $FinishText "SkyGuard QA was upgraded from $Version to ${VERSION}. Every check passed except $Warnings warning(s) on the Verify page (see the log).$\r$\n$\r$\nIn the browser press Ctrl+F5 once. New: answers to Missing information are kept. To undo: ROLLBACK_PATCH.cmd in patch-tools\${VERSION}."
    ${Else}
      StrCpy $FinishText "SkyGuard QA was upgraded from $Version to ${VERSION} and every check passed.$\r$\n$\r$\nIn the browser press Ctrl+F5 once. New: answers to Missing information are kept. To undo: ROLLBACK_PATCH.cmd in patch-tools\${VERSION}."
    ${EndIf}
  ${Else}
    StrCpy $FinishTitle "The upgrade did not complete"
    StrCpy $FinishText "Nothing was lost: the previous version ($Version) was restored and your data was kept.$\r$\n$\r$\nOpen the log to see the step that failed, and send it to your SkyGuard contact.$\r$\n$\r$\nLog: $LogFile"
  ${EndIf}
FunctionEnd

Function StartApp
  ${If} $Result != "ok"
    Return
  ${EndIf}
  StrCpy $Launcher "$RootDir\START_QA_PLATFORM.cmd"
  ${If} $SideBySide == "1"
    StrCpy $Launcher "$TargetApp\START_SKYGUARD_${VERSION}.cmd"
  ${EndIf}
  ${IfNot} ${FileExists} "$Launcher"
    StrCpy $Launcher "$TargetApp\START_QA_PLATFORM.cmd"
  ${EndIf}
  ${If} ${FileExists} "$Launcher"
    ExecShell "open" "$Launcher" "" SW_SHOWMINIMIZED
  ${EndIf}
FunctionEnd

Function OpenLog
  ExecShell "open" "notepad.exe" '"$LogFile"'
FunctionEnd
