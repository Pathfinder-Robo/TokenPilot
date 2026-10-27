#!/usr/bin/env python3
"""Portable test runner for TokenPilot and the optional Argon routing scripts.
TokenPilot 与可选 Argon 路由脚本的可移植单元测试运行器。
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    tests_dir = Path(__file__).resolve().parent
    root_dir = tests_dir.parent
    for entry in (root_dir / 'app', root_dir / 'routing' / 'scripts', root_dir):
        path_str = str(entry)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    for var in ('GEMINI_API_KEY', 'GEMINI_MODEL'):
        os.environ.pop(var, None)

    with tempfile.TemporaryDirectory() as isolated_codex_home:
        os.environ['CODEX_HOME'] = isolated_codex_home
        suite = unittest.defaultTestLoader.discover(
            start_dir=str(tests_dir),
            pattern='test_*.py',
            top_level_dir=str(tests_dir),
        )
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
