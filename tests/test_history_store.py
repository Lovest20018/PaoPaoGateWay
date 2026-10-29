from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import history_store as history


class HistoryTests(unittest.TestCase):
    def test_upserts_survive_restart_and_filter_without_conflating_boots(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'events.sqlite3'
            history.store('boot1', 'mihomo_snapshot', [{'id': '1', 'metadata': {'host': 'example.com', 'sourceIP': '192.0.2.1'}, 'download': 5}], path, now=100)
            history.store('boot1', 'mihomo_snapshot', [{'id': '1', 'metadata': {'host': 'example.com', 'sourceIP': '192.0.2.1'}, 'download': 10}], path, now=101)
            history.store('boot2', 'mihomo_snapshot', [{'id': '1', 'metadata': {'host': 'example.org'}}], path, now=102)
            result = history.query(domain='example.com', path=path)
            self.assertEqual(len(result['items']), 1)
            self.assertEqual(result['items'][0]['record']['download'], 10)
            self.assertEqual(result['items'][0]['first_seen_at'], 100)
            self.assertFalse(result['complete_history'])
            self.assertEqual(len(history.query(path=path)['items']), 2)
            history.store('boot3', 'sniffbox_lifecycle', [], path, retention_days=1, now=100000)
            self.assertEqual(history.query(path=path)['items'], [])
