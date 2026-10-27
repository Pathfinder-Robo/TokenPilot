#!/usr/bin/env python3
"""Install Argon high routing for this user; no network or model call.
为当前用户安装 Argon 高优任务路由技能；安装过程不发起网络或模型调用。
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import uuid

FILES = (
    'SKILL.md',
    'agents/openai.yaml',
    'scripts/argon_worker.py',
    'scripts/routing_control.py',
    'scripts/run_worker.py',
)


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.argon-' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temp, path)
        path.chmod(mode)
    finally:
        temp.unlink(missing_ok=True)


def install(key=None, *, authorize_code_export=False, replace=False, codex_home=None, user_home=None):
    if not authorize_code_export:
        raise ValueError('Use --authorize-code-export only after agreeing to send necessary task code to Google.')
    if sys.version_info < (3, 10) or platform.system() not in ('Darwin', 'Linux'):
        raise RuntimeError('Requires Python 3.10+ on macOS or Linux.')
    user_home = Path(user_home or Path.home()).resolve()
    codex = Path(codex_home or os.environ.get('CODEX_HOME') or str(user_home / '.codex')).expanduser().resolve()
    source = Path(__file__).resolve().parent / 'routing'
    target = codex / 'skills' / 'argon-routing'
    state = codex / 'argon'
    credential = state / 'credentials.env'
    link = user_home / '.agents/skills/argon-routing'
    if target.is_symlink() or state.is_symlink() or credential.is_symlink():
        raise RuntimeError('Inspect existing skill/state symlinks before installation.')
    if target.exists() and not replace:
        raise RuntimeError('Skill already exists. Review it first; --replace backs it up before updating.')
    if (link.exists() or link.is_symlink()) and link.resolve() != target.resolve():
        raise RuntimeError('The discovery entry already points elsewhere; nothing was changed.')
    data = {}
    for name in FILES:
        path = source / name
        if path.is_symlink():
            raise RuntimeError('Packaged source files must not be symlinks.')
        data[name] = path.read_bytes()
        if name.endswith('.py'):
            compile(data[name], name, 'exec')
        dest = target / name
        if dest.is_symlink() or any((target / p).is_symlink() for p in Path(name).parents if str(p) != '.'):
            raise RuntimeError('Inspect existing destination symlinks before updating.')
    if key is None and credential.exists():
        for line in credential.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#') and line.split('=', 1)[0].strip() == 'GEMINI_API_KEY':
                key = line.split('=', 1)[1].strip().strip('"').strip("'")
                break
    if not key or any(c in key for c in '\n\r\x00'):
        raise ValueError('No valid saved key. Run --configure-key from your own terminal.')
    if any(key.encode() in content for content in data.values()):
        raise ValueError('The package contains the configured secret; do not distribute it.')
    override = codex / 'AGENTS.override.md'
    if override.is_symlink():
        raise RuntimeError('Inspect the global override symlink before updating.')
    agents = override if override.is_file() and override.read_text().strip() else codex / 'AGENTS.md'
    if agents.is_symlink():
        raise RuntimeError('Inspect the global instruction symlink before updating.')
    old = agents.read_text() if agents.exists() else ''
    begin, end = '<!-- BEGIN argon-routing -->', '<!-- END argon-routing -->'
    if (old.count(begin) != old.count(end) or old.count(begin) > 1
            or (begin in old and old.index(begin) > old.index(end))):
        raise RuntimeError('Existing Argon instruction markers need manual repair; nothing was changed.')
    timestamp = datetime.now(timezone.utc).isoformat()
    block = (f'{begin}\n## Preferred coding worker: Argon\n\n'
             f'For substantive coding work, consult `{target / "SKILL.md"}` and prefer the configured '
             'Google Argon worker when task boundaries, tools, and independent acceptance fit. '
             f'Before every substantive coding task and again before launching a worker, run '
             f'`python3 "{target / "scripts/routing_control.py"}"` to read the live `gemini_percent` '
             f'preference from `{state / "routing.json"}` (0: GPT only, launcher blocks new Gemini runs; '
             '1-24: large isolated work only; 25-49: GPT led; 50-74: balanced; 75-99: Gemini preferred; '
             '100: delegate every suitable substantive implementation task while Codex retains verification '
             'and integration). Changes affect new tasks only; do not interrupt active workers when the '
             'slider moves. Default to high; do not reduce effort to save cost. Codex owns decomposition, '
             'independent review/testing, integration, and fallback. Use actual accepted outcomes '
             'and rework to adjust delegation. Handle trivial edits directly.\n\n'
             'This user enabled necessary task-context processing by Google via the setup installer '
             '--authorize-code-export option. Do not repeat that approval for every normal coding '
             'task. Exclude secrets and unrelated data; respect explicit project restrictions and '
             'later instructions. Use the external CLI worker, not native Argon subagents.\n'
             f'{end}\n')
    pattern = re.escape(begin) + '.*?' + re.escape(end) + '\n?'
    new = re.sub(pattern, lambda match: block, old, flags=re.S) if begin in old else old.rstrip() + ('\n\n' if old.strip() else '') + block
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    backup = state / 'backups' / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700)
    if old:
        atomic_write(backup / agents.name, old.encode(), 0o600)
    if target.exists():
        shutil.copytree(target, backup / 'argon-routing', symlinks=True)
    for name in ('credentials.env', 'authorization.json', 'installation.json'):
        if (state / name).is_file():
            atomic_write(backup / name, (state / name).read_bytes(), 0o600)
    for name, content in data.items():
        atomic_write(target / name, content, 0o755 if name.endswith('.py') else 0o644)
    atomic_write(credential, ('GEMINI_API_KEY=' + key + '\nGEMINI_MODEL=gemini-4-argon\n').encode(), 0o600)
    if not (state / 'outcomes.jsonl').exists():
        atomic_write(state / 'outcomes.jsonl', b'', 0o600)
    atomic_write(state / 'authorization.json', (json.dumps({'necessary_task_code_to_google': True,
                 'enabled_at_utc': timestamp, 'basis': 'User invoked --authorize-code-export',
                 'excludes': ['secrets', 'unrelated data', 'explicitly restricted projects']}, indent=2) + '\n').encode(), 0o600)
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists() and not link.is_symlink():
        link.symlink_to(target, target_is_directory=True)
    mode = agents.stat().st_mode & 0o777 if agents.exists() else 0o644
    atomic_write(agents, new.encode(), mode)
    result = {'installed': True, 'verified_live': False, 'skill_dir': str(target),
              'global_instructions': str(agents), 'credential_file': str(credential),
              'credential_mode': '0o600', 'discovery_link': str(link), 'backup_dir': str(backup),
              'default_model': 'gemini-4-argon', 'default_thinking_level': 'high',
              'installed_at_utc': timestamp, 'model_calls_during_install': 0,
              'files_sha256': {name: hashlib.sha256(content).hexdigest() for name, content in data.items()}}
    atomic_write(state / 'installation.json', (json.dumps(result, indent=2) + '\n').encode(), 0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure-key', action='store_true', help='Read your Google key using a hidden terminal prompt')
    parser.add_argument('--authorize-code-export', action='store_true', help='Authorize necessary coding task context to Google and enable the global preference')
    parser.add_argument('--replace', action='store_true', help='Back up and update an existing Argon skill')
    options = parser.parse_args()
    if not options.authorize_code_export:
        parser.error('--authorize-code-export is required to enable this global workflow')
    if options.configure_key and not sys.stdin.isatty():
        parser.error('Enter the API key from your own interactive terminal, never through chat or an echoed pipe')
    try:
        key = getpass.getpass('Google API key (hidden): ').strip() if options.configure_key else None
        result = install(key, authorize_code_export=options.authorize_code_export, replace=options.replace)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print('Installation failed: ' + type(error).__name__ + '. Inspect the paths; no credential value was logged.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
