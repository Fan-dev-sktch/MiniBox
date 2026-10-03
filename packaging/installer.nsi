; NSIS 安装包脚本：makensis packaging/installer.nsi  （先用 PyInstaller 生成 dist/MiniBox）
Unicode true
!ifdef AMD64
  Target amd64-unicode  ; 需要带 amd64 stub 的 NSIS（如 Linux 的 nsis 包）；默认 x86 安装器在 64 位 Windows 上同样可用
!endif
!include "MUI2.nsh"
!define APPNAME "MiniBox"
!ifndef VERSION
  !define VERSION "1.7.0"
!endif
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"

Name "${APPNAME}"
OutFile "..\dist\MiniBox-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
InstallDirRegKey HKCU "Software\${APPNAME}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "MiniBox ${VERSION} · 免费开源"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "MiniBox 安装程序"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "MIT License"

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\MiniBox.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即打开 MiniBox"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "MiniBox" SecMain
  ; 覆盖安装前先结束正在运行的程序
  !ifndef NOKILL
  nsExec::Exec '"$SYSDIR\taskkill.exe" /IM MiniBox.exe /F'
  Sleep 500
  !endif
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"
  File /r "..\dist\MiniBox\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\MiniBox.lnk" "$INSTDIR\MiniBox.exe"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\卸载 MiniBox.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\MiniBox.lnk" "$INSTDIR\MiniBox.exe"
  WriteRegStr HKCU "Software\${APPNAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "MiniBox"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\MiniBox.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  !ifndef NOKILL
  nsExec::Exec '"$SYSDIR\taskkill.exe" /IM MiniBox.exe /F'
  Sleep 500
  !endif
  Delete "$DESKTOP\MiniBox.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APPNAME}"
SectionEnd
