#!/usr/bin/env python3
"""Run isolated native preference tests on macOS (Swift) or Linux (Python).
在 macOS（Swift）或 Linux（Python）上运行隔离的原生外观偏好与持久化测试。
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def run_linux_preference_tests(root: Path) -> int:
    app_dir = root / 'app'
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    import linux_app

    count = 0

    def check(ok: bool, message: str) -> None:
        nonlocal count
        count += 1
        if not ok:
            sys.stderr.write(f'FAIL {message}\n')
            raise SystemExit(1)

    def plan(saved=None, languages=None, dark=None):
        raw_lang = saved.get('language') if isinstance(saved, dict) else None
        raw_theme = saved.get('theme') if isinstance(saved, dict) else None
        return linux_app.infer_installation_preferences(
            saved_language=raw_lang,
            saved_theme=raw_theme,
            preferred_languages=languages or [],
            is_dark=dark,
        )

    saved = plan({'language': 'zh-Hans', 'theme': 'liquid'}, ['en-US'], True)
    check(saved['language'] == 'zh-Hans' and saved['theme'] == 'liquid', 'saved choices beat system')
    check(saved['language_source'] == 'saved' and saved['theme_source'] == 'saved', 'saved evidence reported')

    explicit = linux_app.infer_installation_preferences(
        saved_language='en',
        saved_theme='liquid',
        preferred_languages=['en'],
        is_dark=True,
        explicit_language='zh-Hans',
        explicit_theme='vibrant',
    )
    check(explicit['language'] == 'zh-Hans' and explicit['theme'] == 'vibrant', 'explicit choices beat saved')
    check(explicit['language_source'] == 'explicit' and explicit['theme_source'] == 'explicit', 'explicit evidence reported')

    check(plan(None, ['en-US'], True)['theme'] == 'graphite', 'dark maps to Graphite')
    check(plan(None, ['zh-Hans-CN'], False)['theme'] == 'cupertino', 'light maps to Cupertino')

    for tag in ('en', 'en-US', 'en_GB'):
        check(linux_app.infer_system_language([tag]) == 'en', 'English variants')
    for tag in ('zh', 'zh-Hans', 'zh-Hans-CN', 'zh_CN', 'zh-SG'):
        check(linux_app.infer_system_language([tag]) == 'zh-Hans', 'Simplified Chinese variants')
    for tag in ('zh-Hant', 'zh-Hant-TW', 'zh-TW', 'zh-HK', 'zh_MO'):
        check(linux_app.infer_system_language([tag]) is None, 'Traditional Chinese not silently converted')

    check(linux_app.infer_system_language(['zh-Hant', 'en-GB']) == 'en', 'supported secondary language')
    check(linux_app.infer_system_language(['fr-FR', 'de-DE']) is None, 'unsupported preferences require input')
    check(linux_app.infer_system_language([]) is None, 'missing language requires input')

    missing_language = plan(None, ['fr'], True)
    check(
        [q['field'] for q in missing_language['questions']] == ['language']
        and missing_language['theme'] == 'graphite',
        'ask only missing language',
    )

    missing_theme = plan(None, ['en'], None)
    check(
        [q['field'] for q in missing_theme['questions']] == ['theme']
        and missing_theme['language'] == 'en',
        'ask only missing theme',
    )

    partial = plan({'theme': 'liquid'}, ['en-US'], True)
    check(
        partial['theme'] == 'liquid'
        and partial['theme_source'] == 'saved'
        and partial['language_source'] == 'system',
        'partial saved fields inferred independently',
    )

    bad_saved = plan({'theme': 'invalid', 'language': 'invalid'}, ['zh-CN'], False)
    check(
        bad_saved['language'] == 'zh-Hans' and bad_saved['theme'] == 'cupertino',
        'invalid saved fields fall back',
    )

    with tempfile.TemporaryDirectory(prefix='tokenpilot-linux-prefs-') as tmp_dir:
        prefs_file = Path(tmp_dir) / 'appearance-settings.json'

        def make_store():
            return linux_app.AppearanceStore(prefs_path=prefs_file, persists_changes=True)

        def command(args, languages=None, dark=False):
            captured = []
            code = linux_app.handle_installation_preference_command(
                ['TokenPilot', *args],
                appearance=make_store(),
                prefs_path=prefs_file,
                preferred_languages=languages if languages is not None else ['en'],
                is_dark=dark,
                output=captured.append,
            )
            payload = json.loads(captured[0]) if captured else {}
            return code, payload

        inspect = command(['--inspect-install-preferences'], ['fr'], None)
        check(inspect[1].get('needs_input') is True, 'inspection exposes unanswered questions')
        check(not prefs_file.exists(), 'inspection does not write')

        unresolved = command(['--configure-install-preferences'], ['fr'], None)
        check(unresolved[0] == 2 and not prefs_file.exists(), 'unresolved configure does not partly write')

        first = command(['--configure-install-preferences', '--language', 'en', '--theme', 'graphite'])
        check(
            first[0] == 0 and first[1].get('wrote_preferences') is True,
            'first install persists even if matching defaults',
        )
        check(prefs_file.is_file(), 'first install settings actually stored')

        colors = {t: dict(linux_app.DEFAULT_COLOR_PAIRS[t]) for t in linux_app.SUPPORTED_THEMES}
        colors['liquid'] = {'primaryHex': '#FF00DD', 'secondaryHex': '#00FF99'}
        colors['vibrant'] = {'primaryHex': '#AA22CC', 'secondaryHex': '#11DD99'}
        prefs_file.write_text(
            json.dumps({'theme': 'liquid', 'language': 'en', 'customColors': colors}),
            encoding='utf-8',
        )

        changed = command(['--configure-install-preferences', '--language', 'zh-Hans'])
        check(changed[0] == 0, 'valid language-only configure succeeds')
        decoded = json.loads(prefs_file.read_text(encoding='utf-8'))
        check(decoded['theme'] == 'liquid' and decoded['language'] == 'zh-Hans', 'configuration keeps saved theme')
        check(
            decoded['customColors']['liquid'] == colors['liquid']
            and decoded['customColors']['vibrant'] == colors['vibrant'],
            'all custom palettes retained',
        )

        before = prefs_file.read_bytes()
        same = command(['--configure-install-preferences'])
        check(
            same[1].get('wrote_preferences') is False and prefs_file.read_bytes() == before,
            'unchanged saved settings not rewritten',
        )

        for bad_args in (
            ['--configure-install-preferences', '--theme', 'bad'],
            ['--configure-install-preferences', '--language', 'zh-Hant'],
            ['--configure-install-preferences', '--unknown'],
            ['--inspect-install-preferences', '--configure-install-preferences'],
        ):
            res = command(bad_args)
            check(res[0] == 2 and prefs_file.read_bytes() == before, 'invalid or conflicting command never writes')

        raw_partial = {'theme': 'liquid', 'customColors': {'liquid': {'primaryHex': '#FF00DD', 'secondaryHex': '#00FF99'}}}
        prefs_file.write_text(json.dumps(raw_partial), encoding='utf-8')
        repaired = command(['--configure-install-preferences', '--language', 'en'])
        repaired_data = json.loads(prefs_file.read_text(encoding='utf-8'))
        check(
            repaired[0] == 0 and repaired_data['customColors']['liquid'] == colors['liquid'],
            'partial-record palette survives configuration',
        )

        check(
            linux_app.handle_installation_preference_command(
                ['TokenPilot', '--ui-checks'],
                appearance=make_store(),
                prefs_path=prefs_file,
                preferred_languages=[],
                is_dark=None,
                output=lambda _: None,
            )
            is None,
            'other app CLI modes untouched',
        )

    check(
        linux_app.infer_system_language(['zh-Hans-HK']) == 'zh-Hans',
        'explicit Simplified script takes priority over region',
    )
    print(f'Passed {count} independent installation inference and isolated-persistence checks')
    return 0


def main():
    root = Path(__file__).resolve().parent.parent
    if sys.platform != 'darwin':
        return run_linux_preference_tests(root)

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
