import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import configure_app


class ConfigureAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bundle_path = self.root / "dist" / "TokenPilot.app"
        self.macos_dir = self.bundle_path / "Contents" / "MacOS"
        self.macos_dir.mkdir(parents=True, exist_ok=True)
        self.executable_path = self.macos_dir / "TokenPilot"
        self.executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(self.executable_path, 0o755)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _completed(
        args: list[str],
        returncode: int = 0,
        payload: dict | None = None,
        raw_stdout: str | None = None,
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        stdout = (
            raw_stdout
            if raw_stdout is not None
            else (json.dumps(payload) if payload is not None else "")
        )
        return subprocess.CompletedProcess(
            args=args,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def test_missing_app_returns_build_first_error_without_subprocess(self) -> None:
        missing_bundle = self.root / "nonexistent" / "TokenPilot.app"
        calls: list[tuple[list[str], dict]] = []

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            raise AssertionError("subprocess must not be called when app bundle is missing")

        out = io.StringIO()
        err = io.StringIO()
        code = configure_app.main(
            ["--app", str(missing_bundle)],
            run_process=fake_run,
            is_tty=False,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        data = json.loads(out.getvalue())
        self.assertEqual(data["status"], "error")
        self.assertFalse(data["ok"])
        self.assertFalse(data["wrote_preferences"])
        self.assertIn("Build.command", data["error"])
        self.assertIn("Build.command", err.getvalue())

    def test_saved_and_system_inference_auto_accepted_without_prompt_or_launch(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            if "--inspect-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "ready",
                        "ok": True,
                        "needs_input": False,
                        "language": "zh-Hans",
                        "language_source": "saved",
                        "theme": "liquid",
                        "theme_source": "saved",
                        "sources": {"language": "saved", "theme": "saved"},
                        "unresolved_fields": [],
                        "questions": [],
                    },
                )
            if "--configure-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "configured",
                        "ok": True,
                        "needs_input": False,
                        "language": "zh-Hans",
                        "language_source": "saved",
                        "theme": "liquid",
                        "theme_source": "saved",
                        "wrote_preferences": False,
                        "custom_colors": {
                            "liquid": {
                                "primaryHex": "#112233",
                                "secondaryHex": "#445566",
                            },
                            "unrelated_key": {"foo": "bar"},
                        },
                        "unrelated_secret_default": "should_not_leak",
                    },
                )
            raise AssertionError(f"Unexpected command: {cmd}")

        input_mock = mock.Mock(side_effect=AssertionError("Should not prompt when resolved"))
        out = io.StringIO()
        err = io.StringIO()
        code = configure_app.main(
            ["--app", str(self.bundle_path)],
            run_process=fake_run,
            input_fn=input_mock,
            is_tty=True,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(code, 0)
        self.assertEqual(input_mock.call_count, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0][0],
            [str(self.executable_path), "--inspect-install-preferences"],
        )
        self.assertEqual(
            calls[1][0],
            [
                str(self.executable_path),
                "--configure-install-preferences",
                "--language",
                "zh-Hans",
                "--theme",
                "liquid",
            ],
        )

        result = json.loads(out.getvalue())
        self.assertEqual(result["status"], "configured")
        self.assertTrue(result["ok"])
        self.assertFalse(result["needs_input"])
        self.assertEqual(result["language"], "zh-Hans")
        self.assertEqual(result["language_source"], "saved")
        self.assertEqual(result["theme"], "liquid")
        self.assertEqual(result["theme_source"], "saved")
        self.assertFalse(result["wrote_preferences"])
        self.assertFalse(result["launched"])
        self.assertEqual(
            result["custom_colors"],
            {"liquid": {"primaryHex": "#112233", "secondaryHex": "#445566"}},
        )
        self.assertNotIn("unrelated_secret_default", result)

    def test_explicit_flags_forwarded_and_override_sources(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if "--inspect-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "ready",
                        "ok": True,
                        "needs_input": False,
                        "language": "en",
                        "language_source": "explicit",
                        "theme": "vibrant",
                        "theme_source": "explicit",
                        "sources": {"language": "explicit", "theme": "explicit"},
                        "unresolved_fields": [],
                        "questions": [],
                    },
                )
            if "--configure-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "configured",
                        "ok": True,
                        "needs_input": False,
                        "language": "en",
                        "theme": "vibrant",
                        "wrote_preferences": True,
                    },
                )
            raise AssertionError(f"Unexpected command: {cmd}")

        out = io.StringIO()
        code = configure_app.main(
            [
                "--app",
                str(self.bundle_path),
                "--language",
                "en",
                "--theme",
                "vibrant",
            ],
            run_process=fake_run,
            is_tty=False,
            stdout=out,
            stderr=io.StringIO(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            calls[0],
            [
                str(self.executable_path),
                "--inspect-install-preferences",
                "--language",
                "en",
                "--theme",
                "vibrant",
            ],
        )
        self.assertEqual(
            calls[1],
            [
                str(self.executable_path),
                "--configure-install-preferences",
                "--language",
                "en",
                "--theme",
                "vibrant",
            ],
        )
        result = json.loads(out.getvalue())
        self.assertEqual(result["sources"], {"language": "explicit", "theme": "explicit"})
        self.assertTrue(result["wrote_preferences"])

    def test_invalid_cli_arguments_fail_before_subprocess(self) -> None:
        for bad_args in (
            ["--app", str(self.bundle_path), "--language", "zh-Hant"],
            ["--app", str(self.bundle_path), "--language", "fr"],
            ["--app", str(self.bundle_path), "--theme", "neon"],
            ["--app", str(self.bundle_path), "--bogus-flag"],
        ):
            with self.subTest(bad_args=bad_args):
                out = io.StringIO()
                run_mock = mock.Mock()
                code = configure_app.main(
                    bad_args,
                    run_process=run_mock,
                    is_tty=False,
                    stdout=out,
                    stderr=io.StringIO(),
                )
                self.assertEqual(code, 2)
                self.assertEqual(run_mock.call_count, 0)
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["status"], "error")
                self.assertFalse(payload["ok"])

    def test_inspect_mode_prints_proposal_and_never_applies_or_launches(self) -> None:
        calls: list[list[str]] = []

        def fake_run_ready(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return self._completed(
                cmd,
                0,
                {
                    "status": "ready",
                    "ok": True,
                    "needs_input": False,
                    "language": "en",
                    "language_source": "system",
                    "theme": "graphite",
                    "theme_source": "system",
                    "sources": {"language": "system", "theme": "system"},
                    "unresolved_fields": [],
                    "questions": [],
                },
            )

        out_ready = io.StringIO()
        code_ready = configure_app.main(
            ["--app", str(self.bundle_path), "--inspect", "--launch"],
            run_process=fake_run_ready,
            is_tty=True,
            stdout=out_ready,
            stderr=io.StringIO(),
        )
        self.assertEqual(code_ready, 0)
        self.assertEqual(len(calls), 1)
        ready_json = json.loads(out_ready.getvalue())
        self.assertEqual(ready_json["status"], "ready")
        self.assertFalse(ready_json["needs_input"])
        self.assertEqual(ready_json["questions"], [])

        calls.clear()

        def fake_run_unresolved(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return self._completed(
                cmd,
                0,
                {
                    "status": "needs_input",
                    "ok": False,
                    "needs_input": True,
                    "language": None,
                    "language_source": "unresolved",
                    "theme": "cupertino",
                    "theme_source": "system",
                    "sources": {"language": "unresolved", "theme": "system"},
                    "unresolved_fields": ["language"],
                    "questions": [configure_app.default_question_for_field("language")],
                },
            )

        out_unresolved = io.StringIO()
        input_mock = mock.Mock(side_effect=AssertionError("Inspect must not prompt"))
        code_unresolved = configure_app.main(
            ["--app", str(self.bundle_path), "--inspect"],
            run_process=fake_run_unresolved,
            input_fn=input_mock,
            is_tty=True,
            stdout=out_unresolved,
            stderr=io.StringIO(),
        )
        self.assertEqual(code_unresolved, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(input_mock.call_count, 0)
        unresolved_json = json.loads(out_unresolved.getvalue())
        self.assertEqual(unresolved_json["status"], "needs_input")
        self.assertTrue(unresolved_json["needs_input"])
        self.assertEqual(unresolved_json["unresolved_fields"], ["language"])
        self.assertEqual([q["field"] for q in unresolved_json["questions"]], ["language"])

    def test_partial_unresolved_non_tty_exits_2_without_writes(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return self._completed(
                cmd,
                0,
                {
                    "status": "needs_input",
                    "ok": False,
                    "needs_input": True,
                    "language": None,
                    "language_source": "unresolved",
                    "theme": "graphite",
                    "theme_source": "system",
                    "sources": {"language": "unresolved", "theme": "system"},
                    "unresolved_fields": ["language"],
                    "questions": [configure_app.default_question_for_field("language")],
                },
            )

        out = io.StringIO()
        code = configure_app.main(
            ["--app", str(self.bundle_path), "--launch"],
            run_process=fake_run,
            is_tty=False,
            stdout=out,
            stderr=io.StringIO(),
        )
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "needs_input")
        self.assertTrue(payload["needs_input"])
        self.assertIsNone(payload["language"])
        self.assertEqual(payload["theme"], "graphite")
        self.assertEqual([q["field"] for q in payload["questions"]], ["language"])

    def test_interactive_tty_prompts_only_missing_field_retries_invalid_and_applies(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if "--inspect-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "needs_input",
                        "ok": False,
                        "needs_input": True,
                        "language": None,
                        "language_source": "unresolved",
                        "theme": "cupertino",
                        "theme_source": "system",
                        "sources": {"language": "unresolved", "theme": "system"},
                        "unresolved_fields": ["language"],
                        "questions": [configure_app.default_question_for_field("language")],
                    },
                )
            if "--configure-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "configured",
                        "ok": True,
                        "needs_input": False,
                        "language": "zh-Hans",
                        "theme": "cupertino",
                        "wrote_preferences": True,
                    },
                )
            raise AssertionError(f"Unexpected command: {cmd}")

        prompts: list[str] = []
        answers = iter(["zh-Hant", "invalid", "zh-Hans"])

        def fake_input(prompt: str) -> str:
            prompts.append(prompt)
            return next(answers)

        out = io.StringIO()
        err = io.StringIO()
        code = configure_app.main(
            ["--app", str(self.bundle_path)],
            run_process=fake_run,
            input_fn=fake_input,
            is_tty=True,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(prompts), 3)
        self.assertTrue(all("language" in p.lower() for p in prompts))
        self.assertIn("Invalid language choice", err.getvalue())
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[1],
            [
                str(self.executable_path),
                "--configure-install-preferences",
                "--language",
                "zh-Hans",
                "--theme",
                "cupertino",
            ],
        )
        result = json.loads(out.getvalue())
        self.assertEqual(result["language"], "zh-Hans")
        self.assertEqual(result["language_source"], "explicit")
        self.assertEqual(result["theme"], "cupertino")
        self.assertEqual(result["theme_source"], "system")

    def test_interactive_tty_asks_both_unresolved_fields_and_applies_once(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if "--inspect-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "needs_input",
                        "ok": False,
                        "needs_input": True,
                        "language": None,
                        "language_source": "unresolved",
                        "theme": None,
                        "theme_source": "unresolved",
                        "sources": {"language": "unresolved", "theme": "unresolved"},
                        "unresolved_fields": ["language", "theme"],
                        "questions": [
                            configure_app.default_question_for_field("language"),
                            configure_app.default_question_for_field("theme"),
                        ],
                    },
                )
            if "--configure-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "configured",
                        "ok": True,
                        "needs_input": False,
                        "language": "en",
                        "theme": "liquid",
                        "wrote_preferences": True,
                    },
                )
            raise AssertionError(f"Unexpected command: {cmd}")

        answers = iter(["1", "unknown-theme", "5"])
        out = io.StringIO()
        err = io.StringIO()
        code = configure_app.main(
            ["--app", str(self.bundle_path)],
            run_process=fake_run,
            input_fn=lambda _: next(answers),
            is_tty=True,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(code, 0)
        self.assertIn("Invalid theme choice", err.getvalue())
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[1],
            [
                str(self.executable_path),
                "--configure-install-preferences",
                "--language",
                "en",
                "--theme",
                "liquid",
            ],
        )

    def test_interactive_eof_or_interrupt_cancels_without_writes(self) -> None:
        for exc_type in (EOFError, KeyboardInterrupt):
            with self.subTest(exc_type=exc_type.__name__):
                calls: list[list[str]] = []

                def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                    calls.append(cmd)
                    if "--inspect-install-preferences" in cmd:
                        return self._completed(
                            cmd,
                            0,
                            {
                                "status": "needs_input",
                                "ok": False,
                                "needs_input": True,
                                "language": None,
                                "language_source": "unresolved",
                                "theme": None,
                                "theme_source": "unresolved",
                                "sources": {"language": "unresolved", "theme": "unresolved"},
                                "unresolved_fields": ["language", "theme"],
                                "questions": [],
                            },
                        )
                    raise AssertionError("Configure must never be called after EOF/interrupt")

                step = 0

                def fake_input(_prompt: str) -> str:
                    nonlocal step
                    step += 1
                    if step == 1:
                        return "en"
                    raise exc_type()

                out = io.StringIO()
                code = configure_app.main(
                    ["--app", str(self.bundle_path), "--launch"],
                    run_process=fake_run,
                    input_fn=fake_input,
                    is_tty=True,
                    stdout=out,
                    stderr=io.StringIO(),
                )
                self.assertEqual(code, 2)
                self.assertEqual(len(calls), 1)
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["status"], "cancelled")
                self.assertFalse(payload["ok"])
                self.assertFalse(payload["wrote_preferences"])
                self.assertFalse(payload["launched"])

    def test_malformed_native_json_and_native_failures(self) -> None:
        # 1. Malformed JSON on inspect
        out1 = io.StringIO()
        code1 = configure_app.main(
            ["--app", str(self.bundle_path)],
            run_process=lambda cmd, **kw: self._completed(cmd, 0, raw_stdout="{not-json"),
            is_tty=False,
            stdout=out1,
            stderr=io.StringIO(),
        )
        self.assertEqual(code1, 2)
        self.assertEqual(json.loads(out1.getvalue())["status"], "error")

        # 2. Unsupported language in native inspect JSON
        out2 = io.StringIO()
        code2 = configure_app.main(
            ["--app", str(self.bundle_path)],
            run_process=lambda cmd, **kw: self._completed(
                cmd,
                0,
                {
                    "status": "ready",
                    "ok": True,
                    "needs_input": False,
                    "language": "fr",
                    "theme": "graphite",
                },
            ),
            is_tty=False,
            stdout=out2,
            stderr=io.StringIO(),
        )
        self.assertEqual(code2, 2)
        self.assertEqual(json.loads(out2.getvalue())["status"], "error")

        # 3. Native configure failure prevents launch
        calls: list[list[str]] = []

        def fake_run_fail_configure(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if "--inspect-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "ready",
                        "ok": True,
                        "needs_input": False,
                        "language": "en",
                        "language_source": "system",
                        "theme": "graphite",
                        "theme_source": "system",
                    },
                )
            return self._completed(
                cmd,
                2,
                {"status": "error", "ok": False, "error": "Write failed"},
            )

        out3 = io.StringIO()
        code3 = configure_app.main(
            ["--app", str(self.bundle_path), "--launch"],
            run_process=fake_run_fail_configure,
            is_tty=False,
            stdout=out3,
            stderr=io.StringIO(),
        )
        self.assertEqual(code3, 2)
        self.assertEqual(len(calls), 2)
        payload3 = json.loads(out3.getvalue())
        self.assertEqual(payload3["status"], "error")
        self.assertFalse(payload3["launched"])

    def test_launch_only_when_explicit_and_argv_safety(self) -> None:
        recorded_calls: list[tuple[list[str], dict]] = []

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            self.assertIsInstance(cmd, list)
            self.assertTrue(all(isinstance(part, str) for part in cmd))
            self.assertFalse(kwargs.get("shell", False))
            recorded_calls.append((cmd, kwargs))
            if "--inspect-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "ready",
                        "ok": True,
                        "needs_input": False,
                        "language": "en",
                        "language_source": "system",
                        "theme": "cupertino",
                        "theme_source": "system",
                    },
                )
            if "--configure-install-preferences" in cmd:
                return self._completed(
                    cmd,
                    0,
                    {
                        "status": "configured",
                        "ok": True,
                        "needs_input": False,
                        "language": "en",
                        "theme": "cupertino",
                        "wrote_preferences": True,
                    },
                )
            if cmd[:2] == ["open", "-a"]:
                return self._completed(cmd, 0, raw_stdout="")
            raise AssertionError(f"Unexpected subprocess command: {cmd}")

        out = io.StringIO()
        code = configure_app.main(
            ["--app", str(self.bundle_path), "--launch"],
            run_process=fake_run,
            is_tty=False,
            stdout=out,
            stderr=io.StringIO(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(recorded_calls), 3)
        self.assertEqual(recorded_calls[2][0], ["open", "-a", str(self.bundle_path)])
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["launched"])


if __name__ == "__main__":
    unittest.main()
