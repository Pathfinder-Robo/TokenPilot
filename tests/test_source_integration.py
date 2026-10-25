import json,sys,tempfile,time,unittest,urllib.error
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'app', ROOT / 'routing' / 'scripts', ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bridge import Bridge as Collector
from nextreset import URL,parse_forecast,summary_is_stale

class SourceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        self.fixture=json.loads(Path(__file__).with_name('forecast-fixture.json').read_text())
        self.now=datetime.fromisoformat(self.fixture['asOf'].replace('Z','+00:00')).timestamp()+1
        self.good=parse_forecast(self.fixture,now=self.now)
    def tearDown(self):self.temp.cleanup()
    def collector(self):return Collector(self.home,sys.executable,timezone.utc,offline=True)
    def test_previous_source_cache_is_discarded(self):
        p=self.home/'argon/viewer/nextreset-cache.json';p.parent.mkdir(parents=True)
        p.write_text(json.dumps(dict(self.good,url='https://nextreset.net/',headline='Old source headline')))
        c=self.collector();self.assertEqual(c.nextreset['url'],URL);self.assertNotEqual(c.nextreset['headline'],'Old source headline')
    def test_live_snapshot_expiry_and_metadata(self):
        c=self.collector();c.nextreset=dict(self.good)
        with patch('bridge.time.time',return_value=self.now):s=c.snapshot(refresh=False)['nextreset']
        self.assertFalse(s['stale']);self.assertEqual(s['url'],URL);self.assertEqual(s['announcement_state'],'announced');self.assertAlmostEqual(s['probability_24h'],self.fixture['windows'][0]['probability'])
        expiry=datetime.fromisoformat(s['expires_at'].replace('Z','+00:00')).timestamp()
        with patch('bridge.time.time',return_value=expiry+1):self.assertTrue(c.snapshot(refresh=False)['nextreset']['stale'])
    def test_fetch_failure_retains_new_source_cache_marked_stale(self):
        c=self.collector();c.nextreset=dict(self.good)
        with patch('bridge.fetch_summary',side_effect=urllib.error.URLError('test unavailable')):
            c.refresh_nextreset(force=True)
            deadline=time.monotonic()+1
            while c.nextreset_fetching and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(c.nextreset['url'],URL);self.assertEqual(c.nextreset['headline'],self.good['headline']);self.assertTrue(c.nextreset['stale']);self.assertTrue(c.nextreset['error'])
    def test_missing_asof_cannot_be_fresh(self):
        value=dict(self.good);value.pop('source_as_of');self.assertTrue(summary_is_stale(value,now=self.now))

if __name__=='__main__':unittest.main()
