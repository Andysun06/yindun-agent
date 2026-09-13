Unicode true
!include "MUI2.nsh"

Name "隐盾安全智能体"
OutFile "隐盾安全智能体_V3.3_安装包.exe"
InstallDir "$LOCALAPPDATA\Yindun\YindunSecurityAgent"
InstallDirRegKey HKCU "Software\YindunSecurityAgent" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "3.3.0.0"
VIAddVersionKey "ProductName" "隐盾安全智能体"
VIAddVersionKey "FileVersion" "V3.3"
VIAddVersionKey "LegalCopyright" "Yindun Team"

!define APP_EXE "YindunSecurityAgent.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\YindunSecurityAgent"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "installer_readme.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动隐盾安全智能体"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\评委使用说明.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "查看评委使用说明（配置算力模型）"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "安装隐盾安全智能体" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "dist\YindunSecurityAgent\*.*"
  File "评委使用说明.txt"

  ; 快捷方式
  CreateShortcut "$DESKTOP\隐盾安全智能体.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortcut "$SMPROGRAMS\隐盾安全智能体\隐盾安全智能体.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\隐盾安全智能体\卸载隐盾安全智能体.lnk" "$INSTDIR\Uninstall.exe"

  ; 注册表：控制面板卸载入口
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "隐盾安全智能体 V3.3"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "3.3.0"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Yindun Team"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  WriteRegStr HKCU "Software\YindunSecurityAgent" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR"
  Delete "$DESKTOP\隐盾安全智能体.lnk"
  RMDir /r "$SMPROGRAMS\隐盾安全智能体"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\YindunSecurityAgent"
SectionEnd
