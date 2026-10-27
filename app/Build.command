#!/usr/bin/env bash
# Self-contained cross-platform builder for TokenPilot (macOS & Linux).
# TokenPilot 的跨平台（macOS 与 Linux）自包含构建脚本。
set -eu

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SOURCE_DIR/.." && pwd)"
APP_DIR="${TOKENPILOT_APP_DIR:-$ROOT_DIR/dist/TokenPilot.app}"
BUILD_DIR="${TOKENPILOT_BUILD_DIR:-$ROOT_DIR/.build}"
MODULE_CACHE_DIR="$BUILD_DIR/module-cache"
PYTHON_BIN="${TOKENPILOT_PYTHON_BIN:-${PYTHON_BIN:-$(command -v python3 || true)}}"
CODEX_BIN="${TOKENPILOT_CODEX_BIN:-${CODEX_BIN:-$(command -v codex || true)}}"
OS_NAME="$(uname -s)"

if [ "$OS_NAME" != "Darwin" ] && [ "$OS_NAME" != "Linux" ]; then
  echo "Error: TokenPilot app build requires macOS (Darwin) or Linux." >&2
  exit 1
fi
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "Error: python3 not found (set TOKENPILOT_PYTHON_BIN to override)." >&2
  exit 1
fi

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$MODULE_CACHE_DIR"

for MODULE in bridge.py forecasting.py nextreset.py linux_app.py; do
  if [ -f "$SOURCE_DIR/$MODULE" ]; then
    cp "$SOURCE_DIR/$MODULE" "$APP_DIR/Contents/Resources/$MODULE"
  fi
done
chmod 755 "$APP_DIR/Contents/Resources/bridge.py"
if [ -f "$APP_DIR/Contents/Resources/linux_app.py" ]; then
  chmod 755 "$APP_DIR/Contents/Resources/linux_app.py"
fi
chmod 644 "$APP_DIR/Contents/Resources/forecasting.py" "$APP_DIR/Contents/Resources/nextreset.py"

if [ -f "$ROOT_DIR/examples/design-preview.html" ]; then
  mkdir -p "$APP_DIR/Contents/Resources/examples"
  cp "$ROOT_DIR/examples/design-preview.html" "$APP_DIR/Contents/Resources/examples/design-preview.html"
fi

if [ "$OS_NAME" = "Darwin" ]; then
  if ! command -v swiftc >/dev/null 2>&1; then
    echo "Error: swiftc not found. Install Xcode or Command Line Tools." >&2
    exit 1
  fi
  if ! command -v codesign >/dev/null 2>&1; then
    echo "Error: codesign not found." >&2
    exit 1
  fi
  if [ -z "$CODEX_BIN" ]; then
    echo "Error: codex CLI not found in PATH (set TOKENPILOT_CODEX_BIN to override)." >&2
    exit 1
  fi

  swiftc \
    "$SOURCE_DIR/main.swift" \
    "$SOURCE_DIR/Appearance.swift" \
    "$SOURCE_DIR/InstallationPreferences.swift" \
    "$SOURCE_DIR/LiquidGlassContent.swift" \
    "$SOURCE_DIR/GaussianTextFrost.swift" \
    "$SOURCE_DIR/NextResetCardView.swift" \
    -O -o "$APP_DIR/Contents/MacOS/TokenPilot" \
    -framework SwiftUI -framework AppKit \
    -module-cache-path "$MODULE_CACHE_DIR"

  if [ -f "$SOURCE_DIR/TokenPilot.icns" ]; then
    cp "$SOURCE_DIR/TokenPilot.icns" "$APP_DIR/Contents/Resources/TokenPilot.icns"
  elif command -v iconutil >/dev/null 2>&1; then
    ICONSET_DIR="$BUILD_DIR/TokenPilot.iconset"
    rm -rf "$ICONSET_DIR"
    swiftc "$SOURCE_DIR/Icon.swift" -O -o "$BUILD_DIR/render-icon" -framework AppKit -module-cache-path "$MODULE_CACHE_DIR"
    "$BUILD_DIR/render-icon" "$ICONSET_DIR"
    iconutil -c icns "$ICONSET_DIR" -o "$APP_DIR/Contents/Resources/TokenPilot.icns"
  else
    echo "Error: TokenPilot.icns not found and iconutil is unavailable." >&2
    exit 1
  fi
else
  if [ -f "$SOURCE_DIR/TokenPilot.icns" ]; then
    cp "$SOURCE_DIR/TokenPilot.icns" "$APP_DIR/Contents/Resources/TokenPilot.icns"
  fi
  if [ -f "$SOURCE_DIR/TokenPilot.png" ]; then
    cp "$SOURCE_DIR/TokenPilot.png" "$APP_DIR/Contents/Resources/TokenPilot.png"
  fi

  cat > "$APP_DIR/Contents/MacOS/TokenPilot" <<EOF
#!/usr/bin/env bash
set -eu
SELF_PATH="\$(readlink -f "\$0" 2>/dev/null || realpath "\$0" 2>/dev/null || echo "\$0")"
SCRIPT_DIR="\$(cd "\$(dirname "\$SELF_PATH")" && pwd)"
RES_DIR="\$(cd "\$SCRIPT_DIR/../Resources" && pwd)"
PYTHON_EXEC="\${TOKENPILOT_PYTHON_BIN:-$PYTHON_BIN}"
exec "\$PYTHON_EXEC" "\$RES_DIR/linux_app.py" "\$@"
EOF
  chmod 755 "$APP_DIR/Contents/MacOS/TokenPilot"
fi

"$PYTHON_BIN" - "$APP_DIR" "$PYTHON_BIN" "${CODEX_BIN:-}" <<'PY'
from pathlib import Path
import json, os, plistlib, sys

if sys.version_info < (3, 10):
    raise SystemExit('Error: Python 3.10+ required.')

app = Path(sys.argv[1]).resolve() / 'Contents'
codex_home = Path(os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')).expanduser()

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
            'CFBundleVersion': '18',
            'CFBundleShortVersionString': '2.7',
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

if [ "$OS_NAME" = "Darwin" ]; then
  codesign --force --deep --sign - "$APP_DIR"
fi
echo "Built $APP_DIR"
