"""Linux build, preference CLI, and HTTP runtime integration tests for TokenPilot.
TokenPilot 的 Linux 构建、外观偏好 CLI 与 HTTP 运行时集成测试。
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "app", ROOT / "routing" / "scripts", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import configure_app  # noqa: E402
import linux_app  # noqa: E402
import run_native_preferences  # noqa: E402


class LinuxBuildAndRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tokenpilot-linux-test-")
        self.stage = Path(self.temp_dir.name)
        self.app_dir = self.stage / "TokenPilot.app"
        self.build_dir = self.stage / "build"
        self.codex_home = self.stage / "codex-home"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.prefs_file = self.codex_home / "argon" / "viewer" / "appearance-settings.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["TOKENPILOT_APP_DIR"] = str(self.app_dir)
        env["TOKENPILOT_BUILD_DIR"] = str(self.build_dir)
        env["TOKENPILOT_PYTHON_BIN"] = sys.executable
        env["CODEX_HOME"] = str(self.codex_home)
        env["TOKENPILOT_PREFERENCES_FILE"] = str(self.prefs_file)
        env["TOKENPILOT_PREFERRED_LANGUAGES"] = "en-US"
        env["TOKENPILOT_SYSTEM_APPEARANCE"] = "dark"
        return env

    def test_build_command_and_ui_checks_succeed_on_linux(self) -> None:
        env = self._build_env()
        build_res = subprocess.run(
            [str(ROOT / "app" / "Build.command")],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(build_res.returncode, 0, build_res.stderr)

        executable = self.app_dir / "Contents" / "MacOS" / "TokenPilot"
        self.assertTrue(executable.is_file())
        self.assertTrue(os.access(executable, os.X_OK))
        self.assertTrue((self.app_dir / "Contents" / "Resources" / "runtime.json").is_file())
        self.assertTrue((self.app_dir / "Contents" / "Resources" / "bridge.py").is_file())
        self.assertTrue((self.app_dir / "Contents" / "Resources" / "linux_app.py").is_file())

        checks_res = subprocess.run(
            [str(executable), "--ui-checks"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(checks_res.returncode, 0, checks_res.stderr)
        self.assertIn("0 failed", checks_res.stdout)

    def test_configure_app_end_to_end_with_built_linux_binary(self) -> None:
        env = self._build_env()
        subprocess.run([str(ROOT / "app" / "Build.command")], env=env, check=True, capture_output=True)

        # 1. Read-only inspect
        inspect_res = subprocess.run(
            [sys.executable, str(ROOT / "configure_app.py"), "--app", str(self.app_dir), "--inspect"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(inspect_res.returncode, 0, inspect_res.stderr)
        inspect_json = json.loads(inspect_res.stdout)
        self.assertEqual(inspect_json["status"], "ready")
        self.assertEqual(inspect_json["language"], "en")
        self.assertEqual(inspect_json["theme"], "graphite")
        self.assertFalse(self.prefs_file.exists())

        # 2. Configure explicit theme & language
        conf_res = subprocess.run(
            [
                sys.executable,
                str(ROOT / "configure_app.py"),
                "--app",
                str(self.app_dir),
                "--language",
                "zh-Hans",
                "--theme",
                "liquid",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(conf_res.returncode, 0, conf_res.stderr)
        conf_json = json.loads(conf_res.stdout)
        self.assertEqual(conf_json["status"], "configured")
        self.assertEqual(conf_json["language"], "zh-Hans")
        self.assertEqual(conf_json["theme"], "liquid")
        self.assertTrue(conf_json["wrote_preferences"])
        self.assertTrue(self.prefs_file.is_file())

        # 3. Repeat configure without changes does not rewrite
        repeat_res = subprocess.run(
            [sys.executable, str(ROOT / "configure_app.py"), "--app", str(self.app_dir)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(repeat_res.returncode, 0, repeat_res.stderr)
        repeat_json = json.loads(repeat_res.stdout)
        self.assertEqual(repeat_json["status"], "configured")
        self.assertEqual(repeat_json["language"], "zh-Hans")
        self.assertEqual(repeat_json["theme"], "liquid")
        self.assertFalse(repeat_json["wrote_preferences"])

    def test_native_preference_suite_runner(self) -> None:
        code = run_native_preferences.run_linux_preference_tests(ROOT)
        self.assertEqual(code, 0)

    def test_linux_http_server_endpoints(self) -> None:
        env = self._build_env()
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "app" / "linux_app.py"),
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
                "--offline",
                "--no-browser",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            url = f"http://127.0.0.1:{port}"
            ready = False
            for _ in range(40):
                try:
                    with urllib.request.urlopen(f"{url}/api/health", timeout=1) as resp:
                        if resp.status == 200:
                            ready = True
                            break
                except Exception:
                    time.sleep(0.1)
            self.assertTrue(ready, "Linux TokenPilot HTTP server did not start in time")

            with urllib.request.urlopen(f"{url}/api/state", timeout=2) as resp:
                state_data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(state_data["ok"])
            self.assertEqual(state_data["snapshot"]["routing"]["gemini_percent"], 75)

            req = urllib.request.Request(
                f"{url}/api/routing",
                data=json.dumps({"gemini_percent": 40}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                route_data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(route_data["ok"])
            self.assertEqual(route_data["snapshot"]["routing"]["gemini_percent"], 40)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()
