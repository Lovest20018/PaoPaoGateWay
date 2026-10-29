from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import yaml
import clash_policy as policy
from clash_ppsub import feature_options, apply_features, logical_document, editor_config, merge_editor


class RefreshTests(unittest.TestCase):
    def test_automatic_invalid_update_keeps_last_success_only_for_same_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root / 'clash.yaml'; state = root / 'admin'
            with patch.object(policy, 'STATE', state), patch.object(policy, 'LAST_GOOD', state / 'last-good.yaml'):
                original = {'proxies': [], 'proxy-groups': [], 'rules': ['MATCH,DIRECT']}
                target.write_text(yaml.safe_dump(original))
                self.assertTrue(policy.apply_candidate(target, 'source1', None, b'test-key', lambda p: True))
                successful = target.read_bytes()
                before = json.loads((state / 'policy-status.json').read_bytes())
                target.write_text('rules: [malformed')
                self.assertFalse(policy.apply_candidate(target, 'source1', None, b'test-key', lambda p: False))
                self.assertEqual(target.read_bytes(), successful)
                conflict = json.loads((state / 'policy-status.json').read_bytes())
                self.assertEqual(conflict['effective_revision'], before['effective_revision'])
                self.assertEqual(conflict['status'], 'conflict')
                target.write_text('rules: [malformed')
                with self.assertRaisesRegex(ValueError, 'WITHOUT_BACKUP'):
                    policy.apply_candidate(target, 'source2', None, b'test-key', lambda p: False)
                self.assertNotEqual(target.read_bytes(), successful)

    def test_preproxy_uses_provider_override_and_can_be_removed_without_cloning_loops(self):
        original = {'proxies': [{'name': 'N', 'type': 'socks5', 'server': '127.0.0.1', 'port': 9}],
                    'proxy-providers': {'remote': {'type': 'http', 'url': 'https://example.com/nodes', 'path': './remote.yaml'}},
                    'proxy-groups': [{'name': 'PRE', 'type': 'select', 'proxies': ['DIRECT']},
                                     {'name': 'Main', 'type': 'select', 'proxies': ['N'], 'use': ['remote']}],
                    'rules': ['MATCH,Main']}
        config, _ = editor_config(original)
        config['node-groups'][1].update(use_pre_proxy=True, pre_proxy_group='PRE')
        features = feature_options(config)
        result = apply_features(original, features)
        chained = result['proxy-groups'][1]
        self.assertNotIn('dialer-proxy', chained)
        for name in chained['use']:
            self.assertEqual(result['proxy-providers'][name]['override']['dialer-proxy'], 'PRE')
        inline = [result['proxy-providers'][name] for name in chained['use'] if name.endswith('inline')][0]
        self.assertEqual(inline['payload'][0]['name'], 'N')
        self.assertEqual(logical_document(result), original)
        self.assertEqual(apply_features(result, features), result)
        self.assertEqual(apply_features(result, {}), original)
        updated = deepcopy(original); updated['proxies'][0]['port'] = 1234
        fresh = apply_features(updated, features)
        self.assertEqual(fresh['proxy-providers'][chained['use'][-1]]['payload'][0]['port'], 1234)

    def test_smart_ss_and_restart_options_are_materialized_and_validate_input(self):
        original = {'proxies': [], 'proxy-groups': [{'name': 'Main', 'type': 'select', 'proxies': ['DIRECT']}], 'rules': ['MATCH,Main']}
        baseline, _ = editor_config(original); edited = deepcopy(baseline)
        edited['node-groups'][0]['smart'] = True
        edited.update(restart_cron={'enable': True, 'hour': 4}, shadowsocks_in={
            'enable': True, 'cipher': 'aes-128-gcm', 'password': 'synthetic-test-password'})
        merged, _, _ = merge_editor(original, baseline, edited)
        result = apply_features(merged, feature_options(edited))
        self.assertIn('smartspeedtest@hide', result['proxy-groups'][0]['proxies'])
        self.assertEqual(result['listeners'][0]['listen'], '127.0.0.1')
        self.assertTrue(feature_options(edited)['restart_cron']['enable'])
        edited['restart_cron']['hour'] = 24
        with self.assertRaises(ValueError): feature_options(edited)
