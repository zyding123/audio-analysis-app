# Build and verify a self-contained Windows application and Inno Setup installer.
# 用法（在仓库根目录）：powershell -ExecutionPolicy Bypass -File build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$AppName = "音频分析"
if ($env:PY) {
    $Py = $env:PY
} elseif (Test-Path ".venv\Scripts\python.exe") {
    $Py = ".venv\Scripts\python.exe"
} else {
    $Py = "python"
}

& $Py -c "import PyInstaller, numpy, librosa, matplotlib; from PySide6.QtMultimedia import QMediaPlayer"
if ($LASTEXITCODE -ne 0) { throw "依赖未安装，请先 pip install -r requirements-build.txt" }

$AppVer = (& $Py -c "from version import APP_VERSION; print(APP_VERSION)").Trim()
$AppBuild = (& $Py -c "from version import APP_BUILD; print(APP_BUILD)").Trim()
Write-Host "→ 版本 v$AppVer (build $AppBuild)"

# static-ffmpeg 自带 Windows 静态二进制
$FfmpegDir = (& $Py -c @"
import contextlib, io, os, shutil
import static_ffmpeg
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    static_ffmpeg.add_paths()
print(os.path.dirname(shutil.which('ffmpeg')))
"@).Trim()
$Ffmpeg = Join-Path $FfmpegDir "ffmpeg.exe"
$Ffprobe = Join-Path $FfmpegDir "ffprobe.exe"
if (!(Test-Path $Ffmpeg)) { throw "ffmpeg.exe not found at $Ffmpeg" }
if (!(Test-Path $Ffprobe)) { throw "ffprobe.exe not found at $Ffprobe" }
Write-Host "→ 内置 ffmpeg: $FfmpegDir"

# 版本化输出目录，保留历史构建
$BuildRoot = if ($env:BUILD_ROOT) { $env:BUILD_ROOT } else { "build\v$AppVer" }
$DistRoot = if ($env:DIST_ROOT) { $env:DIST_ROOT } else { "dist\v$AppVer" }
New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot | Out-Null
$env:PYINSTALLER_CONFIG_DIR = if ($env:PYINSTALLER_CONFIG_DIR) { $env:PYINSTALLER_CONFIG_DIR } else { Join-Path $BuildRoot "pyinstaller-cache" }

& $Py -m PyInstaller --noconfirm --clean --windowed --onedir `
    --workpath (Join-Path $BuildRoot "work") `
    --specpath $BuildRoot `
    --distpath $DistRoot `
    --name $AppName `
    --version-file (Resolve-Path "version_info.txt").Path `
    --add-binary "$Ffmpeg;ffmpeg.exe" `
    --add-binary "$Ffprobe;ffprobe.exe" `
    --exclude-module pandas `
    --exclude-module IPython `
    --exclude-module tkinter `
    --exclude-module pytest `
    --exclude-module torch `
    --exclude-module torchvision `
    --exclude-module torchaudio `
    --exclude-module sklearn `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$AppDir = Join-Path $DistRoot $AppName
$AppExe = Join-Path $AppDir "$AppName.exe"
if (!(Test-Path $AppExe)) { throw "缺少 $AppExe" }

# 诊断：确认 ffmpeg/ffprobe 已被打进包
$bundled = Get-ChildItem $AppDir -Recurse -Include "ffmpeg.exe", "ffprobe.exe" -ErrorAction SilentlyContinue
if (!$bundled) {
    Write-Host "⚠️ 包内未找到 ffmpeg/ffprobe，_internal 内容："
    Get-ChildItem (Join-Path $AppDir "_internal") -ErrorAction SilentlyContinue |
        Select-Object -First 25 | ForEach-Object { Write-Host "   $($_.Name)" }
    throw "ffmpeg/ffprobe 未打包进应用目录"
}
Write-Host "→ 包内二进制："
$bundled | ForEach-Object { Write-Host "   $($_.FullName)" }

# 包内自检：验证包内 ffmpeg、分析链路、图表与 UI（数据写入临时目录）
$env:QT_QPA_PLATFORM = "offscreen"
& $AppExe --self-test
if ($LASTEXITCODE -ne 0) { throw "包内自检失败（--self-test）" }
Write-Host "→ 包内自检通过"

# Inno Setup 安装包
$IsccCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (!$Iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $Iscc = $cmd.Source }
}
if (!$Iscc) { throw "未找到 Inno Setup（ISCC.exe），请安装 Inno Setup 6" }

& $Iscc "/DAppVersion=$AppVer" "/DAppBuild=$AppBuild" `
    "/DSourceDir=$(Resolve-Path $AppDir)" "/DOutputDir=$(Resolve-Path $DistRoot)" `
    installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 打包失败" }

$Setup = Join-Path $DistRoot "audio-analysis-v$AppVer-windows-x64-setup.exe"
if (!(Test-Path $Setup)) { throw "缺少 $Setup" }
$Hash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$Setup.sha256" -Value "$Hash  $(Split-Path $Setup -Leaf)" -NoNewline

Write-Host ""
Write-Host "✅ 构建完成："
Write-Host "   应用目录 : $(Resolve-Path $AppDir)"
Write-Host "   安装包   : $(Resolve-Path $Setup)"
Write-Host "   SHA-256  : $Hash"
