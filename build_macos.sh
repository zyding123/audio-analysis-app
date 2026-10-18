#!/usr/bin/env bash
# Build and verify a self-contained Apple Silicon application and installer.
set -euo pipefail
cd "$(dirname "$0")"
APP_NAME="音频分析"
if [ -n "${PY:-}" ]; then
  :
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x "$HOME/.workbuddy/binaries/python/envs/default/bin/python" ]; then
  PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
else
  PY=python3
fi
"$PY" -c 'import PyInstaller, numpy, librosa, matplotlib; from PySide6.QtMultimedia import QMediaPlayer'
if ! /usr/bin/lipo -version >/dev/null 2>&1; then
  export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
fi
APP_VER="$("$PY" -c 'from version import APP_VERSION; print(APP_VERSION)')"
APP_BUILD="$("$PY" -c 'from version import APP_BUILD; print(APP_BUILD)')"
FFMPEG_BIN_DIR="$("$PY" - <<'PY'
import contextlib, io, os, shutil
import static_ffmpeg
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    static_ffmpeg.add_paths()
binary = shutil.which('ffmpeg')
assert binary, 'ffmpeg not found'
print(os.path.dirname(binary))
PY
)"
test -x "$FFMPEG_BIN_DIR/ffmpeg"
test -x "$FFMPEG_BIN_DIR/ffprobe"
# Version-specific outputs preserve previous release deliverables.
BUILD_ROOT="${BUILD_ROOT:-build/v$APP_VER}"
DIST_ROOT="${DIST_ROOT:-dist/v$APP_VER}"
mkdir -p "$BUILD_ROOT" "$DIST_ROOT"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$BUILD_ROOT/pyinstaller-cache}"
"$PY" -m PyInstaller --noconfirm --clean --windowed \
  --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" --distpath "$DIST_ROOT" \
  --name "$APP_NAME" --osx-bundle-identifier com.workbuddy.audioanalysis \
  --add-binary "$FFMPEG_BIN_DIR/ffmpeg:." --add-binary "$FFMPEG_BIN_DIR/ffprobe:." \
  --exclude-module pandas --exclude-module IPython --exclude-module tkinter \
  --exclude-module pytest --exclude-module torch --exclude-module torchvision \
  --exclude-module torchaudio --exclude-module sklearn main.py
APP="$DIST_ROOT/$APP_NAME.app"
PLIST="$APP/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleShortVersionString -string "$APP_VER" "$PLIST"
/usr/bin/plutil -replace CFBundleVersion -string "$APP_BUILD" "$PLIST"
/usr/bin/codesign --force --deep --sign - "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/$APP_NAME" --self-test
STAGING="$BUILD_ROOT/installer"
mkdir -p "$STAGING"
/usr/bin/ditto "$APP" "$STAGING/$APP_NAME.app"
ln -sfn /Applications "$STAGING/Applications"
DMG="$DIST_ROOT/audio-analysis-v$APP_VER-macos-arm64.dmg"
hdiutil create -volname "$APP_NAME v$APP_VER" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
"$PY" - "$DMG" <<'PY'
import hashlib, sys
from pathlib import Path
p = Path(sys.argv[1])
Path(str(p) + '.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n')
PY
echo "Built and verified: $DMG"
