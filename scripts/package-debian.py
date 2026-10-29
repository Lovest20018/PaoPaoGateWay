#!/usr/bin/env python3
"""Package a locally built Linux ELF core, scripts, dashboard and API contract."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tarfile
import tempfile
from prepare_dashboard import prepare_dashboard

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--arch', choices=['amd64', 'arm64'], required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if (not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', args.version)
            or not re.fullmatch(r'[0-9a-f]{40}', args.commit)
            or not re.fullmatch(r'[0-9a-f]{64}', args.source_sha256)):
        parser.error('invalid version or commit')
    header = args.binary.read_bytes()[:20]
    if header[:5] != b'\x7fELF\x02' or struct.unpack('<H', header[18:20])[0] != {'amd64': 62, 'arm64': 183}[args.arch]:
        parser.error('binary is not a matching 64-bit Linux ELF')
    args.output.mkdir(parents=True, exist_ok=True)
    name = f'paopaogw-{args.version}-debian-{args.arch}'
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / name
        executables = set()
        def copy(source, destination, executable=False):
            dest = stage / destination
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            dest.chmod(0o755 if executable else 0o644)
            if executable: executables.add(name + '/' + destination)
        copy(args.binary, 'lib/bin/sniffbox', True)
        for filename in ('runtime.sh', 'network.py', 'launch.py', 'test_profile.py'):
            copy(ROOT / 'deploy/debian' / filename, 'lib/' + filename, True)
        for filename in ('install.py', 'uninstall.sh'):
            copy(ROOT / 'deploy/debian' / filename, filename, True)
        for filename in ('ppg.sh', 'nft.sh'):
            copy(ROOT / 'FILES/usr/bin' / filename, 'lib/' + filename, True)
        copy(ROOT / 'integration/gateway_api.py', 'lib/gateway_api.py')
        copy(ROOT / 'integration/staged_probe.py', 'lib/staged_probe.py')
        copy(ROOT / 'integration/subscription_admin.py', 'lib/subscription_admin.py')
        copy(ROOT / 'integration/clash_ppsub.py', 'lib/clash_ppsub.py')
        copy(ROOT / 'integration/clash_policy.py', 'lib/clash_policy.py')
        copy(ROOT / 'integration/mihomo_update.py', 'lib/mihomo_update.py', True)
        copy(ROOT / 'integration/maintenance.py', 'lib/maintenance.py', True)
        copy(ROOT / 'integration/history_store.py', 'lib/history_store.py')
        copy(ROOT / 'integration/observe.py', 'lib/observe.py')
        copy(ROOT / 'FILES/etc/config/clash/base.yaml', 'lib/base.yaml')
        for source in (ROOT / 'deploy/debian').iterdir():
            if source.suffix in {'.service', '.timer'}:
                copy(source, 'units/' + source.name)
        prepare_dashboard(stage / 'ui')
        for filename in ('LICENSE',):
            copy(ROOT / filename, filename)
        for filename in ('debian-deployment.md', 'integration-api-v1.md', 'openapi-v1.json'):
            copy(ROOT / 'docs' / filename, 'docs/' + filename)
        (stage / 'lib/build.json').write_text(json.dumps({'version': args.version,
            'build_commit': args.commit, 'source_sha256': args.source_sha256,
            'architecture': args.arch}, indent=2) + '\n')
        manifest = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(stage.rglob('*')) if p.is_file()}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        archive = args.output / (name + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            def permissions(member):
                # Windows chmod does not preserve POSIX executable bits.
                member.mode = 0o755 if member.isdir() or member.name in executables else 0o644
                member.uid = member.gid = 0
                member.uname = member.gname = 'root'
                return member
            tar.add(stage, arcname=name, filter=permissions)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(f'{checksum}  {archive.name}\n')
    print(archive)


if __name__ == '__main__':
    main()
