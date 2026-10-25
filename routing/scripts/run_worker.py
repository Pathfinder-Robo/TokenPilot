#!/usr/bin/env python3
"""Launch the installed Google worker with persistent user credentials."""
import json
import os
from pathlib import Path
import sys

import argon_worker
import routing_control


def export_authorized(codex_home):
    try:
        record = json.loads((codex_home / 'argon/authorization.json').read_text())
        return record.get('necessary_task_code_to_google') is True
    except (OSError, ValueError, AttributeError):
        return False


def main():
    codex_home = Path(os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')).expanduser()
    credential = codex_home / 'argon' / 'credentials.env'
    if sys.argv[1:] == ['--check-config']:
        try:
            config = argon_worker.config_file(credential, require_key=False)
            saved_mode = credential.stat().st_mode & 0o777 if credential.exists() else None
            if saved_mode is not None and saved_mode & 0o077:
                raise ValueError('Saved credential permissions must exclude group and other access')
            result = {
                'credential_available': bool(config.get('GEMINI_API_KEY')),
                'code_export_authorized': export_authorized(codex_home),
                'credential_file': str(credential),
                'credential_file_mode': oct(saved_mode) if saved_mode is not None else None,
                'environment_key_override': bool(os.environ.get('GEMINI_API_KEY')),
                'environment_model_override': bool(os.environ.get('GEMINI_MODEL')),
                'model': config.get('GEMINI_MODEL', 'gemini-4-argon'),
                'default_thinking_level': 'high',
                'sandbox_backend': argon_worker.execution_backend(),
                'worker_execution_mode': 'sandboxed_python' if argon_worker.execution_backend() else 'file_tools_with_codex_checks',
                'default_python': sys.executable,
                'api_called': False,
                'routing': routing_control.read_policy(codex_home),
            }
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as error:
            # Do not include exception contents: config errors may refer to secrets.
            print(json.dumps({'status': 'setup_error', 'error_type': type(error).__name__, 'api_called': False}))
            return 2
    if '--env-file' not in sys.argv and not any(a.startswith('--env-file=') for a in sys.argv):
        sys.argv.extend(['--env-file', str(credential)])
    if '--help' not in sys.argv and '-h' not in sys.argv and not export_authorized(codex_home):
        print(json.dumps({'status': 'setup_error', 'code_export_authorized': False,
                          'message': 'Re-enable this user\'s authorization record before sending task code to Google.'}))
        return 2
    if '--help' not in sys.argv and '-h' not in sys.argv:
        try:
            policy = routing_control.read_policy(codex_home)
            if policy['gemini_percent'] == 0:
                print(json.dumps({'status': 'routing_disabled', 'gemini_percent': 0,
                                  'message': 'Routing Float is set to GPT only. No Gemini worker was started.'}))
                return 2
            routing_control.register_run(codex_home, sys.argv[1:])
        except (ValueError, OSError, AttributeError):
            print(json.dumps({'status': 'setup_error', 'message': 'Routing preference or run registry is invalid; repair it in Routing Float.'}))
            return 2
    return argon_worker.main()


if __name__ == '__main__':
    sys.exit(main())
