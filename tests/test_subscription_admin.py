import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import subscription_admin as admin


class SubscriptionTests(unittest.TestCase):
    def test_native_panel_bearer_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / 'core-token'
            digest = hashlib.sha256(b'panel-password').hexdigest()
            token.write_text(digest)
            manager = admin.Admin()
            with patch.object(admin, 'CORE_TOKEN', token):
                self.assertTrue(manager.authenticated('Bearer ' + digest))
                self.assertFalse(manager.authenticated(None))
                self.assertFalse(manager.authenticated('Bearer panel-password'))
                self.assertFalse(manager.authenticated('Bearer observer-token'))
                self.assertFalse(manager.authenticated('Bearer 非法字符'))

    def test_clash_current_config_loads_actual_policy_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'ppgw.ini'
            original = b'#paopao-gateway\nmode=suburl\n'
            config.write_bytes(original)
            exported = "mode='suburl'\nsuburl='https://example.com/sub?token=secret&x=1'\n"
            document = {'proxy-groups': [{'name': 'G1', 'type': 'select', 'proxies': ['N1', 'DIRECT']}],
                        'rule-providers': {'rules1': {'type': 'http', 'url': 'https://example.com/rules', 'format': 'mrs'}},
                        'rules': ['RULE-SET,rules1,G1', 'MATCH,DIRECT']}
            with patch.object(admin, 'CONFIG', config), patch.object(admin, 'FILES', root), \
                 patch.object(admin, 'running_document', return_value=document), \
                 patch('subscription_admin.subprocess.run') as run:
                run.return_value.stdout = exported
                result = admin.Admin().current_config()
            self.assertEqual(result['config']['subs'][0]['url'], 'https://example.com/sub?token=secret&x=1')
            self.assertEqual(result['source'], 'clash_runtime')
            self.assertEqual(result['config']['node-groups'][0]['name'], 'G1')
            self.assertEqual(result['config']['node-groups'][0]['include'], ['N1', 'DIRECT'])
            self.assertEqual(result['config']['rules'][-1]['fixrule'], document['rules'])
            self.assertEqual(result['config']['rules'][0]['format'], 'mrs')
            self.assertEqual(result['counts'], {'node_groups': 1, 'rules': 2, 'rule_providers': 1})
            self.assertTrue(result['write_allowed'])
            self.assertEqual(result['edit_kind'], 'clash_patch')
            self.assertGreaterEqual(len(result['snapshot_id']), 24)
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(run.call_count, 1)
            self.assertIsInstance(run.call_args.args[0], list)

    def test_full_clash_cannot_be_overwritten_by_ppsub_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(admin, 'FILES', root), \
                 patch.object(admin, 'config_values', return_value={'mode': 'suburl', 'suburl': 'https://example.com/sub'}):
                payload = {'kind': 'ppsub', 'value': json.dumps({'exported_at': 'snapshot', 'subs': [{'name': 'S', 'url': 'https://example.com/sub'}]})}
                with self.assertRaisesRegex(ValueError, 'CLASH_PPSUB_NOT_EQUIVALENT'):
                    admin.Admin().apply(payload)

    def test_update_preserves_password_and_business_config(self):
        raw = b'#paopao-gateway\nmode=free\nclash_web_password="private-secret"\nfake_cidr=7.0.0.0/8\n'
        updated = admin.update_config(raw, {'mode':'suburl', 'suburl':'https://example.com/sub?token=abc'})
        self.assertIn(b'clash_web_password="private-secret"', updated)
        self.assertIn(b'fake_cidr=7.0.0.0/8', updated)
        self.assertEqual(updated.count(b'mode='), 1)
        self.assertIn(b'mode="suburl"', updated)

    def test_payload_only_accepts_supported_inputs(self):
        changes, upload = admin.validate_subscription({'kind':'url', 'value':'https://example.com/sub?token=x'})
        self.assertEqual(changes['mode'], 'suburl')
        self.assertIsNone(upload)
        changes, upload = admin.validate_subscription({'kind':'yaml', 'value':'proxies:\n  - name: x\n'})
        self.assertEqual(upload[0], 'custom.yaml')
        for value in ('file:///etc/passwd', 'https://user:secret@example.com/', 'https://example.com/\nx=1', 'https://example.com/"'):
            with self.assertRaises(ValueError):
                admin.validate_subscription({'kind':'url', 'value':value})
        with self.assertRaises(ValueError):
            admin.validate_subscription({'kind':'yaml', 'value':'no nodes'})
        with self.assertRaises(ValueError):
            admin.validate_subscription({'kind':'url', 'value':'https://example.com/', 'path':'/etc/hosts'})

    def test_ppsub_file_requires_actual_export_shape(self):
        with self.assertRaises(ValueError):
            admin.validate_subscription({'kind':'ppsub', 'value':'{}'})
        _, upload = admin.validate_subscription({'kind':'ppsub', 'value':json.dumps({'exported_at':'test','subs':[{'name':'x','url':'https://example.com'}]})})
        self.assertEqual(upload[0], 'ppsub.json')

    def test_failed_apply_restores_all_prior_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = root / 'files'
            files.mkdir()
            config = files / 'ppgw.ini'
            original = b'#paopao-gateway\nmode=free\n'
            config.write_bytes(original)
            (files / 'ppsub.json').write_bytes(b'{"private":"old"}')
            candidate = admin.update_config(original, {'mode':'yaml'})
            manager = admin.Admin()
            manager.operation = {'id':'test','status':'applying'}
            with patch.object(admin,'CONFIG',config), patch.object(admin,'FILES',files), patch.object(admin,'STATE',root/'state'), patch('subscription_admin.subprocess.run',side_effect=RuntimeError()):
                manager.worker(original,candidate,('custom.yaml',b'proxies: []'))
            self.assertEqual(config.read_bytes(),original)
            self.assertEqual((files/'ppsub.json').read_bytes(),b'{"private":"old"}')
            self.assertFalse((files/'custom.yaml').exists())
            self.assertEqual(manager.operation['status'],'failed')


if __name__=='__main__':
    unittest.main()
