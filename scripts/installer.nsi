Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "nsDialogs.nsh"
!include "WordFunc.nsh"
!include "StrFunc.nsh"
${StrRep}

!define PRODUCT_ID "POLY5-748D8316-6D5E-42CF-90DA-4F84E77A6F20"
!define PRODUCT_VERSION "3.3.0-dev.14"
!define PRODUCT_BUILD "3.3.0.14"
!ifndef PRODUCT_REG_KEY
!define PRODUCT_REG_KEY "Software\POLY5"
!endif
!ifndef UNINSTALL_KEY
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\POLY5"
!endif
Var ExistingDir
Var ExistingVersion
Var ExistingBuild
Var MaintenanceChoice
Var UpdateRadio
Var KeepRadio
Name "POLY5"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\POLY5"
InstallDirRegKey HKCU "${PRODUCT_REG_KEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID zlib
SetDatablockOptimize on
SetOverwrite on
ShowInstDetails show
ShowUninstDetails show
BrandingText "POLY5"
VIProductVersion "3.2.0.1"
VIAddVersionKey /LANG=2052 "ProductName" "POLY5"
VIAddVersionKey /LANG=2052 "FileDescription" "POLY5 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "POLY5"
Icon "${APP_SOURCE}\Poly5.ico"
UninstallIcon "${APP_SOURCE}\Poly5.ico"

!define MUI_ABORTWARNING
!define MUI_ICON "${APP_SOURCE}\Poly5.ico"
!define MUI_UNICON "${APP_SOURCE}\Poly5.ico"
!define MUI_WELCOMEPAGE_TITLE "安装 POLY5"
!define MUI_WELCOMEPAGE_TEXT "将安装 POLY5 行情监控与纸面模拟开发版。$\r$\n$\r$\n默认安装到当前用户的 Programs 文件夹。$\r$\n数据位置：D:\POLYMARKET\data$\r$\n$\r$\n升级前请从托盘菜单完全退出程序。"
!define MUI_DIRECTORYPAGE_TEXT_TOP "选择程序文件夹。默认使用当前用户的标准程序目录，无需管理员权限。行情记录和设置保存在 D:\POLYMARKET\data。"
!define MUI_COMPONENTSPAGE_TEXT_TOP "程序文件为必选项。默认仅在程序目录创建启动入口；桌面快捷方式可以自行选择。"
!define MUI_FINISHPAGE_TITLE "POLY5 已安装"
!define MUI_FINISHPAGE_TEXT "点击完成退出安装。$\r$\n$\r$\n程序关闭窗口后会静默收入托盘；完全退出请使用托盘菜单。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\Poly5.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--show"
!define MUI_FINISHPAGE_RUN_TEXT "打开 POLY5"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "卸载只移除本安装包提供的程序文件。$\r$\n行情记录、设置和 AI 建议将保留在 D:\POLYMARKET\data。"
!insertmacro MUI_PAGE_WELCOME
Page custom MaintenancePage MaintenanceLeave
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_PRE DirectoryPre
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要 64 位 Windows。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "此版本需要 Windows 10 或更新版本。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  StrCpy $MaintenanceChoice "install"
  ReadRegStr $ExistingDir HKCU "${PRODUCT_REG_KEY}" "InstallDir"
  IfFileExists "$ExistingDir\Poly5.exe" 0 init_done
  ReadINIStr $0 "$ExistingDir\installation.ini" "POLY5" "ProductId"
  ${If} $0 == "${PRODUCT_ID}"
    ReadINIStr $ExistingVersion "$ExistingDir\installation.ini" "POLY5" "Version"
    ReadINIStr $ExistingBuild "$ExistingDir\installation.ini" "POLY5" "BuildVersion"
    StrCpy $INSTDIR "$ExistingDir"
    StrCpy $MaintenanceChoice "update"
    ReadINIStr $0 "$ExistingDir\installation.ini" "POLY5" "DesktopShortcut"
    ${If} $0 == "1"
      SectionSetFlags 1 1
    ${EndIf}
  ${Else}
    StrCpy $ExistingDir ""
  ${EndIf}
  init_done:
FunctionEnd

Function MaintenancePage
  ${If} $MaintenanceChoice == "install"
    Abort
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "检测到已安装的 POLY5" "选择更新现有安装，或保持当前版本。"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 34u "当前版本：$ExistingVersion$\r$\n目标版本：${PRODUCT_VERSION}$\r$\n安装位置：$ExistingDir"
  Pop $0
  ${NSD_CreateRadioButton} 0 48u 100% 14u "更新现有安装（同版本可修复）"
  Pop $UpdateRadio
  ${NSD_Check} $UpdateRadio
  ${NSD_CreateRadioButton} 0 70u 100% 14u "保持当前版本，退出安装"
  Pop $KeepRadio
  ${NSD_CreateLabel} 0 94u 100% 34u "更新保留行情、设置、AI 建议及模拟账本。$\r$\n不会自动结束正在运行的程序；覆盖文件前需从托盘完全退出。"
  Pop $0
  nsDialogs::Show
FunctionEnd

Function MaintenanceLeave
  ${NSD_GetState} $KeepRadio $0
  ${If} $0 == ${BST_CHECKED}
    Quit
  ${EndIf}
  StrCpy $MaintenanceChoice "update"
  StrCpy $INSTDIR "$ExistingDir"
FunctionEnd

Function DirectoryPre
  ${If} $MaintenanceChoice == "update"
    Abort
  ${EndIf}
FunctionEnd

