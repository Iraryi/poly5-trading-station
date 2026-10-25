Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WordFunc.nsh"
!ifndef TRAINER_VERSION
!define TRAINER_VERSION "1.9.1"
!endif
Name "POLY5 模型训练台"
OutFile "D:\POLYMARKET\outputs\POLY5Trainer_Setup_${TRAINER_VERSION}_x64.exe"
InstallDir "$LOCALAPPDATA\Programs\POLY5Trainer"
InstallDirRegKey HKCU "Software\POLY5Trainer" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID zlib
Icon "D:\POLYMARKET\Poly5_Trading_Station_v3\assets\poly5.ico"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\POLY5Trainer.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--autostart"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
Function .onInit
  ReadRegStr $0 HKCU "Software\POLY5Trainer" "InstallDir"
  ${If} $0 != ""
    IfSilent update
    MessageBox MB_YESNO|MB_ICONQUESTION "检测到已安装的训练台。是否更新？模型、数据和配置将保留。" IDYES update
    Abort
    update:
    StrCpy $INSTDIR $0
  ${EndIf}
FunctionEnd
Function CheckTrainerClosed
  IfFileExists "$INSTDIR\POLY5Trainer.exe" 0 done
  ClearErrors
  FileOpen $0 "$INSTDIR\POLY5Trainer.exe" a
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONEXCLAMATION "训练台仍在运行。请先停止训练，并退出托盘中的界面后重新更新；模型与进度会保留。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  FileClose $0
  done:
FunctionEnd
Function CheckVersion
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5Trainer" "DisplayVersion"
  ${If} $0 != ""
    ${VersionCompare} "$0" "${TRAINER_VERSION}" $1
    ${If} $1 == 1
      MessageBox MB_OK|MB_ICONSTOP "已安装较新的训练台，不能降级覆盖。" /SD IDOK
      SetErrorLevel 3
      Quit
    ${EndIf}
  ${EndIf}
FunctionEnd
Section
  Call CheckVersion
  Call CheckTrainerClosed
  SetOutPath "$INSTDIR"
  File /r "D:\POLYMARKET\outputs\POLY5Trainer-${TRAINER_VERSION}\POLY5Trainer\*.*"
  WriteRegStr HKCU "Software\POLY5Trainer" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5Trainer" "DisplayName" "POLY5 模型训练台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5Trainer" "DisplayVersion" "${TRAINER_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5Trainer" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\POLY5"
  CreateShortcut "$SMPROGRAMS\POLY5\模型训练台.lnk" "$INSTDIR\POLY5Trainer.exe" "--autostart" "$INSTDIR\POLY5Trainer.exe"
SectionEnd
Function un.onInit
  IfFileExists "$INSTDIR\POLY5Trainer.exe" 0 done
  ClearErrors
  FileOpen $0 "$INSTDIR\POLY5Trainer.exe" a
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONEXCLAMATION "请先停止训练，并退出托盘中的训练台后再卸载。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  FileClose $0
  done:
FunctionEnd
Section "Uninstall"
  !include "D:\POLYMARKET\work\trainer-uninstall.nsh"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\POLY5\模型训练台.lnk"
  RMDir "$SMPROGRAMS\POLY5"
  DeleteRegKey HKCU "Software\POLY5Trainer"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5Trainer"
SectionEnd
