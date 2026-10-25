import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'app', ROOT / 'routing' / 'scripts', ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from forecasting import build_forecast, select_display, window_forecast, QuotaHistory
from nextreset import parse_summary

NOW = 1800000000


def iso(at):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def limits(five=25, weekly=20):
    return {'fetched_at': iso(NOW), 'stale': False, 'bucket': 'codex',
            'five_hour': None if five is None else {'used_percent': five, 'resets_at': NOW + 18000},
            'weekly': None if weekly is None else {'used_percent': weekly, 'resets_at': NOW + 604800}}


def history(start_five=10, end_five=25, start_weekly=10, end_weekly=20):
    return [{'at': NOW-900+i*60, 'bucket': 'codex',
             'five_hour': {'used': start_five+(end_five-start_five)*i/15, 'reset': NOW+18000},
             'weekly': {'used': start_weekly+(end_weekly-start_weekly)*i/15, 'reset': NOW+604800}}
            for i in range(16)]


class ForecastTests(unittest.TestCase):
    def test_projection_uses_trailing_fifteen_minutes(self):
        result=build_forecast(history(),limits(),15000,NOW)
        self.assertEqual(result['five_hour']['status'],'projected')
        self.assertEqual(result['five_hour']['exhaustion_at'],NOW+75*60)
        self.assertEqual(result['tokens_per_minute'],1000)

    def test_more_than_95_percent_left_forces_weekly(self):
        first={'status':'projected','exhaustion_at':NOW+60}
        second={'status':'projected','exhaustion_at':NOW+120}
        self.assertEqual(select_display(limits(4,10),first,second)[0],'weekly')
        self.assertEqual(select_display(limits(5,10),first,second)[0],'five_hour')

    def test_otherwise_earliest_forecast_wins(self):
        first={'status':'projected','exhaustion_at':NOW+600}
        second={'status':'projected','exhaustion_at':NOW+120}
        self.assertEqual(select_display(limits(20,10),first,second)[0],'weekly')

    def test_unknown_forecast_is_explicitly_provisional(self):
        unknown={'status':'learning','exhaustion_at':None}
        selected,reason=select_display(limits(30,10),unknown,unknown)
        self.assertEqual(selected,'five_hour')
        self.assertIn('provisional',reason)

    def test_new_reset_never_uses_previous_window(self):
        rows=history()
        for row in rows:row['five_hour']['reset']=NOW-100
        self.assertEqual(window_forecast('five_hour',rows,limits(),NOW)['status'],'learning')

    def test_usage_drop_does_not_become_negative_or_infinite_eta(self):
        rows=history();rows[8]['five_hour']['used']=1
        self.assertEqual(window_forecast('five_hour',rows,limits(),NOW)['status'],'learning')

    def test_stale_quota_hides_estimate(self):
        current=limits();current['stale']=True
        self.assertEqual(window_forecast('five_hour',history(),current,NOW)['status'],'stale')

    def test_zero_measured_burn_is_not_an_invented_time(self):
        rows=history(25,25,20,20)
        result=window_forecast('five_hour',rows,limits(),NOW)
        self.assertEqual(result['status'],'idle');self.assertIsNone(result['exhaustion_at'])

    def test_reset_before_exhaustion_is_reported(self):
        current=limits();current['five_hour']['resets_at']=NOW+1000
        rows=history()
        for row in rows:row['five_hour']['reset']=NOW+1000
        self.assertEqual(window_forecast('five_hour',rows,current,NOW)['status'],'resets_first')

    def test_next_reset_gets_special_state_instead_of_later_exact_time(self):
        current=limits();current['five_hour']['resets_at']=NOW+100
        rows=history()
        for row in rows:row['five_hour']['reset']=NOW+100
        result=build_forecast(rows,current,1000,NOW)
        self.assertEqual(result['status'],'reset_before_projection')
        self.assertIsNone(result['exhaustion_at'])
        self.assertEqual(result['headline'],'Next reset comes first')
        self.assertIn('weekly quota remains',result['detail'])

    def test_before_next_reset_keeps_exact_forecast(self):
        result=build_forecast(history(),limits(),1000,NOW)
        self.assertEqual(result['status'],'projected')
        self.assertEqual(result['exhaustion_at'],NOW+4500)

    def test_all_reset_first_windows_use_same_special_state(self):
        current=limits();current['five_hour']['resets_at']=NOW+100;current['weekly']['resets_at']=NOW+200
        rows=history()
        for row in rows:
            row['five_hour']['reset']=NOW+100;row['weekly']['reset']=NOW+200
        result=build_forecast(rows,current,1000,NOW)
        self.assertEqual(result['status'],'reset_before_projection')
        self.assertIsNone(result['exhaustion_at'])

    def test_gaps_in_history_do_not_fabricate_a_recent_rate(self):
        rows=history();rows[0]['at']=NOW-1000;rows=rows[:1]+rows[10:]
        self.assertEqual(window_forecast('five_hour',rows,limits(),NOW)['status'],'learning')

    def test_reached_limit_and_missing_window(self):
        result=build_forecast([],limits(100,None),0,NOW)
        self.assertEqual(result['status'],'reached')
        self.assertEqual(result['weekly']['status'],'unavailable')

    def test_history_persists_only_recent_unique_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            def writer(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
            h=QuotaHistory(directory,writer);h.add(history()+history(),NOW)
            self.assertEqual(len(h.samples),16)
            self.assertEqual(len(QuotaHistory(directory,writer).samples),16)

    def test_small_rounding_jitter_is_not_a_reset(self):
        rows=history();rows[8]['five_hour']['used']=rows[7]['five_hour']['used']-1
        self.assertEqual(window_forecast('five_hour',rows,limits(),NOW)['status'],'projected')

    def test_missing_quota_is_not_reported_as_zero_burn(self):
        self.assertEqual(build_forecast([],limits(None,None),0,NOW)['status'],'unavailable')

    def test_nextreset_parser_preserves_source_quality_and_report(self):
        html='''<p class="tracking-delay">Source check delayed</p><div data-elapsed="2026-10-23T17:00:00Z"></div>
        <div id="next-reset" data-next-kind="completed"><p>Next reset</p><h2>No <em>time announced.</em></h2></div><h2>Ignore this heading</h2>'''
        result=parse_summary(html)
        self.assertEqual(result['headline'],'No time announced.')
        self.assertTrue(result['source_delayed'])
        self.assertEqual(result['latest_report_at'],'2026-10-23T17:00:00Z')

    def test_changed_nextreset_markup_is_not_treated_as_success(self):
        with self.assertRaises(ValueError):parse_summary('<h2>Unknown page</h2>')


if __name__=='__main__':unittest.main()
