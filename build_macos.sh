#!/usr/bin/env bash
# =============================================================================
# 打包脚本：把本工具打包成 macOS 可直接双击运行的 .app，并生成 .dmg 安装镜像。
#
# 用法：  bash build_macos.sh
# 产物：  dist/音频分析.app   （可直接拖入“应用程序”）
#         dist/音频分析.dmg   （可分发安装镜像）
#
# 前提：  Python 3.9+，且已安装运行依赖（numpy / PySide6-Essentials / librosa 等，
#         见 requirements.txt）；脚本会自动安装 PyInstaller，并用 static-ffmpeg
#         获取静态 ffmpeg/ffprobe 二进制一并打进 .app（目标机无需预装 ffmpeg）。
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="音频分析"

# 选择 Python：显式指定（$PY）> 项目 venv > WorkBuddy 托管环境 > 系统 python3
if [ -n "${PY:-}" ]; then
  :
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "$HOME/.workbuddy/binaries/python/envs/default/bin/python" ]; then
  PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "❌ 未找到 python3，请先安装。" >&2
  exit 1
fi
echo "→ 使用 Python: $PY"

# 依赖自检（BPM 检测需要 librosa；GUI 需要 PySide6；试听需要 QtMultimedia；图表需要 matplotlib）
if ! "$PY" -c "import numpy, PySide6, librosa, matplotlib; from PySide6.QtMultimedia import QMediaPlayer" 2>/dev/null; then
  echo "❌ 缺少运行依赖。请先执行：$PY -m pip install -r requirements.txt" >&2
  exit 1
fi

# 规避「Xcode 许可证未接受」导致系统 lipo 不可用的问题：
# 此时改用 Command Line Tools 自带的独立 lipo（不受许可证影响）
if ! /usr/bin/lipo -version >/dev/null 2>&1; then
  if [ -x /Library/Developer/CommandLineTools/usr/bin/lipo ]; then
    export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
    echo "→ 系统 lipo 不可用（Xcode 许可证未接受），已切换到 Command Line Tools 的 lipo"
  else
    echo "⚠ 系统 lipo 不可用且未找到 Command Line Tools lipo，打包可能失败" >&2
  fi
fi

echo "→ 1/4 安装打包依赖（PyInstaller）..."
"$PY" -m pip install --quiet --upgrade pyinstaller

echo "→ 2/4 定位静态 ffmpeg / ffprobe 二进制..."
BIN_PATHS="$("$PY" - <<'PY'
import shutil
import static_ffmpeg
static_ffmpeg.add_paths()  # 首次会下载静态二进制到本地缓存
print(shutil.which("ffmpeg") or "")
print(shutil.which("ffprobe") or "")
PY
)"
FFMPEG="$(printf '%s\n' "$BIN_PATHS" | sed -n '1p')"
FFPROBE="$(printf '%s\n' "$BIN_PATHS" | sed -n '2p')"

if [ -z "$FFMPEG" ] || [ -z "$FFPROBE" ]; then
  echo "❌ 获取 ffmpeg/ffprobe 失败，请检查网络后重试。" >&2
  exit 1
fi
echo "    ffmpeg : $FFMPEG"
echo "    ffprobe: $FFPROBE"

echo "→ 3/4 打包 .app（PyInstaller）..."
# 先清理上次的构建产物。
# 注意：直接 rm 大批量文件可能触发 macOS 的「批量删除保护」，因此大目录统一
# 移动到废纸篓（可恢复、不会被拦截）；单文件删除不受影响。
TS="$(date +%Y%m%d-%H%M%S)"
TRASH="$HOME/.Trash"
if [ -d "dist/$APP_NAME" ]; then
  mv "dist/$APP_NAME" "$TRASH/${APP_NAME}-构建残留-$TS" 2>/dev/null || true
fi
if [ -d "dist/$APP_NAME.app" ]; then
  mv "dist/$APP_NAME.app" "$TRASH/${APP_NAME}.app-旧构建-$TS" 2>/dev/null || true
fi
if [ -d "build/$APP_NAME" ]; then
  mv "build/$APP_NAME" "$TRASH/${APP_NAME}-build缓存-$TS" 2>/dev/null || true
fi
if [ -f "dist/$APP_NAME.dmg" ]; then
  rm -f "dist/$APP_NAME.dmg"
fi
# 把 PyInstaller 缓存重定向到临时目录，避免在项目内触发批量删除保护 / 受限目录写入失败
export PYINSTALLER_CONFIG_DIR="/tmp/pyinstaller-cache-audioanalysis"
mkdir -p "$PYINSTALLER_CONFIG_DIR"
"$PY" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "com.workbuddy.audioanalysis" \
  --add-binary "$FFMPEG:." \
  --add-binary "$FFPROBE:." \
  --exclude-module pandas \
  --exclude-module IPython \
  --exclude-module tkinter \
  --exclude-module pytest \
  --exclude-module torch \
  --exclude-module torchvision \
  --exclude-module torchaudio \
  --exclude-module sklearn \
  main.py

# 注入版本号（PyInstaller 默认写 0.0.0；改完后重新 adhoc 签名，保持签名有效）
APP_VER="1.0.0"
APP_BUILD="1"
PLIST="dist/$APP_NAME.app/Contents/Info.plist"
if [ -f "$PLIST" ]; then
  /usr/bin/plutil -replace CFBundleShortVersionString -string "$APP_VER" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VER" "$PLIST" 2>/dev/null || true
  /usr/bin/plutil -replace CFBundleVersion -string "$APP_BUILD" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_BUILD" "$PLIST" 2>/dev/null || true
  # 修改 Info.plist 会使原签名失效，重新做 adhoc 签名
  /usr/bin/codesign --force --deep --sign - "dist/$APP_NAME.app" >/dev/null 2>&1 || true
  echo "→ 版本号已注入：v$APP_VER"
fi

echo "→ 4/4 生成 .dmg 安装镜像..."
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "dist/$APP_NAME.app" \
  -ov -format UDZO \
  "dist/$APP_NAME.dmg" >/dev/null

echo ""
echo "✅ 打包完成："
echo "   应用 : $(pwd)/dist/$APP_NAME.app"
echo "   镜像 : $(pwd)/dist/$APP_NAME.dmg"
echo ""
echo "   安装：双击 .dmg，把图标拖入 Applications 即可。"
