; Inno Setup script for the Windows build.
; 参数由 build_windows.ps1 通过 /D 注入（AppVersion / AppBuild / SourceDir / OutputDir）。

#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif
#ifndef AppBuild
  #define AppBuild "3"
#endif
#ifndef SourceDir
  #define SourceDir "dist\v1.2.0\音频分析"
#endif
#ifndef OutputDir
  #define OutputDir "dist\v1.2.0"
#endif

[Setup]
AppId={{8C4E1F62-3B7A-4D9C-9F2E-6A1B5D8C7E34}
AppName=音频分析
AppVersion={#AppVersion}
AppVerName=音频分析 v{#AppVersion}
AppPublisher=WorkBuddy
AppPublisherURL=https://github.com/zyding123/audio-analysis-app
AppSupportURL=https://github.com/zyding123/audio-analysis-app/issues
AppUpdatesURL=https://github.com/zyding123/audio-analysis-app/releases
DefaultDirName={autopf}\音频分析
DefaultGroupName=音频分析
UninstallDisplayIcon={app}\音频分析.exe
OutputDir={#OutputDir}
OutputBaseFilename=audio-analysis-v{#AppVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName=音频分析
VersionInfoDescription=音频分析 — 音频批量分析工具
DisableProgramGroupPage=yes
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "chinesesimp"; MessagesFile: "installer\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\音频分析"; Filename: "{app}\音频分析.exe"
Name: "{group}\{cm:UninstallProgram,音频分析}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\音频分析"; Filename: "{app}\音频分析.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\音频分析.exe"; Description: "{cm:LaunchProgram,音频分析}"; Flags: nowait postinstall skipifsilent
