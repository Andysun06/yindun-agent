Unicode true
!include "MUI2.nsh"

; 主推的日常交付形态：NSIS 安装包（开始菜单/桌面快捷方式、控制面板卸载）
; 免安装单文件 exe 保留给评委试用/临时机器（见 build_portable.py），不是唯一形态。
; 路径说明：相对本脚本目录；构建时先 cd tools\packaging，再执行 makensis installer.nsi
; 构建：makensis tools\packaging\installer.nsi
; 依赖：dist\隐盾安全智能体_V<版本>_便携版.exe（build_portable.py 产物）+ docs\使用说明.pdf

!define APP_NAME "隐盾安全智能体"
!define APP_VERSION "3.6.21"
!define APP_EXE "Yindun.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\YindunSecurityAgent"

Name "${APP_NAME}"
OutFile "..\..\dist\隐盾安全智能体_V3.6.21_安装包.exe"
InstallDir "$LOCALAPPDATA\Programs\Yindun"
InstallDirRegKey HKCU "Software\YindunSecurityAgent" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "3.6.21.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "FileVersion" "V3.6.21"
VIAddVersionKey "LegalCopyright" "Yindun Team"
VIAddVersionKey "FileDescription" "隐盾安全智能体安装包"
Icon "..\..\assets\icons\yindun-client.ico"
UninstallIcon "..\..\assets\icons\yindun-client.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "installer_readme.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动隐盾安全智能体"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\使用说明.pdf"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "查看使用说明（配置算力模型）"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "安装隐盾安全智能体" SecMain
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  ; 安装包直接复用已经过归档校验的单文件 exe，保证"装上的"和"便携试用的"是同一份
  File /oname=${APP_EXE} "..\..\dist\隐盾安全智能体_V3.6.21_便携版.exe"
  File "..\..\docs\使用说明.pdf"
  File "installer_readme.txt"

  CreateShortcut "$DESKTOP\隐盾安全智能体.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateDirectory "$SMPROGRAMS\隐盾安全智能体"
  CreateShortcut "$SMPROGRAMS\隐盾安全智能体\隐盾安全智能体.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortcut "$SMPROGRAMS\隐盾安全智能体\使用说明.lnk" "$INSTDIR\使用说明.pdf"
  CreateShortcut "$SMPROGRAMS\隐盾安全智能体\卸载隐盾安全智能体.lnk" "$INSTDIR\Uninstall.exe"

  ; 注册表：控制面板"程序和功能"里的卸载入口（当前用户）
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME} V${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Yindun Team"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  WriteRegStr HKCU "Software\YindunSecurityAgent" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\隐盾安全智能体.lnk"
  RMDir /r "$SMPROGRAMS\隐盾安全智能体"

  ; 先删程序本体，再单独处理用户数据：卸载不应该默认销毁聊天记录/审计链
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\使用说明.pdf"
  Delete "$INSTDIR\installer_readme.txt"
  Delete "$INSTDIR\Uninstall.exe"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\YindunSecurityAgent"

  ; 静默卸载默认保留数据（自动化/升级场景更安全）；交互式卸载明确询问
  IfSilent keep_data
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "是否同时删除本地配置、聊天记录与审计数据？$\r$\n选择“否”将保留这些数据，便于以后重装继续使用。" \
    IDNO keep_data
  RMDir /r "$INSTDIR"
  Goto done

keep_data:
  ; 目录里还有配置/会话/审计等数据时，RMDir 会失败并保留目录——这是预期行为
  RMDir "$INSTDIR"

done:
SectionEnd
