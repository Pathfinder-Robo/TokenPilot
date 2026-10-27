#!/usr/bin/env python3
"""Live preference and numeric run registry used by the Argon launcher.
供 Argon 启动器使用的实时路由偏好读取器与数值化运行状态注册模块。
"""
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import sys
import uuid


def write_run_state(codex_home, result_dir, *, started_at=None, interaction=None, status=None, usage=None):
    """Publish only numerical usage and lifecycle metadata for the floating viewer."""
    directory = Path(codex_home) / 'argon/usage-cache'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = hashlib.sha256(str(Path(result_dir).expanduser().resolve()).encode()).hexdigest()
    path = directory / (identity + '.json')
    if path.is_symlink():
        raise ValueError('Usage summary must not be a symlink')
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {'started_at': started_at or datetime.now(timezone.utc).isoformat(),
                 'status': 'running', 'interactions': {}}
    if started_at:
        state['started_at'] = started_at
    if interaction:
        counts = {key: value for key, value in (interaction.get('usage') or {}).items()
                  if isinstance(value, (int, float)) and not isinstance(value, bool)}
        state['interactions'][interaction['id']] = {'id': interaction['id'],
            'created': interaction.get('created') or state['started_at'], 'usage': counts}
    if status:
        state['status'] = status
    if usage is not None:
        state['usage'] = {key: value for key, value in usage.items()
                          if isinstance(value, (int, float)) and not isinstance(value, bool)}
    temporary = path.with_suffix('.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_policy(codex_home):
    path = Path(codex_home) / 'argon/routing.json'
    if not path.exists():
        return {'gemini_percent': 75}
    policy = json.loads(path.read_text())
    if not isinstance(policy, dict):
        raise ValueError('Invalid routing preference')
    percent = policy.get('gemini_percent')
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError('Invalid routing preference; choose 0–100 in Routing Float')
    return policy


def describe(percent):
    if percent == 0:
        return 'GPT only: no new Gemini workers'
    if percent < 25:
        return 'GPT preferred: Gemini only for large, clearly isolated implementation'
    if percent < 50:
        return 'GPT led: delegate substantial isolated modules when worthwhile'
    if percent < 75:
        return 'Balanced: Gemini for self-contained implementation; GPT for small or coupled work'
    if percent < 100:
        return 'Gemini preferred: delegate most suitable substantive coding work'
    return 'Gemini maximum: delegate every suitable implementation task; GPT still verifies and integrates'


def register_run(codex_home, arguments):
    result = None
    for index, argument in enumerate(arguments):
        if argument.startswith('--result-dir='):
            result = argument.split('=', 1)[1]
        elif argument == '--result-dir' and index + 1 < len(arguments):
            result = arguments[index + 1]
    if not result:
        return
    directory = Path(codex_home) / 'argon'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'runs.jsonl'
    if path.is_symlink():
        raise ValueError('Run registry must not be a symlink')
    record = {'result_dir': str(Path(result).expanduser().resolve()),
              'started_at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid()}
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, 'a') as stream:
        stream.write(json.dumps(record) + '\n')
    write_run_state(codex_home, record['result_dir'], started_at=record['started_at'])


if __name__ == '__main__':
    home = Path(os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')).expanduser()
    try:
        policy = read_policy(home)
        print(json.dumps(dict(policy, guidance=describe(policy['gemini_percent']), thinking_level='high')))
    except (ValueError, OSError, AttributeError):
        print(json.dumps({'error': 'Invalid or unreadable routing preference', 'gemini_allowed': False}))
        sys.exit(2)
