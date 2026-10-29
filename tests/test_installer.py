from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def test_staging_update_preserves_identity_config_and_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            binary = work / 'sniffbox'
            # Staging only: not claimed as executable validation.
            binary.write_bytes(b'\x7fELF\x02' + bytes(13) + b'\x3e\x00' + bytes(12))
            subprocess.run([sys.executable, str(ROOT / 'scripts/package-debian.py'),
                '--binary', str(binary), '--version', 'test', '--commit', 'a' * 40,
                '--source-sha256', 'b' * 64, '--arch', 'amd64', '--output', str(work)], check=True, capture_output=True)
            with tarfile.open(work / 'paopaogw-test-debian-amd64.tar.gz') as archive:
                for name in ('lib/bin/sniffbox', 'lib/network.py', 'lib/ppg.sh', 'uninstall.sh', 'install.py'):
                    self.assertEqual(archive.getmember('paopaogw-test-debian-amd64/' + name).mode, 0o755)
                self.assertEqual(archive.getmember('paopaogw-test-debian-amd64/lib/base.yaml').mode, 0o644)
                destination = (work / 'package').resolve()
                for member in archive.getmembers():
                    output = (destination / member.name).resolve()
                    self.assertTrue(output.is_relative_to(destination))
                    if member.isdir():
                        output.mkdir(parents=True, exist_ok=True)
                    else:
                        self.assertTrue(member.isfile())
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_bytes(archive.extractfile(member).read())
                        output.chmod(member.mode & 0o777)
            package = work / 'package/paopaogw-test-debian-amd64'
            stage = work / 'root'
            args = [sys.executable, str(package / 'install.py'), '--root', str(stage)]
            subprocess.run(args, check=True, capture_output=True)
            identity = (stage / 'etc/paopaogw/instance-id').read_text()
            token = (stage / 'etc/paopaogw/read-token').read_text()
            config = stage / 'etc/paopaogw/files/ppgw.ini'
            defaults = config.read_text()
            for key in ('openport', 'udp_enable', 'net_rec', 'dns_burn'):
                self.assertIn(key + '="yes"', defaults)
            self.assertIn('proxy_cidr="127.0.0.0/8,10.0.0.0/8', defaults)
            self.assertIn('openport_auth="ppgw:', defaults)
            self.assertIn('PPGW_IPV6="yes"', (stage / 'etc/paopaogw/service.env').read_text())
            config.write_text(config.read_text() + 'net_cleanday=15\n')
            state = stage / 'var/lib/paopaogw/clash/retained.json'
            state.write_text('{"keep":true}')
            subprocess.run(args, check=True, capture_output=True)
            self.assertEqual((stage / 'etc/paopaogw/instance-id').read_text(), identity)
            self.assertEqual((stage / 'etc/paopaogw/read-token').read_text(), token)
            self.assertIn('net_cleanday=15', config.read_text())
            self.assertEqual(state.read_text(), '{"keep":true}')
            config.write_text(config.read_text() + 'box="route.block_unknown=true,route.block_quic=true"\n')
            (stage / 'etc/paopaogw/test-profile.initialized').unlink()
            subprocess.run(args, check=True, capture_output=True)
            self.assertIn('box="route.block_unknown=true,route.block_quic=false"', config.read_text())
            (package / 'lib/base.yaml').write_text('corrupt')
            self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
