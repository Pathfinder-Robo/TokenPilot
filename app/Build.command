#!/bin/zsh
set -eu

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SOURCE_DIR/.." && pwd)"
APP_DIR="${TOKENPILOT_APP_DIR:-$ROOT_DIR/dist/TokenPilot.app}"
BUILD_DIR="${TOKENPILOT_BUILD_DIR:-$ROOT_DIR/.build}"
MODULE_CACHE_DIR="$BUILD_DIR/module-cache"
PYTHON_BIN="${TOKENPILOT_PYTHON_BIN:-${PYTHON_BIN:-$(command -v python3 || true)}}"
CODEX_BIN="${TOKENPILOT_CODEX_BIN:-${CODEX_BIN:-$(command -v codex || true)}}"

if [ "$(uname -s)" != "Darwin" ]; then
  print -u2 "Error: TokenPilot native app build requires macOS (Darwin)."
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  print -u2 "Error: swiftc not found. Install Xcode or Command Line Tools."
  exit 1
fi
if ! command -v codesign >/dev/null 2>&1; then
  print -u2 "Error: codesign not found."
  exit 1
fi
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  print -u2 "Error: python3 not found (set TOKENPILOT_PYTHON_BIN to override)."
  exit 1
fi
if [ -z "$CODEX_BIN" ]; then
  print -u2 "Error: codex CLI not found in PATH (set TOKENPILOT_CODEX_BIN to override)."
  exit 1
fi

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$MODULE_CACHE_DIR"

swiftc \
  "$SOURCE_DIR/main.swift" \
  "$SOURCE_DIR/Appearance.swift" \
  "$SOURCE_DIR/LiquidGlassContent.swift" \
  "$SOURCE_DIR/GaussianTextFrost.swift" \
  "$SOURCE_DIR/NextResetCardView.swift" \
  -O -o "$APP_DIR/Contents/MacOS/TokenPilot" \
  -framework SwiftUI -framework AppKit \
  -module-cache-path "$MODULE_CACHE_DIR"

for MODULE in bridge.py forecasting.py nextreset.py; do
  cp "$SOURCE_DIR/$MODULE" "$APP_DIR/Contents/Resources/$MODULE"
done
chmod 755 "$APP_DIR/Contents/Resources/bridge.py"
chmod 644 "$APP_DIR/Contents/Resources/forecasting.py" "$APP_DIR/Contents/Resources/nextreset.py"

if [ -f "$SOURCE_DIR/TokenPilot.icns" ]; then
  cp "$SOURCE_DIR/TokenPilot.icns" "$APP_DIR/Contents/Resources/TokenPilot.icns"
elif command -v iconutil >/dev/null 2>&1; then
  ICONSET_DIR="$BUILD_DIR/TokenPilot.iconset"
  rm -rf "$ICONSET_DIR"
  swiftc "$SOURCE_DIR/Icon.swift" -O -o "$BUILD_DIR/render-icon" -framework AppKit -module-cache-path "$MODULE_CACHE_DIR"
  "$BUILD_DIR/render-icon" "$ICONSET_DIR"
  iconutil -c icns "$ICONSET_DIR" -o "$APP_DIR/Contents/Resources/TokenPilot.icns"
else
  print -u2 "Error: TokenPilot.icns not found and iconutil is unavailable."
  exit 1
fi

"$PYTHON_BIN" - "$APP_DIR" "$PYTHON_BIN" "$CODEX_BIN" <<'PY'
from pathlib import Path
import json, os, plistlib, sys

if sys.version_info < (3, 10):
    raise SystemExit('Error: Python 3.10+ required.')

app = Path(sys.argv[1]).resolve() / 'Contents'
codex_home = Path(os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')).expanduser()
bundled_bridge = app / 'Resources' / 'bridge.py'

(app / 'Resources' / 'runtime.json').write_text(
    json.dumps(
        {
            'python': sys.argv[2],
            'codex_binary': sys.argv[3],
            'codex_home': str(codex_home),
            'bridge_path': None,
        },
        indent=2,
    )
    + '\n'
)
(app / 'Info.plist').write_bytes(
    plistlib.dumps(
        {
            'CFBundleIconFile': 'TokenPilot.icns',
            'CFBundleName': 'TokenPilot',
            'CFBundleDisplayName': 'TokenPilot',
            'CFBundleIdentifier': 'local.routingfloat.viewer',
            'CFBundleVersion': '16',
            'CFBundleShortVersionString': '2.5',
            'CFBundleExecutable': 'TokenPilot',
            'CFBundlePackageType': 'APPL',
            'LSMinimumSystemVersion': '15.0',
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
            'NSPrincipalClass': 'NSApplication',
        }
    )
)
PY

codesign --force --deep --sign - "$APP_DIR"
print "Built $APP_DIR"