Function CheckInstalledVersion
  IfFileExists "$INSTDIR\installation.ini" 0 version_done
  ReadINIStr $0 "$INSTDIR\installation.ini" "POLY5" "ProductId"
  ${If} $0 != "${PRODUCT_ID}"
    MessageBox MB_OK|MB_ICONSTOP "目标目录包含其他产品的安装记录，已停止。" /SD IDOK
    SetErrorLevel 3
    Quit
  ${EndIf}
  ReadINIStr $ExistingVersion "$INSTDIR\installation.ini" "POLY5" "Version"
  ReadINIStr $ExistingBuild "$INSTDIR\installation.ini" "POLY5" "BuildVersion"
  ${If} $ExistingBuild == ""
    ${StrRep} $ExistingBuild "$ExistingVersion" "-dev." "."
    ${If} $ExistingBuild == $ExistingVersion
      StrCpy $ExistingBuild "$ExistingVersion.65535"
    ${EndIf}
  ${EndIf}
  ${VersionCompare} "$ExistingBuild" "${PRODUCT_BUILD}" $0
  ${If} $0 == 1
    MessageBox MB_OK|MB_ICONSTOP "已安装较新版本 $ExistingVersion，不能用此安装包降级覆盖。" /SD IDOK
    SetErrorLevel 3
    Quit
  ${EndIf}
  version_done:
FunctionEnd

Function .onVerifyInstDir
  Push $0
  Push $1
  Push $2
  ; NSIS GetFullPathName requires an existing path. Windows' API also
  ; canonicalizes a destination that has not been created yet.
  StrCpy $0 "$INSTDIR"
  System::Call 'kernel32::GetFullPathNameW(w r0, i ${NSIS_MAX_STRLEN}, w .r1, p 0) i .r2'
  ${If} $2 == 0
  ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
    Pop $2
    Pop $1
    Pop $0
    Abort
  ${EndIf}
  StrCpy $INSTDIR "$1"
  StrLen $2 "D:\POLYMARKET\data\"
  StrCpy $0 "$INSTDIR" $2
  StrLen $2 "$INSTDIR"
  ; Keep program files out of the fact ledger; never install at a drive root.
  ${If} $2 <= 3
  ${OrIf} $INSTDIR == "D:\POLYMARKET"
  ${OrIf} $INSTDIR == "D:\POLYMARKET\data"
  ${OrIf} $0 == "D:\POLYMARKET\data\"
    Pop $2
    Pop $1
    Pop $0
    Abort
  ${EndIf}
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function CheckAppClosed
  IfFileExists "$INSTDIR\Poly5.exe" 0 done
  ClearErrors
  FileOpen $0 "$INSTDIR\Poly5.exe" a
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONEXCLAMATION "请先从 POLY5 托盘菜单选择「完全退出」，然后重新安装。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  FileClose $0
  done:
FunctionEnd

Section "POLY5 程序文件" Core
  SectionIn RO
  Call .onVerifyInstDir
  Call CheckInstalledVersion
  Call CheckAppClosed
  SetOutPath "$INSTDIR"
  !include "${INSTALL_FILES}"
  WriteINIStr "$INSTDIR\installation.ini" "POLY5" "ProductId" "${PRODUCT_ID}"
  WriteINIStr "$INSTDIR\installation.ini" "POLY5" "InstallDir" "$INSTDIR"
  WriteINIStr "$INSTDIR\installation.ini" "POLY5" "Version" "${PRODUCT_VERSION}"
  WriteINIStr "$INSTDIR\installation.ini" "POLY5" "BuildVersion" "${PRODUCT_BUILD}"
  WriteINIStr "$INSTDIR\installation.ini" "POLY5" "SettingsSchema" "1"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  SetOutPath "$INSTDIR"
  CreateShortCut "$INSTDIR\启动 POLY5.lnk" "$INSTDIR\Poly5.exe" "--show" "$INSTDIR\Poly5.exe" 0
  WriteRegStr HKCU "${PRODUCT_REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "POLY5"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Poly5.exe,0"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${PAYLOAD_KB}
SectionEnd

Section /o "桌面快捷方式" Desktop
  CreateShortCut "$DESKTOP\POLY5.lnk" "$INSTDIR\Poly5.exe" "--show" "$INSTDIR\Poly5.exe" 0
  WriteINIStr "$INSTDIR\installation.ini" "POLY5" "DesktopShortcut" "1"
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  ReadINIStr $0 "$INSTDIR\installation.ini" "POLY5" "ProductId"
  ReadINIStr $1 "$INSTDIR\installation.ini" "POLY5" "InstallDir"
  ${If} $0 != "${PRODUCT_ID}"
  ${OrIf} $1 != $INSTDIR
    MessageBox MB_OK|MB_ICONSTOP "未找到匹配的安装记录，已停止卸载。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  IfFileExists "$INSTDIR\Poly5.exe" 0 done
  ClearErrors
  FileOpen $0 "$INSTDIR\Poly5.exe" a
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONEXCLAMATION "请先从 POLY5 托盘菜单选择「完全退出」，再卸载。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  FileClose $0
  done:
FunctionEnd

Section "Uninstall"
  ReadINIStr $0 "$INSTDIR\installation.ini" "POLY5" "DesktopShortcut"
  ${If} $0 == "1"
    Delete "$DESKTOP\POLY5.lnk"
  ${EndIf}
  ; Explicit package manifest only. Never recursively delete the install root.
  !include "${UNINSTALL_FILES}"
  Delete "$INSTDIR\启动 POLY5.lnk"
  Delete "$INSTDIR\installation.ini"
  Delete "$INSTDIR\Uninstall.exe"
  SetOutPath "D:\POLYMARKET"
  RMDir "$INSTDIR"
  ReadRegStr $0 HKCU "${PRODUCT_REG_KEY}" "InstallDir"
  ${If} $0 == $INSTDIR
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
    DeleteRegKey HKCU "${PRODUCT_REG_KEY}"
  ${EndIf}
SectionEnd

