#!/usr/bin/env python3
"""Run isolated native preference tests on a Mac with Swift and a graphical session."""
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    if sys.platform != 'darwin':
        print('Native preference tests require macOS and Swift.', file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    app = root / 'app'
    source = (app / 'main.swift').read_text()
    marker = 'let application = NSApplication.shared'
    if source.count(marker) != 1:
        raise SystemExit('Cannot locate the app entry point safely.')
    with tempfile.TemporaryDirectory(prefix='tokenpilot-native-tests-') as directory:
        stage = Path(directory)
        entry = stage / 'main.swift'
        entry.write_text(source.split(marker)[0] + (root / 'tests/native_preferences.swift').read_text())
        binary = stage / 'checks'
        sources = [app / name for name in ('Appearance.swift', 'LiquidGlassContent.swift',
                    'GaussianTextFrost.swift', 'NextResetCardView.swift', 'InstallationPreferences.swift')]
        subprocess.run(['swiftc', str(entry), *map(str, sources), '-o', str(binary),
                        '-framework', 'SwiftUI', '-framework', 'AppKit',
                        '-module-cache-path', str(stage / 'module-cache')], check=True)
        return subprocess.run([str(binary)], check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
