from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from copy import deepcopy
from clash_ppsub import editor_config, merge_editor, source_identity
from clash_policy import build_policy, merge_policy


class ClashPpsubTests(unittest.TestCase):
    def document(self):
        return {'proxies': [{'name': 'N1', 'type': 'socks5', 'server': '127.0.0.1', 'port': 9,
                             'password': 'private-node', 'dialer-proxy': 'Main'}],
                'secret': 'private-core', 'dns': {'nameserver': ['223.5.5.5']},
                'proxy-groups': [{'name': 'Main', 'type': 'select', 'proxies': ['Backup', 'N1', 'DIRECT'],
                                  'hidden': True, 'icon': 'https://example.com/icon.svg'},
                                 {'name': 'Backup', 'type': 'fallback', 'proxies': ['N1', 'N1'],
                                  'url': 'https://example.com/test', 'interval': 121, 'lazy': False, 'tolerance': 37}],
                'rule-providers': {'China': {'type': 'http', 'url': 'https://example.com/china',
                                  'path': './rules/private.mrs', 'format': 'mrs', 'behavior': 'domain',
                                  'header': {'User-Agent': ['private-agent']}, 'proxy': 'Main'}},
                'rules': ['RULE-SET,China,DIRECT', 'PROCESS-NAME,example.exe,Main', 'MATCH,Main']}

    def editing(self):
        document = self.document()
        baseline, _ = editor_config(document, 'https://example.com/sub')
        submitted = deepcopy(baseline)
        submitted.update(global_monitor={'enable': False, 'url': 'https://www.youtube.com/generate_204'},
                         restart_cron={'enable': False, 'hour': 4}, shadowsocks_in={'enable': False})
        for row in submitted['node-groups']:
            row.update(use_pre_proxy=False, pre_proxy_group='')
            if row.get('failover'):
                row['retries'] = 3
        for row in submitted['rules']:
            if row.get('type') == 'rule-set':
                row.setdefault('interval', 86400)
        return document, baseline, submitted

    def test_editor_roundtrip_does_not_change_hidden_fields_or_duplicates(self):
        document, baseline, submitted = self.editing()
        result, url, names = merge_editor(document, baseline, submitted)
        self.assertEqual(result, document)
        self.assertIsNone(url)
        self.assertEqual(names, {})
        self.assertEqual(result['proxy-groups'][1]['proxies'], ['N1', 'N1'])

    def test_rename_group_preserves_hidden_fields_and_fixes_references(self):
        document, baseline, submitted = self.editing()
        submitted['node-groups'][0]['name'] = 'Renamed'
        result, _, names = merge_editor(document, baseline, submitted)
        self.assertEqual(result['proxy-groups'][0]['icon'], document['proxy-groups'][0]['icon'])
        self.assertEqual(result['rules'][-1], 'MATCH,Renamed')
        self.assertEqual(result['rules'][1], 'PROCESS-NAME,example.exe,Renamed')
        self.assertEqual(result['rule-providers']['China']['proxy'], 'Renamed')
        self.assertEqual(result['proxies'][0]['dialer-proxy'], 'Renamed')
        self.assertEqual(result['proxies'][0]['password'], 'private-node')
        self.assertEqual(result['dns'], document['dns'])
        self.assertEqual(names, {'Main': 'Renamed'})
        self.assertEqual(document['proxy-groups'][0]['name'], 'Main')

    def test_rule_and_provider_edits_preserve_cache_path_and_headers(self):
        document, baseline, submitted = self.editing()
        submitted['rules'][0].update(name='NewChina', interval=7200)
        submitted['rules'][-1]['fixrule'].insert(0, 'DOMAIN,example.com,DIRECT')
        result, _, _ = merge_editor(document, baseline, submitted)
        provider = result['rule-providers']['NewChina']
        self.assertEqual(provider['interval'], 7200)
        self.assertEqual(provider['header'], document['rule-providers']['China']['header'])
        self.assertEqual(provider['path'], './rules/private.mrs')
        self.assertEqual(result['rules'][:2], ['DOMAIN,example.com,DIRECT', 'RULE-SET,NewChina,DIRECT'])
        self.assertEqual(result['secret'], 'private-core')

    def test_membership_edits_do_not_change_fallback_type_or_interval(self):
        document, baseline, submitted = self.editing()
        submitted['node-groups'][1]['include'] = ['DIRECT', 'N1']
        result, _, _ = merge_editor(document, baseline, submitted)
        group = result['proxy-groups'][1]
        self.assertEqual(group['type'], 'fallback')
        self.assertEqual(group['interval'], 121)
        self.assertFalse(group['lazy'])
        self.assertEqual(group['proxies'], ['DIRECT', 'N1'])

    def test_unknown_or_duplicate_id_cannot_alias_original_objects(self):
        document, baseline, submitted = self.editing()
        submitted['node-groups'][1]['_clash_id'] = submitted['node-groups'][0]['_clash_id']
        with self.assertRaisesRegex(ValueError, 'INVALID_EDITOR_ID'):
            merge_editor(document, baseline, submitted)

    def test_new_groups_and_rule_blocks_can_be_added_and_reordered(self):
        document, baseline, submitted = self.editing()
        submitted['node-groups'].insert(0, {'name': 'New', 'include': ['Main', 'DIRECT'], 'subs': []})
        submitted['rules'].insert(0, {'fixrule': ['DOMAIN,example.org,New']})
        result, _, _ = merge_editor(document, baseline, submitted)
        self.assertEqual(result['proxy-groups'][0]['type'], 'select')
        self.assertEqual(result['proxy-groups'][0]['proxies'], ['Main', 'DIRECT'])
        self.assertEqual(result['rules'][0], 'DOMAIN,example.org,New')

    def test_policy_keeps_updating_nodes_and_non_policy_fields(self):
        document, baseline, submitted = self.editing()
        submitted['rules'][-1]['fixrule'].insert(0, 'DOMAIN,example.com,DIRECT')
        result, _, renames = merge_editor(document, baseline, submitted)
        identity = source_identity({'mode': 'suburl', 'suburl': 'https://example.com/sub'})
        policy = build_policy(document, result, None, identity, renames)
        updated = deepcopy(document)
        updated['proxies'][0]['password'] = 'new-upstream-password'
        updated['dns']['nameserver'] = ['1.1.1.1']
        applied = merge_policy(updated, policy, identity)
        self.assertEqual(applied['proxies'][0]['password'], 'new-upstream-password')
        self.assertEqual(applied['dns'], updated['dns'])
        self.assertEqual(applied['rules'], result['rules'])
        self.assertEqual(merge_policy(updated, policy, 'another-subscription'), updated)
        self.assertEqual(list(policy['sections']), ['rules'])

    def test_groups_members_rule_order_and_provider_parameters_are_preserved(self):
        document = {
            'proxies': [{'name': 'N1', 'password': 'never-expose-node-password'}],
            'secret': 'never-expose-controller-secret',
            'proxy-groups': [
                {'name': 'Main', 'type': 'select', 'proxies': ['Backup', 'N1', 'N1', 'DIRECT']},
                {'name': 'Backup', 'type': 'fallback', 'proxies': ['N1'], 'url': 'https://example.com/check', 'expected-status': 204},
                {'name': 'Speed', 'type': 'url-test', 'proxies': ['N1'], 'url': 'https://example.com/test', 'interval': 120},
            ],
            'rule-providers': {'China': {'type': 'http', 'url': 'https://example.com/china.mrs',
                                        'format': 'mrs', 'behavior': 'domain', 'interval': 7200, 'proxy': 'Main'}},
            'rules': ['RULE-SET,China,DIRECT', 'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve', 'MATCH,Main'],
        }
        config, counts = editor_config(document, 'https://example.com/sub')
        self.assertEqual([row['name'] for row in config['node-groups']], ['Main', 'Backup', 'Speed'])
        self.assertEqual(config['node-groups'][0]['include'], ['Backup', 'N1', 'DIRECT'])
        self.assertTrue(config['node-groups'][1]['failover'])
        self.assertEqual(config['node-groups'][2]['speedtest_url'], 'https://example.com/test')
        self.assertEqual(config['rules'][-1]['fixrule'], document['rules'])
        self.assertEqual(config['rules'][0]['proxy'], 'Main')
        self.assertEqual(config['rules'][0]['format'], 'mrs')
        self.assertEqual(counts, {'node_groups': 3, 'rules': 3, 'rule_providers': 1})
        self.assertNotIn('never-expose', str(config))
        self.assertEqual(document['proxy-groups'][0]['proxies'], ['Backup', 'N1', 'N1', 'DIRECT'])

    def test_does_not_invent_default_groups_or_rules(self):
        config, counts = editor_config({'proxy-groups': [], 'rules': []})
        self.assertEqual(config['node-groups'], [])
        self.assertEqual(config['rules'], [])
        self.assertEqual(counts['rules'], 0)

    def test_structured_invalid_rules_are_rejected(self):
        with self.assertRaises(ValueError):
            editor_config({'rules': [{'invalid': True}]})
