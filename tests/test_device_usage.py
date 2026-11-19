import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'app', ROOT / 'routing' / 'scripts', ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import bridge
import chat_attribution
import device_usage
import routing_control

TZ_NAME = 'Asia/Shanghai'
TZ = ZoneInfo(TZ_NAME)
NOW_ISO = '2026-10-24T08:00:00Z'
NOW_EPOCH = datetime.fromisoformat(NOW_ISO.replace('Z', '+00:00')).timestamp()


def codex_record(response_id, stamp, total=100, cached=70, inp=90, out=10, extra=None):
    usage = {
        'total_tokens': total,
        'cached_input_tokens': cached,
        'input_tokens': inp,
        'output_tokens': out,
    }
    if extra:
        usage.update(extra)
    payload = {'usage': usage}
    if response_id is not None:
        payload['response_id'] = response_id
    return {
        'type': 'token_usage_record',
        'timestamp': stamp,
        'payload': payload,
        'message': 'confidential conversation body that must never be exported',
    }


def codex_fallback_record(stamp, total, cached=0, inp=0, out=0):
    return {
        'type': 'event_msg',
        'timestamp': stamp,
        'payload': {
            'type': 'token_count',
            'info': {
                'total_token_usage': {
                    'total_tokens': total,
                    'cached_input_tokens': cached,
                    'input_tokens': inp,
                    'output_tokens': out,
                }
            },
        },
    }


class DeviceUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mac_home = self.root / 'mac-codex'
        self.ubuntu_home = self.root / 'ubuntu-codex'
        for home in (self.mac_home, self.ubuntu_home):
            (home / 'sessions').mkdir(parents=True)
            (home / 'argon/devices').mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_jsonl(self, path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')

    def write_manifest(self, snapshot_file='ubuntu.json'):
        manifest = {
            'version': 1,
            'local_device': {'id': 'mac', 'name': 'Mac'},
            'remotes': [
                {'id': 'ubuntu', 'name': 'Ubuntu', 'snapshot_file': snapshot_file}
            ],
        }
        (self.mac_home / 'argon/devices.json').write_text(json.dumps(manifest), encoding='utf-8')

    def write_remote_snapshot(self, payload, filename='ubuntu.json'):
        target = self.mac_home / 'argon/devices' / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding='utf-8')
        return target

    def test_default_without_manifest_remains_local_only(self):
        self.write_jsonl(
            self.mac_home / 'sessions/local.jsonl',
            [codex_record('local-1', '2026-10-24T01:00:00Z', total=150, cached=50, inp=120, out=30)],
        )
        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        snap = b.snapshot(refresh=False, now=NOW_EPOCH)
        self.assertNotIn('devices', snap)
        self.assertNotIn('totals', snap)
        self.assertEqual(snap['codex']['total_tokens'], 150)
        self.assertEqual(snap['routing']['gemini_percent'], 75)

    def test_two_device_sum_and_local_top_level_preserved(self):
        self.write_manifest()
        # Local Mac usage: Codex 200, Gemini 80
        self.write_jsonl(
            self.mac_home / 'sessions/mac.jsonl',
            [codex_record('mac-resp-1', '2026-10-24T02:00:00Z', total=200, cached=60, inp=160, out=40)],
        )
        routing_control.write_run_state(
            self.mac_home,
            self.mac_home / 'mac-run-1',
            started_at='2026-10-24T02:10:00Z',
            interaction={
                'id': 'mac-gem-1',
                'created': '2026-10-24T02:11:00Z',
                'usage': {
                    'total_tokens': 80,
                    'total_cached_tokens': 20,
                    'total_input_tokens': 50,
                    'total_output_tokens': 25,
                    'total_thought_tokens': 5,
                },
            },
            status='completed',
        )

        # Remote Ubuntu usage: Codex 350, Gemini 120
        self.write_jsonl(
            self.ubuntu_home / 'sessions/ubuntu.jsonl',
            [codex_record('ubuntu-resp-1', '2026-10-24T03:00:00Z', total=350, cached=100, inp=300, out=50)],
        )
        routing_control.write_run_state(
            self.ubuntu_home,
            self.ubuntu_home / 'ubuntu-run-1',
            started_at='2026-10-24T03:10:00Z',
            interaction={
                'id': 'ubuntu-gem-1',
                'created': '2026-10-24T03:11:00Z',
                'usage': {
                    'total_tokens': 120,
                    'total_cached_tokens': 30,
                    'total_input_tokens': 80,
                    'total_output_tokens': 30,
                    'total_thought_tokens': 10,
                },
            },
            status='completed',
        )

        exporter = device_usage.DeviceExporter(
            codex_home=self.ubuntu_home,
            device_id='ubuntu',
            device_name='Ubuntu',
            timezone=TZ_NAME,
        )
        remote_snap = exporter.snapshot(now=NOW_EPOCH - 15)
        self.write_remote_snapshot(remote_snap)

        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        snap = b.snapshot(refresh=False, now=NOW_EPOCH)

        # Top-level codex and gemini must stay Mac LOCAL
        self.assertEqual(snap['codex']['total_tokens'], 200)
        self.assertEqual(snap['gemini']['total_tokens'], 80)

        # Devices breakdown has Mac and Ubuntu rows
        self.assertEqual(len(snap['devices']), 2)
        mac_row, ubuntu_row = snap['devices']
        self.assertEqual(mac_row['id'], 'mac')
        self.assertEqual(mac_row['name'], 'Mac')
        self.assertEqual(mac_row['status'], 'fresh')
        self.assertEqual(mac_row['age_seconds'], 0)
        self.assertEqual(mac_row['codex']['total_tokens'], 200)
        self.assertEqual(mac_row['gemini']['total_tokens'], 80)

        self.assertEqual(ubuntu_row['id'], 'ubuntu')
        self.assertEqual(ubuntu_row['name'], 'Ubuntu')
        self.assertEqual(ubuntu_row['status'], 'fresh')
        self.assertEqual(ubuntu_row['age_seconds'], 15)
        self.assertEqual(ubuntu_row['codex']['total_tokens'], 350)
        self.assertEqual(ubuntu_row['gemini']['total_tokens'], 120)

        # Combined totals sum distinct events
        totals = snap['totals']
        self.assertFalse(totals['partial'])
        self.assertEqual(totals['duplicate_events'], 0)
        self.assertEqual(totals['codex']['total_tokens'], 550)
        self.assertEqual(totals['codex']['cached_tokens'], 160)
        self.assertEqual(totals['codex']['input_tokens'], 460)
        self.assertEqual(totals['codex']['output_tokens'], 90)
        self.assertEqual(totals['gemini']['total_tokens'], 200)
        self.assertEqual(totals['gemini']['cached_tokens'], 50)
        self.assertEqual(totals['gemini']['input_tokens'], 130)
        self.assertEqual(totals['gemini']['output_tokens'], 70)  # (25+5) + (30+10)
        self.assertEqual(snap['warnings'], [])

    def test_copied_event_dedup_across_devices_and_legacy_fallback(self):
        self.write_manifest()
        shared_codex = codex_record('shared-resp', '2026-10-24T01:00:00Z', total=100, cached=40, inp=80, out=20)
        mac_only_codex = codex_record('mac-only', '2026-10-24T02:00:00Z', total=250, cached=50, inp=200, out=50)
        ubuntu_only_codex = codex_record('ubuntu-only', '2026-10-24T03:00:00Z', total=400, cached=100, inp=350, out=50)

        # Legacy fallback session copied to Mac sessions, Mac archived_sessions, and Ubuntu sessions
        fallback_events = [
            codex_fallback_record('2026-10-23T15:00:00Z', 100),  # Yesterday in Asia/Shanghai
            codex_fallback_record('2026-10-24T00:00:00Z', 160, cached=20, inp=50, out=10),  # +60 delta today
            codex_fallback_record('2026-10-24T00:05:00Z', 190, cached=30, inp=70, out=20),  # +30 delta today
        ]

        self.write_jsonl(self.mac_home / 'sessions/primary.jsonl', [shared_codex, mac_only_codex])
        self.write_jsonl(self.mac_home / 'sessions/legacy.jsonl', fallback_events)
        (self.mac_home / 'archived_sessions').mkdir()
        self.write_jsonl(self.mac_home / 'archived_sessions/legacy.jsonl', fallback_events)

        self.write_jsonl(self.ubuntu_home / 'sessions/primary.jsonl', [shared_codex, ubuntu_only_codex])
        self.write_jsonl(self.ubuntu_home / 'archived_sessions/legacy.jsonl', fallback_events)

        # Shared Gemini interaction present in Mac usage-cache and Ubuntu registered result_dir
        shared_gem_interaction = {
            'id': 'shared-gem-id',
            'created': '2026-10-24T04:00:00Z',
            'usage': {
                'total_tokens': 90,
                'total_cached_tokens': 10,
                'total_input_tokens': 70,
                'total_output_tokens': 15,
                'total_thought_tokens': 5,
            },
        }
        routing_control.write_run_state(
            self.mac_home,
            self.mac_home / 'mac-gem-run',
            started_at='2026-10-24T04:00:00Z',
            interaction=shared_gem_interaction,
            status='completed',
        )
        ubuntu_run_dir = self.ubuntu_home / 'ubuntu-gem-run'
        ubuntu_run_dir.mkdir(parents=True)
        self.write_jsonl(
            self.ubuntu_home / 'argon/runs.jsonl',
            [{'result_dir': str(ubuntu_run_dir), 'started_at': '2026-10-24T04:00:00Z'}],
        )
        (ubuntu_run_dir / 'interaction-01.json').write_text(json.dumps(shared_gem_interaction), encoding='utf-8')
        (ubuntu_run_dir / 'result.json').write_text(json.dumps({'status': 'completed'}), encoding='utf-8')

        exporter = device_usage.DeviceExporter(self.ubuntu_home, 'ubuntu', 'Ubuntu', TZ_NAME)
        self.write_remote_snapshot(exporter(now=NOW_EPOCH - 5))

        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        snap = b.snapshot(refresh=False, now=NOW_EPOCH)

        # Mac observed Codex: 100 (shared) + 250 (mac-only) + 90 (legacy 60+30) = 440
        # Ubuntu observed Codex: 100 (shared) + 400 (ubuntu-only) + 90 (legacy 60+30) = 590
        # Combined deduplicated Codex: 100 + 250 + 400 + 90 = 840
        self.assertEqual(snap['devices'][0]['codex']['total_tokens'], 440)
        self.assertEqual(snap['devices'][1]['codex']['total_tokens'], 590)
        self.assertEqual(snap['totals']['codex']['total_tokens'], 840)

        # Mac observed Gemini: 90, Ubuntu observed Gemini: 90, Combined deduplicated Gemini: 90
        self.assertEqual(snap['devices'][0]['gemini']['total_tokens'], 90)
        self.assertEqual(snap['devices'][1]['gemini']['total_tokens'], 90)
        self.assertEqual(snap['totals']['gemini']['total_tokens'], 90)

        # 1 shared primary codex + 2 shared legacy fallback codex + 1 shared gemini = 4 duplicate events
        self.assertEqual(snap['totals']['duplicate_events'], 4)
        self.assertFalse(snap['totals']['partial'])

    def test_conflicting_identity_counts_warn_and_do_not_sum(self):
        self.write_manifest()
        self.write_jsonl(
            self.mac_home / 'sessions/a.jsonl',
            [codex_record('conflict-resp', '2026-10-24T01:00:00Z', total=100, cached=20, inp=80, out=20)],
        )
        self.write_jsonl(
            self.ubuntu_home / 'sessions/a.jsonl',
            [codex_record('conflict-resp', '2026-10-24T01:00:00Z', total=150, cached=30, inp=120, out=30)],
        )
        exporter = device_usage.DeviceExporter(self.ubuntu_home, 'ubuntu', 'Ubuntu', TZ_NAME)
        self.write_remote_snapshot(exporter.snapshot(now=NOW_EPOCH))

        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        snap = b.snapshot(refresh=False, now=NOW_EPOCH)

        # Must NOT sum 100 + 150 = 250; resolves deterministically to 150 and marks partial with warning
        self.assertEqual(snap['totals']['codex']['total_tokens'], 150)
        self.assertTrue(snap['totals']['partial'])
        self.assertEqual(snap['totals']['duplicate_events'], 1)
        self.assertEqual(snap['totals'].get('conflict_events'), 1)
        self.assertTrue(any('Conflicting' in w for w in snap['warnings']))

    def test_missing_stale_yesterday_and_midnight_timezone_rollover(self):
        self.write_manifest()
        # Event at 2026-10-23T15:59:59Z is 23:59:59 on Oct 23 in Asia/Shanghai (yesterday)
        # Event at 2026-10-23T16:00:00Z is 00:00:00 on Oct 24 in Asia/Shanghai (today)
        self.write_jsonl(
            self.mac_home / 'sessions/tz.jsonl',
            [
                codex_record('before-midnight', '2026-10-23T15:59:59Z', total=900),
                codex_record('at-midnight', '2026-10-23T16:00:00Z', total=110),
            ],
        )
        self.write_jsonl(
            self.ubuntu_home / 'sessions/tz.jsonl',
            [
                codex_record('u-before-midnight', '2026-10-23T15:59:59Z', total=800),
                codex_record('u-at-midnight', '2026-10-23T16:00:00Z', total=220),
            ],
        )

        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)

        # 1. Missing remote snapshot -> unavailable (unknown, not 0), partial total
        snap_missing = b.snapshot(refresh=False, now=NOW_EPOCH)
        ubuntu_missing = snap_missing['devices'][1]
        self.assertEqual(ubuntu_missing['status'], 'unavailable')
        self.assertIsNone(ubuntu_missing['age_seconds'])
        self.assertFalse(ubuntu_missing['codex']['available'])
        self.assertFalse(ubuntu_missing['gemini']['available'])
        self.assertTrue(snap_missing['totals']['partial'])
        self.assertEqual(snap_missing['totals']['codex']['total_tokens'], 110)
        self.assertTrue(any('unavailable' in w for w in snap_missing['warnings']))

        # 2. Stale remote snapshot (>60s old) -> status 'stale', still displayed with age and partial warning
        exporter = device_usage.DeviceExporter(self.ubuntu_home, 'ubuntu', 'Ubuntu', TZ_NAME)
        stale_payload = exporter.snapshot(now=NOW_EPOCH - 125)
        self.write_remote_snapshot(stale_payload)

        snap_stale = b.snapshot(refresh=False, now=NOW_EPOCH)
        ubuntu_stale = snap_stale['devices'][1]
        self.assertEqual(ubuntu_stale['status'], 'stale')
        self.assertEqual(ubuntu_stale['age_seconds'], 125)
        self.assertTrue(ubuntu_stale['codex']['available'])
        self.assertEqual(ubuntu_stale['codex']['total_tokens'], 220)
        self.assertEqual(snap_stale['totals']['codex']['total_tokens'], 330)
        self.assertTrue(snap_stale['totals']['partial'])
        self.assertTrue(any('stale' in w for w in snap_stale['warnings']))

        # 3. Advance clock across midnight in Asia/Shanghai to 2026-10-25 (2026-10-24T16:00:05Z)
        # Yesterday's snapshot on disk and in memory cache must NOT be treated as today's usage!
        tomorrow_epoch = datetime.fromisoformat('2026-10-24T16:00:05+00:00').timestamp()
        snap_tomorrow = b.snapshot(refresh=False, now=tomorrow_epoch)
        ubuntu_tomorrow = snap_tomorrow['devices'][1]
        self.assertEqual(snap_tomorrow['day'], '2026-10-25')
        self.assertEqual(ubuntu_tomorrow['status'], 'unavailable')
        self.assertFalse(ubuntu_tomorrow['codex']['available'])
        self.assertFalse(ubuntu_tomorrow['gemini']['available'])
        self.assertEqual(snap_tomorrow['totals']['codex']['total_tokens'], 0)
        self.assertTrue(snap_tomorrow['totals']['partial'])

    def test_corrupted_future_bad_metadata_bad_counts_and_preserve_latest_good(self):
        self.write_manifest()
        self.write_jsonl(
            self.mac_home / 'sessions/mac.jsonl',
            [codex_record('mac-1', '2026-10-24T01:00:00Z', total=100)],
        )
        self.write_jsonl(
            self.ubuntu_home / 'sessions/ubuntu.jsonl',
            [codex_record('ubuntu-1', '2026-10-24T01:00:00Z', total=300)],
        )
        exporter = device_usage.DeviceExporter(self.ubuntu_home, 'ubuntu', 'Ubuntu', TZ_NAME)
        good_payload = exporter.snapshot(now=NOW_EPOCH - 10)
        snap_path = self.write_remote_snapshot(good_payload)

        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        initial = b.snapshot(refresh=False, now=NOW_EPOCH)
        self.assertEqual(initial['totals']['codex']['total_tokens'], 400)
        self.assertFalse(initial['totals']['partial'])

        # Prepare invalid payloads
        future_payload = copy.deepcopy(good_payload)
        future_payload['collected_at'] = '2026-10-24T08:02:00Z'  # +120s in the future

        wrong_tz = copy.deepcopy(good_payload)
        wrong_tz['timezone'] = 'America/New_York'

        wrong_day = copy.deepcopy(good_payload)
        wrong_day['day'] = '2026-10-23'

        wrong_id = copy.deepcopy(good_payload)
        wrong_id['device_id'] = 'intruder'

        wrong_schema = copy.deepcopy(good_payload)
        wrong_schema['schema'] = 2

        neg_tokens = copy.deepcopy(good_payload)
        neg_tokens['providers']['codex']['events'][0]['total_tokens'] = -5
        neg_tokens['providers']['codex']['total_tokens'] = -5

        bool_tokens = copy.deepcopy(good_payload)
        bool_tokens['providers']['codex']['events'][0]['total_tokens'] = True
        bool_tokens['providers']['codex']['total_tokens'] = True

        unhashed_id = copy.deepcopy(good_payload)
        unhashed_id['providers']['codex']['events'][0]['id'] = 'ubuntu-1'

        missing_events = copy.deepcopy(good_payload)
        missing_events['providers']['codex']['events'] = []  # total_tokens is still 300!

        invalid_texts = [
            '{corrupted json',
            json.dumps(future_payload),
            json.dumps(wrong_tz),
            json.dumps(wrong_day),
            json.dumps(wrong_id),
            json.dumps(wrong_schema),
            json.dumps(neg_tokens),
            json.dumps(bool_tokens),
            json.dumps(unhashed_id),
            json.dumps(missing_events),
            json.dumps(good_payload).replace('300', 'NaN'),
            json.dumps(good_payload).replace('300', 'Infinity'),
        ]

        for raw_text in invalid_texts:
            snap_path.write_text(raw_text, encoding='utf-8')
            # 1) On existing Bridge with a prior good snapshot, latest good snapshot is preserved!
            preserved = b.snapshot(refresh=False, now=NOW_EPOCH + 5)
            self.assertEqual(preserved['devices'][1]['status'], 'fresh')
            self.assertEqual(preserved['devices'][1]['age_seconds'], 15)
            self.assertEqual(preserved['devices'][1]['codex']['total_tokens'], 300)
            self.assertEqual(preserved['totals']['codex']['total_tokens'], 400)
            self.assertTrue(preserved['totals']['partial'])
            self.assertTrue(any('preserving previous good snapshot' in w for w in preserved['warnings']))

            # 2) On a fresh Bridge with no prior good snapshot, the invalid file is rejected as unavailable!
            fresh_bridge = bridge.Bridge(self.mac_home, None, TZ, offline=True)
            rejected = fresh_bridge.snapshot(refresh=False, now=NOW_EPOCH + 5)
            self.assertEqual(rejected['devices'][1]['status'], 'unavailable')
            self.assertFalse(rejected['devices'][1]['codex']['available'])
            self.assertEqual(rejected['totals']['codex']['total_tokens'], 100)
            self.assertTrue(rejected['totals']['partial'])

    def test_snapshot_path_traversal_and_symlink_rejected(self):
        secret_file = self.mac_home / 'auth.json'
        secret_file.write_text(json.dumps({'access_token': 'super-secret-token'}), encoding='utf-8')

        # 1. Traversal outside argon/devices
        self.write_manifest(snapshot_file='../../auth.json')
        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        snap = b.snapshot(refresh=False, now=NOW_EPOCH)
        self.assertEqual(snap['devices'][1]['status'], 'unavailable')
        self.assertTrue(snap['totals']['partial'])
        self.assertTrue(any('rejected' in w for w in snap['warnings']))

        # 2. Symlink inside argon/devices pointing to outside file
        outside = self.root / 'outside.json'
        outside.write_text('{}', encoding='utf-8')
        symlink_path = self.mac_home / 'argon/devices/ubuntu.json'
        symlink_path.symlink_to(outside)
        self.write_manifest(snapshot_file='ubuntu.json')
        snap_sym = b.snapshot(refresh=False, now=NOW_EPOCH)
        self.assertEqual(snap_sym['devices'][1]['status'], 'unavailable')
        self.assertTrue(snap_sym['totals']['partial'])

    def test_exported_metadata_excludes_secrets_paths_text_and_arbitrary_keys(self):
        # Create fake credentials and auth.json that must never be read or exported
        secret_marker = 'sk-secret-do-not-export-987654321'
        (self.ubuntu_home / 'auth.json').write_text(json.dumps({'api_key': secret_marker}), encoding='utf-8')
        (self.ubuntu_home / 'argon/credentials.env').write_text(f'GEMINI_API_KEY={secret_marker}\n', encoding='utf-8')

        chat_secret = 'Sensitive user prompt and source code snippet'
        session_file = self.ubuntu_home / 'sessions/secret_session_name.jsonl'
        self.write_jsonl(
            session_file,
            [
                {
                    'type': 'token_usage_record',
                    'timestamp': '2026-10-24T05:00:00Z',
                    'message': chat_secret,
                    'payload': {
                        'response_id': 'raw-codex-response-id',
                        'usage': {
                            'total_tokens': 500,
                            'cached_input_tokens': 200,
                            'input_tokens': 400,
                            'output_tokens': 100,
                            'arbitrary_secret_key': secret_marker,
                        },
                    },
                }
            ],
        )

        run_dir = self.ubuntu_home / 'private_workspace_run_dir'
        run_dir.mkdir(parents=True)
        self.write_jsonl(
            self.ubuntu_home / 'argon/runs.jsonl',
            [{'result_dir': str(run_dir), 'started_at': '2026-10-24T05:10:00Z'}],
        )
        (run_dir / 'interaction-01.json').write_text(
            json.dumps({
                'id': 'raw-gemini-interaction-id',
                'created': '2026-10-24T05:11:00Z',
                'prompt': chat_secret,
                'steps': [{'text': chat_secret}],
                'usage': {
                    'total_tokens': 300,
                    'total_cached_tokens': 50,
                    'total_input_tokens': 200,
                    'total_output_tokens': 70,
                    'total_thought_tokens': 30,
                    'custom_extra_usage': 9999,
                },
            }),
            encoding='utf-8',
        )
        (run_dir / 'result.json').write_text(
            json.dumps({'status': 'cancelled', 'output_text': chat_secret}),
            encoding='utf-8',
        )

        cli_res = subprocess.run(
            [
                sys.executable,
                str(ROOT / 'app/device_usage.py'),
                '--export',
                '--codex-home',
                str(self.ubuntu_home),
                '--device-id',
                'ubuntu',
                '--device-name',
                'Ubuntu',
                '--timezone',
                TZ_NAME,
                '--now',
                NOW_ISO,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        raw_out = cli_res.stdout
        for forbidden in (
            secret_marker,
            chat_secret,
            'secret_session_name',
            'private_workspace_run_dir',
            'interaction-01.json',
            'raw-codex-response-id',
            'raw-gemini-interaction-id',
            'arbitrary_secret_key',
            'custom_extra_usage',
            str(self.ubuntu_home),
        ):
            self.assertNotIn(forbidden, raw_out)

        parsed = json.loads(raw_out)
        self.assertEqual(parsed['schema'], 1)
        self.assertEqual(parsed['device_id'], 'ubuntu')
        self.assertEqual(parsed['device_name'], 'Ubuntu')
        self.assertEqual(parsed['day'], '2026-10-24')
        self.assertEqual(parsed['timezone'], TZ_NAME)

        codex_events = parsed['providers']['codex']['events']
        self.assertEqual(len(codex_events), 1)
        self.assertEqual(
            set(codex_events[0].keys()),
            {'id', 'total_tokens', 'cached_tokens', 'input_tokens', 'output_tokens'},
        )
        self.assertEqual(codex_events[0]['total_tokens'], 500)

        gemini_prov = parsed['providers']['gemini']
        self.assertEqual(gemini_prov['status'], 'paused')
        self.assertIn('supervisor', gemini_prov['status_message'])
        self.assertEqual(gemini_prov['failed_runs_today'], 0)
        self.assertEqual(gemini_prov['output_tokens'], 100)  # 70 output + 30 thoughts
        self.assertEqual(len(gemini_prov['events']), 1)
        self.assertEqual(
            set(gemini_prov['events'][0].keys()),
            {'id', 'total_tokens', 'cached_tokens', 'input_tokens', 'output_tokens'},
        )

    def test_missing_events_and_unsupported_identity_do_not_fabricate_totals(self):
        # 1. Gemini result.json has usage > 0 but no interaction-*.json files exist
        run_dir = self.ubuntu_home / 'unbacked-run'
        run_dir.mkdir(parents=True)
        self.write_jsonl(
            self.ubuntu_home / 'argon/runs.jsonl',
            [{'result_dir': str(run_dir), 'started_at': '2026-10-24T06:00:00Z'}],
        )
        (run_dir / 'result.json').write_text(
            json.dumps({'status': 'completed', 'usage': {'total_tokens': 5000}}),
            encoding='utf-8',
        )

        # 2. Codex token_usage_record missing response_id
        self.write_jsonl(
            self.ubuntu_home / 'sessions/bad_id.jsonl',
            [codex_record(None, '2026-10-24T06:00:00Z', total=700)],
        )

        exporter = device_usage.DeviceExporter(self.ubuntu_home, 'ubuntu', 'Ubuntu', TZ_NAME)
        snap = exporter.snapshot(now=NOW_EPOCH)

        # Neither provider may fabricate 5000 / 700 or claim an authoritative 0
        self.assertEqual(snap['providers']['codex']['total_tokens'], 0)
        self.assertFalse(snap['providers']['codex']['available'])
        self.assertTrue(snap['providers']['codex']['partial'])

        self.assertEqual(snap['providers']['gemini']['total_tokens'], 0)
        self.assertFalse(snap['providers']['gemini']['available'])
        self.assertTrue(snap['providers']['gemini']['partial'])

    def test_rolling_3h_chart_crosses_midnight_and_reset_with_schema2_dedup(self):
        # 2026-10-24T17:00:00Z == 2026-10-25 01:00:00+08:00 in Asia/Shanghai.
        # Local midnight is 16:00:00Z (now - 1h); rolling 3h chart spans [14:00:00Z, 17:00:00Z].
        cross_now_iso = '2026-10-24T17:00:00Z'
        cross_now_epoch = datetime.fromisoformat(cross_now_iso.replace('Z', '+00:00')).timestamp()

        u_shared = '11111111-1111-1111-1111-111111111111'
        u_ubuntu_pre = '22222222-2222-2222-2222-222222222222'
        u_mac_pre = '33333333-3333-3333-3333-333333333333'
        u_mac_post = '44444444-4444-4444-4444-444444444444'
        u_old = '55555555-5555-5555-5555-555555555555'

        k_shared = chat_attribution.hash_chat_key(u_shared)
        k_ubuntu_pre = chat_attribution.hash_chat_key(u_ubuntu_pre)
        k_mac_pre = chat_attribution.hash_chat_key(u_mac_pre)
        k_mac_post = chat_attribution.hash_chat_key(u_mac_post)

        self.write_jsonl(
            self.mac_home / 'session_index.jsonl',
            [
                {'id': u_shared, 'thread_name': 'Shared Cross-Device'},
                {'id': u_mac_pre, 'thread_name': 'Mac Yesterday'},
                {'id': u_mac_post, 'thread_name': 'Mac Today'},
            ],
        )
        self.write_jsonl(
            self.ubuntu_home / 'session_index.jsonl',
            [
                {'id': u_shared, 'thread_name': 'Shared Cross-Device'},
                {'id': u_ubuntu_pre, 'thread_name': 'Ubuntu Pre-Boundary'},
            ],
        )

        shared_pre = codex_record(
            'shared-pre-resp', '2026-10-24T15:00:00Z',
            total=200_000, cached=0, inp=0, out=200_000,  # $2.00 in bucket 4 (15:00..15:15Z)
        )
        mac_pre = codex_record(
            'mac-pre-resp', '2026-10-24T15:30:00Z',
            total=500_000, cached=0, inp=500_000, out=0,  # $1.00 in bucket 6 (15:30..15:45Z)
        )
        ubuntu_pre = codex_record(
            'ubuntu-pre-resp', '2026-10-24T14:20:00Z',
            total=400_000, cached=0, inp=0, out=400_000,  # $4.00 in bucket 1 (14:15..14:30Z)
        )
        too_old = codex_record(
            'before-3h-cutoff', '2026-10-24T13:59:59Z',
            total=2_000_000, cached=0, inp=0, out=2_000_000,  # $20.00 before 14:00Z cutoff
        )
        mac_post = codex_record(
            'mac-post-resp', '2026-10-24T16:50:00Z',
            total=250_000, cached=0, inp=250_000, out=0,  # $0.50 in bucket 11 (16:45..17:00Z)
        )

        self.write_jsonl(self.mac_home / f'sessions/rollout-{u_shared}.jsonl', [shared_pre])
        self.write_jsonl(self.mac_home / f'sessions/rollout-{u_mac_pre}.jsonl', [mac_pre])
        self.write_jsonl(self.mac_home / f'sessions/rollout-{u_old}.jsonl', [too_old])
        self.write_jsonl(self.mac_home / f'sessions/rollout-{u_mac_post}.jsonl', [mac_post])

        self.write_jsonl(self.ubuntu_home / f'sessions/rollout-{u_shared}.jsonl', [shared_pre])
        self.write_jsonl(self.ubuntu_home / f'sessions/rollout-{u_ubuntu_pre}.jsonl', [ubuntu_pre])
        routing_control.write_run_state(
            self.ubuntu_home,
            self.ubuntu_home / 'ubuntu-gem-post',
            started_at='2026-10-24T16:40:00Z',
            interaction={
                'id': 'ubuntu-gem-post-1',
                'created': '2026-10-24T16:41:00Z',
                'usage': {
                    'total_tokens': 500_000,
                    'total_cached_tokens': 0,
                    'total_input_tokens': 0,
                    'total_output_tokens': 500_000,
                    'total_thought_tokens': 0,
                },
            },
            status='completed',
        )

        # 1. Standalone Mac (no manifest) crossing local midnight
        standalone_bridge = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        standalone_snap = standalone_bridge.snapshot(refresh=False, now=cross_now_epoch)
        self.assertEqual(standalone_snap['codex']['total_tokens'], 250_000)  # today only
        self.assertEqual([r['id'] for r in standalone_snap['chat_usage']['rows']], [k_mac_post])
        st_trend = standalone_snap['chat_usage']['trend']
        self.assertEqual(st_trend['bucket_seconds'], 900)
        self.assertEqual(len(st_trend['buckets']), 12)
        self.assertFalse(st_trend['partial'])
        self.assertEqual(
            [c['id'] for c in st_trend['top_chats']],
            [k_shared, k_mac_pre, k_mac_post],
        )
        self.assertAlmostEqual(sum(b['total_usd'] for b in st_trend['buckets']), 3.5)

        # 2. Multi-device Mac + Ubuntu schema-2 crossing local midnight with deduplicated shared session
        self.write_manifest()
        exporter_v2 = device_usage.DeviceExporter(
            self.ubuntu_home, 'ubuntu', 'Ubuntu', TZ_NAME, schema=2, history_days=7,
        )
        self.write_remote_snapshot(exporter_v2.snapshot(now=cross_now_epoch - 10))

        multi_bridge = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        snap_midnight = multi_bridge.snapshot(refresh=False, now=cross_now_epoch)

        # Totals and table stay strictly post-midnight (Mac $0.50 Codex, Ubuntu $5.00 Gemini)
        self.assertFalse(snap_midnight['totals']['partial'])
        self.assertEqual(snap_midnight['totals']['codex']['total_tokens'], 250_000)
        self.assertEqual(snap_midnight['totals']['gemini']['total_tokens'], 500_000)
        table_ids = {r['id'] for r in snap_midnight['chat_usage']['rows']}
        self.assertEqual(table_ids, {k_mac_post, 'unattributed'})

        # Rolling 3h chart includes pre-midnight Mac + Ubuntu Codex, deduplicates shared_pre, excludes Gemini
        trend = snap_midnight['chat_usage']['trend']
        self.assertEqual(trend['bucket_seconds'], 900)
        self.assertEqual(len(trend['buckets']), 12)
        self.assertFalse(trend['partial'])
        self.assertEqual(trend['buckets'][0]['start_at'], '2026-10-24T14:00:00Z')
        self.assertEqual(trend['buckets'][11]['end_at'], '2026-10-24T17:00:00Z')
        self.assertEqual(
            trend['top_chats'],
            [
                {'id': k_ubuntu_pre, 'title': 'Ubuntu Pre-Boundary', 'codex_usd': 4.0},
                {'id': k_shared, 'title': 'Shared Cross-Device', 'codex_usd': 2.0},
                {'id': k_mac_pre, 'title': 'Mac Yesterday', 'codex_usd': 1.0},
            ],
        )
        self.assertAlmostEqual(trend['buckets'][1]['total_usd'], 4.0)
        self.assertAlmostEqual(trend['buckets'][4]['total_usd'], 2.0)  # deduplicated ($2, not $4)
        self.assertAlmostEqual(trend['buckets'][6]['total_usd'], 1.0)
        self.assertAlmostEqual(trend['buckets'][11]['total_usd'], 0.5)  # k_mac_post in total, 4th place
        self.assertAlmostEqual(sum(b['total_usd'] for b in trend['buckets']), 7.5)

        # 3. Active weekly quota reset 30m before now (16:30:00Z): period shrinks to 30m, 3h chart unchanged
        reset_epoch = cross_now_epoch - 1800
        multi_bridge.reset_tracker.update(
            [
                {'at': reset_epoch - 60, 'bucket': 'codex', 'weekly': {'used': 75.0, 'reset': reset_epoch}},
                {'at': reset_epoch, 'bucket': 'codex', 'weekly': {'used': 0.0, 'reset': reset_epoch + 604800}},
            ],
            now=cross_now_epoch,
        )
        snap_reset = multi_bridge.snapshot(refresh=False, now=cross_now_epoch)
        self.assertTrue(snap_reset['reset_period']['available'])
        self.assertEqual(snap_reset['totals']['codex']['total_tokens'], 250_000)
        self.assertEqual(snap_reset['chat_usage']['trend']['top_chats'], trend['top_chats'])
        self.assertAlmostEqual(
            sum(b['total_usd'] for b in snap_reset['chat_usage']['trend']['buckets']),
            7.5,
        )

    def test_schema1_remote_never_fabricates_chart_points_and_marks_trend_partial(self):
        self.write_manifest()
        u_mac = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        self.write_jsonl(
            self.mac_home / f'sessions/rollout-{u_mac}.jsonl',
            [codex_record('mac-recent', '2026-10-24T07:30:00Z', total=500_000, cached=0, inp=500_000, out=0)],
        )
        self.write_jsonl(
            self.ubuntu_home / 'sessions/ubuntu.jsonl',
            [codex_record('ubuntu-s1-only', '2026-10-24T07:40:00Z', total=300_000, cached=0, inp=0, out=300_000)],
        )

        # Legacy schema-1 snapshot during same local day (period_start <= now - 3h)
        exporter_v1 = device_usage.DeviceExporter(self.ubuntu_home, 'ubuntu', 'Ubuntu', TZ_NAME, schema=1)
        self.write_remote_snapshot(exporter_v1.snapshot(now=NOW_EPOCH - 10))

        b = bridge.Bridge(self.mac_home, None, TZ, offline=True)
        snap = b.snapshot(refresh=False, now=NOW_EPOCH)
        self.assertFalse(snap['totals']['partial'])
        self.assertEqual(snap['totals']['codex']['total_tokens'], 800_000)

        trend = snap['chat_usage']['trend']
        self.assertEqual(trend['bucket_seconds'], 900)
        self.assertEqual(len(trend['buckets']), 12)
        self.assertTrue(trend['partial'])
        # Only Mac's timestamped $1.00 event appears in chart; Ubuntu's schema-1 $3.00 is never fabricated
        self.assertAlmostEqual(sum(b['total_usd'] for b in trend['buckets']), 1.0)

        # Crossing local midnight (01:00+08:00 = 17:00Z): schema-1 covers today's totals, not pre-midnight 3h chart
        cross_epoch = datetime.fromisoformat('2026-10-24T17:00:00+00:00').timestamp()
        self.write_jsonl(
            self.mac_home / f'sessions/rollout-{u_mac}.jsonl',
            [
                codex_record('mac-recent', '2026-10-24T07:30:00Z', total=500_000, cached=0, inp=500_000, out=0),
                codex_record('mac-pre-mid', '2026-10-24T15:00:00Z', total=500_000, cached=0, inp=500_000, out=0),
                codex_record('mac-post-mid', '2026-10-24T16:30:00Z', total=250_000, cached=0, inp=250_000, out=0),
            ],
        )
        self.write_remote_snapshot(exporter_v1.snapshot(now=cross_epoch - 5))
        snap_cross = b.snapshot(refresh=False, now=cross_epoch)
        self.assertFalse(snap_cross['totals']['partial'])
        self.assertEqual(snap_cross['totals']['codex']['total_tokens'], 250_000)
        self.assertTrue(snap_cross['chat_usage']['trend']['partial'])
        self.assertAlmostEqual(
            sum(bucket['total_usd'] for bucket in snap_cross['chat_usage']['trend']['buckets']),
            1.5,
        )


if __name__ == '__main__':
    unittest.main()
