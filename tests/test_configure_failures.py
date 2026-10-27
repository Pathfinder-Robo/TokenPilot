"""Failure-handling and timeout tests for the installation preference configurator.
安装偏好配置器的故障处理、超时与异常状态测试。
"""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import configure_app


class ConfigureFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Path(self.temp.name) / 'Fixture App.app'
        executable = self.app / 'Contents/MacOS/TokenPilot'
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o755)
    def tearDown(self):
        self.temp.cleanup()
    def invoke(self, runner):
        out = io.StringIO()
        code = configure_app.main(['--app', str(self.app)], run_process=runner,
                                  is_tty=False, stdout=out, stderr=io.StringIO())
        return code, json.loads(out.getvalue())
    def ready(self, command):
        return subprocess.CompletedProcess(command, 0, json.dumps({
            'status': 'ready', 'ok': True, 'language': 'en', 'theme': 'liquid',
            'language_source': 'saved', 'theme_source': 'saved'}), '')
    def test_inspection_oserror_is_structured_and_never_configures(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            self.assertEqual(kwargs['timeout'], 20)
            raise PermissionError('fixture unavailable')
        code, value = self.invoke(runner)
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)
        self.assertFalse(value['wrote_preferences'])
        self.assertEqual(value['status'], 'error')
    def test_configuration_timeout_reports_unknown_write_state(self):
        def runner(command, **kwargs):
            if '--inspect-install-preferences' in command:
                return self.ready(command)
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        code, value = self.invoke(runner)
        self.assertEqual(code, 2)
        self.assertIsNone(value['wrote_preferences'])
        self.assertIn('unknown', value['error'])
    def test_empty_native_object_is_not_a_successful_inspection(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{}', '')
        code, value = self.invoke(runner)
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(value['status'], 'error')
    def test_native_must_confirm_requested_values(self):
        def runner(command, **kwargs):
            if '--inspect-install-preferences' in command:
                return self.ready(command)
            return subprocess.CompletedProcess(command, 0, json.dumps({
                'status': 'configured', 'ok': True, 'language': 'en', 'theme': 'vibrant'}), '')
        code, value = self.invoke(runner)
        self.assertEqual(code, 2)
        self.assertEqual(value['status'], 'error')
