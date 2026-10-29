from pathlib import Path
import hashlib
import gzip
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
import mihomo_update as update


class UpdaterTests(unittest.TestCase):
    def release(self, raw):
        return {'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False,
                'assets': [{'name': 'mihomo-linux-amd64-compatible-v1.2.3.gz',
                'browser_download_url': 'https://github.com/MetaCubeX/mihomo/releases/download/v1.2.3/mihomo-linux-amd64-compatible-v1.2.3.gz',
                'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size': len(raw)}]}

    def elf(self):
        return b'\x7fELF\x02\x01' + bytes(12) + b'\x3e\x00' + bytes(1024 * 1024)

    def test_only_official_stable_digest_matched_asset_is_selected(self):
        release = self.release(b'asset')
        self.assertEqual(update.release_asset(release, 'x86_64')['version'], 'v1.2.3')
        release['assets'][0]['browser_download_url'] = 'https://example.com/malicious'
        with self.assertRaises(ValueError):update.release_asset(release, 'x86_64')
        self.assertFalse(update.official_url('https://github.com.evil.example/a'))
        self.assertFalse(update.official_url('http://github.com/MetaCubeX/mihomo'))
        self.assertTrue(update.official_url('https://release-assets.githubusercontent.com/github-production-release-asset/a'))

    def test_hash_failure_does_not_change_installed_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binary = root / 'code/bin/clash';binary.parent.mkdir(parents=True);binary.write_bytes(b'old')
            updater = update.Updater(root / 'code', root / 'state')
            with patch.object(update, 'operation_lock'), patch.object(updater, '_check', return_value=update.release_asset(self.release(b'good'), 'x86_64')), \
                 patch.object(update, 'version_of', return_value='v0.0.1'), patch.object(update, 'fetch', return_value=b'bad'):
                with self.assertRaisesRegex(ValueError, 'CHECKSUM'):updater.update()
            self.assertEqual(binary.read_bytes(), b'old')

    def test_failed_health_restores_previous_core(self):
        compressed = gzip.compress(self.elf())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binary = root / 'code/bin/clash';binary.parent.mkdir(parents=True);binary.write_bytes(b'old')
            updater = update.Updater(root / 'code', root / 'state')
            def version(path):return 'v0.0.1' if Path(path).read_bytes() == b'old' else 'v1.2.3'
            with patch.object(update, 'operation_lock'), patch.object(updater, '_check', return_value=update.release_asset(self.release(compressed), 'x86_64')), \
                 patch.object(update, 'fetch', return_value=compressed), patch.object(update, 'version_of', side_effect=version), \
                 patch.object(updater, 'validate_config'), patch.object(updater, 'restart_and_check', side_effect=[ValueError('failed'), None]):
                with self.assertRaisesRegex(ValueError, 'ROLLED_BACK'):updater.update()
            self.assertEqual(binary.read_bytes(), b'old')
            self.assertEqual(updater.previous.read_bytes(), b'old')

    def test_prerelease_or_missing_digest_is_not_silently_trusted(self):
        release = self.release(b'asset');release['prerelease'] = True
        with self.assertRaises(ValueError):update.release_asset(release, 'x86_64')
        release['prerelease'] = False;release['assets'][0]['digest'] = None
        with self.assertRaises(ValueError):update.release_asset(release, 'x86_64')
