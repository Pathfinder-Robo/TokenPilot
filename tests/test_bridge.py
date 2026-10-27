"""Unit tests for the local usage collector, quota normalization, launcher controls, and installer.
本地用量收集器、配额标准化、启动器控制与安装器的单元测试。
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import os
import unittest
from datetime import date
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'app', ROOT / 'routing' / 'scripts', ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import bridge
import install
import routing_control

TODAY = date(2026, 10, 24)
TZ = ZoneInfo('Asia/Shanghai')


def event(response, stamp, total=100, cached=70):
    return {'type': 'token_usage_record', 'timestamp': stamp, 'payload': {
        'response_id': response, 'usage': {'total_tokens': total, 'input_tokens': total-10,
                                          'output_tokens': 10, 'cached_input_tokens': cached}}}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home/'sessions').mkdir()
        (self.home/'argon').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, records):
        path.write_text(''.join(json.dumps(record)+'\n' for record in records))

    def test_codex_dedup_incremental_partial_and_midnight(self):
        path = self.home/'sessions/a.jsonl'
        self.write(path, [event('old','2026-10-23T15:59:59Z',50),
                          event('today','2026-10-23T16:00:00Z'),
                          event('today','2026-10-23T16:00:00Z'),
                          {'type':'event_msg','timestamp':'2026-10-24T00:00:00Z','payload':{
                              'type':'token_count','info':{'total_token_usage':{'total_tokens':9999}}}}])
        collector=bridge.CodexUsage(self.home)
        first=collector.collect(TODAY,TZ)
        self.assertEqual(first['total_tokens'],100)
        self.assertEqual(first['cached_tokens'],70)
        self.assertEqual(collector.collect(TODAY,TZ),first)
        second=json.dumps(event('second','2026-10-24T00:00:00Z',200))
        with path.open('a') as stream:stream.write(second[:30])
        self.assertEqual(collector.collect(TODAY,TZ)['total_tokens'],100)
        with path.open('a') as stream:stream.write(second[30:]+'\n')
        self.assertEqual(collector.collect(TODAY,TZ)['total_tokens'],300)
        archived=self.home/'archived_sessions';archived.mkdir()
        (archived/'a.jsonl').write_bytes(path.read_bytes())
        self.assertEqual(collector.collect(TODAY,TZ)['total_tokens'],300)

    def test_fallback_counts_deltas_not_cumulative_snapshots(self):
        records=[]
        for stamp,total in [('2026-10-23T15:00:00Z',100),('2026-10-24T00:00:00Z',150),('2026-10-24T00:01:00Z',150),('2026-10-24T00:02:00Z',180)]:
            records.append({'type':'event_msg','timestamp':stamp,'payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total}}}})
        self.write(self.home/'sessions/old.jsonl',records)
        self.assertEqual(bridge.CodexUsage(self.home).collect(TODAY,TZ)['total_tokens'],80)

    def test_file_rotation_and_missing_usage(self):
        collector=bridge.CodexUsage(self.home)
        self.assertFalse(collector.collect(TODAY,TZ)['available'])
        path=self.home/'sessions/a.jsonl'
        self.write(path,[event('one','2026-10-24T00:00:00Z',100)])
        self.assertEqual(collector.collect(TODAY,TZ)['total_tokens'],100)
        path.unlink()
        self.write(path,[event('two','2026-10-24T00:00:00Z',30)])
        self.assertEqual(collector.collect(TODAY,TZ)['total_tokens'],30)

    def test_gemini_running_and_completed_no_double_count(self):
        run=self.home/'run';run.mkdir()
        self.write(self.home/'argon/runs.jsonl',[{'result_dir':str(run),'started_at':'2026-10-24T00:00:00Z'}]*2)
        self.write(self.home/'argon/outcomes.jsonl',[{'result_dir':str(run),'date':'2026-10-24T00:00:00Z','usage':{'total_tokens':99999}}])
        interaction={'id':'one','created':'2026-10-24T00:00:00Z','usage':{'total_tokens':80,'total_input_tokens':60,'total_cached_tokens':10,'total_output_tokens':15,'total_thought_tokens':5}}
        (run/'interaction-01.json').write_text(json.dumps(interaction))
        (run/'result.json').write_text(json.dumps({'usage':{'total_tokens':99999}}))
        collector=bridge.GeminiUsage(self.home)
        result=collector.collect(TODAY,TZ)
        self.assertEqual(result['total_tokens'],80)
        self.assertEqual(result['output_tokens'],20)
        interaction['id']='two';interaction['usage']['total_tokens']=20
        (run/'interaction-02.json').write_text(json.dumps(interaction))
        self.assertEqual(collector.collect(TODAY,TZ)['total_tokens'],100)
        # A resumed copy of the same interaction remains one charge.
        run2=self.home/'run2';run2.mkdir()
        (run2/'interaction-01.json').write_text(json.dumps(interaction))
        with (self.home/'argon/runs.jsonl').open('a') as stream:stream.write(json.dumps({'result_dir':str(run2)})+'\n')
        self.assertEqual(collector.collect(TODAY,TZ)['total_tokens'],100)

    def test_policy_round_trip_boundaries_and_bad_values(self):
        for percent in [0,1,24,25,49,50,74,75,99,100]:
            bridge.set_routing(self.home,percent)
            self.assertEqual(routing_control.read_policy(self.home)['gemini_percent'],percent)
        self.assertEqual((self.home/'argon/routing.json').stat().st_mode & 0o777,0o600)
        for value in [True,-1,101,50.1,None,'50']:
            with self.assertRaises(ValueError):bridge.set_routing(self.home,value)

    def test_quota_bucket_and_duration_mapping(self):
        result={'rateLimits':{'primary':{'usedPercent':99,'windowDurationMins':300}},'rateLimitsByLimitId':{
            'other':{'primary':{'usedPercent':100,'windowDurationMins':300}},
            'codex':{'secondary':{'usedPercent':15,'windowDurationMins':300,'resetsAt':123},
                     'primary':{'usedPercent':2,'windowDurationMins':10080,'resetsAt':456}}}}
        actual=bridge.normalize_limits(result)
        self.assertEqual(actual['five_hour']['used_percent'],15)
        self.assertEqual(actual['weekly']['used_percent'],2)
        self.assertEqual(actual['bucket'],'codex')
        with self.assertRaises(ValueError):bridge.normalize_limits({'rateLimits':{'primary':{'usedPercent':4,'windowDurationMins':60}}})

    def test_failed_gemini_run_is_visible_without_inventing_usage(self):
        run = self.home/'failed'; run.mkdir()
        self.write(self.home/'argon/runs.jsonl', [{'result_dir': str(run), 'started_at': '2026-10-24T09:24:35Z'}])
        (run/'result.json').write_text(json.dumps({'status': 'error', 'usage': {}}))
        result = bridge.GeminiUsage(self.home).collect(TODAY, TZ)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['failed_runs_today'], 1)
        self.assertEqual(result['total_tokens'], 0)
        self.assertIn('failed', result['status_message'])

    def test_private_summaries_do_not_require_task_workspace_access(self):
        run = self.home/'unavailable-workspace-results'
        routing_control.write_run_state(self.home, run, started_at='2026-10-24T09:00:00Z')
        interaction = {'id': 'summary-one', 'created': '2026-10-24T09:01:00Z',
                       'usage': {'total_tokens': 123, 'total_input_tokens': 100},
                       'steps': [{'text': 'must never enter the numerical cache'}]}
        routing_control.write_run_state(self.home, run, interaction=interaction)
        routing_control.write_run_state(self.home, run, status='completed')
        result = bridge.GeminiUsage(self.home).collect(TODAY,TZ)
        self.assertEqual(result['total_tokens'],123)
        self.assertEqual(result['status'],'completed')
        self.assertFalse(run.exists())
        contents=next((self.home/'argon/usage-cache').glob('*.json')).read_text()
        self.assertNotIn('must never',contents)

    def _isolated_env(self, codex_home=None, user_home=None):
        codex_dir = Path(codex_home or self.home)
        home_dir = Path(user_home or self.home)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ('GEMINI_API_KEY', 'GEMINI_MODEL', 'CODEX_HOME')
        }
        env['CODEX_HOME'] = str(codex_dir)
        env['HOME'] = str(home_dir)
        return env

    def test_real_launcher_blocks_zero_without_api(self):
        bridge.set_routing(self.home,0)
        (self.home/'argon/authorization.json').write_text(json.dumps({'necessary_task_code_to_google':True}))
        launcher=ROOT/'routing/scripts/run_worker.py'
        result=subprocess.run([sys.executable,str(launcher),'--workspace',str(self.home),'--task-file','unused','--result-dir',str(self.home/'result')],capture_output=True,text=True,env=self._isolated_env())
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['status'],'routing_disabled')
        self.assertFalse((self.home/'result').exists())

    def test_isolated_install_files_permissions_and_launcher_controls(self):
        codex_home = self.home / 'isolated-codex'
        user_home = self.home / 'isolated-user'
        user_home.mkdir()
        synthetic_key = 'synthetic-gemini-key-for-unit-test'

        with self.assertRaises(ValueError):
            install.install(synthetic_key, authorize_code_export=False, codex_home=codex_home, user_home=user_home)
        with self.assertRaises(ValueError):
            install.install(None, authorize_code_export=True, codex_home=codex_home, user_home=user_home)

        installed = install.install(
            synthetic_key,
            authorize_code_export=True,
            codex_home=codex_home,
            user_home=user_home,
        )
        self.assertTrue(installed['installed'])
        self.assertEqual(installed['model_calls_during_install'], 0)
        self.assertEqual(installed['default_model'], 'gemini-4-argon')
        self.assertEqual(set(installed['files_sha256']), set(install.FILES))
        self.assertIn('scripts/routing_control.py', installed['files_sha256'])

        skill_dir = codex_home / 'skills/argon-routing'
        for rel in install.FILES:
            target_file = skill_dir / rel
            self.assertTrue(target_file.is_file(), rel)
            expected_mode = 0o755 if rel.endswith('.py') else 0o644
            self.assertEqual(target_file.stat().st_mode & 0o777, expected_mode)

        link = user_home / '.agents/skills/argon-routing'
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), skill_dir.resolve())

        state_dir = codex_home / 'argon'
        self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)
        for state_file in ('credentials.env', 'authorization.json', 'installation.json', 'outcomes.jsonl'):
            self.assertEqual((state_dir / state_file).stat().st_mode & 0o777, 0o600)

        auth_record = json.loads((state_dir / 'authorization.json').read_text())
        self.assertIs(auth_record.get('necessary_task_code_to_google'), True)

        agents_text = (codex_home / 'AGENTS.md').read_text()
        self.assertIn('<!-- BEGIN argon-routing -->', agents_text)
        self.assertIn('<!-- END argon-routing -->', agents_text)
        self.assertIn('scripts/routing_control.py', agents_text)
        self.assertIn('gemini_percent', agents_text)

        env = self._isolated_env(codex_home=codex_home, user_home=user_home)
        installed_launcher = skill_dir / 'scripts/run_worker.py'
        check_res = subprocess.run(
            [sys.executable, str(installed_launcher), '--check-config'],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(check_res.returncode, 0, check_res.stderr)
        check_payload = json.loads(check_res.stdout)
        self.assertTrue(check_payload['credential_available'])
        self.assertTrue(check_payload['code_export_authorized'])
        self.assertFalse(check_payload['api_called'])
        self.assertFalse(check_payload['environment_key_override'])
        self.assertEqual(check_payload['model'], 'gemini-4-argon')
        self.assertEqual(check_payload['routing']['gemini_percent'], 75)

        bridge.set_routing(codex_home, 0)
        blocked_res = subprocess.run(
            [
                sys.executable,
                str(installed_launcher),
                '--workspace',
                str(self.home),
                '--task-file',
                'unused',
                '--result-dir',
                str(self.home / 'installed-result'),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(blocked_res.returncode, 2)
        self.assertEqual(json.loads(blocked_res.stdout)['status'], 'routing_disabled')
        self.assertFalse((self.home / 'installed-result').exists())

        with self.assertRaises(RuntimeError):
            install.install(
                synthetic_key,
                authorize_code_export=True,
                replace=False,
                codex_home=codex_home,
                user_home=user_home,
            )
        replaced = install.install(
            None,
            authorize_code_export=True,
            replace=True,
            codex_home=codex_home,
            user_home=user_home,
        )
        self.assertTrue(replaced['installed'])
        self.assertTrue(Path(replaced['backup_dir']).is_dir())

    def test_live_bridge_commands_persist_and_reject_bad_input(self):
        proc=subprocess.Popen([sys.executable,str(Path(bridge.__file__)),'--serve','--offline','--codex-home',str(self.home),'--codex-binary','/usr/bin/false'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=self._isolated_env())
        try:
            self.assertEqual(json.loads(proc.stdout.readline())['routing']['gemini_percent'],75)
            proc.stdin.write(json.dumps({'command':'set_routing','gemini_percent':50})+'\n');proc.stdin.flush()
            self.assertEqual(json.loads(proc.stdout.readline())['routing']['gemini_percent'],50)
            proc.stdin.write(json.dumps({'command':'set_routing','gemini_percent':101})+'\n');proc.stdin.flush()
            self.assertIn('error',json.loads(proc.stdout.readline()))
            self.assertEqual(bridge.read_routing(self.home)['gemini_percent'],50)
        finally:
            proc.stdin.close();proc.wait(timeout=5);proc.stdout.close()
        self.assertEqual(proc.returncode,0)

    def test_run_registration_accepts_equals_and_separate(self):
        routing_control.register_run(self.home,['--result-dir',str(self.home/'r1')])
        routing_control.register_run(self.home,['--result-dir='+str(self.home/'r2')])
        rows=list(bridge.json_lines(self.home/'argon/runs.jsonl'))
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[1]['result_dir'].endswith('/r2'))


if __name__=='__main__':unittest.main()
