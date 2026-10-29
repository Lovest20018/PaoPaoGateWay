#!/usr/bin/env python3
"""Install a verified local release. No implicit service start or config replacement."""
import argparse
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import uuid


def write_new(path, value, mode):
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'w') as f:
            f.write(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'), help='Staging root; default is the live filesystem')
    parser.add_argument('--enable-test-features', action='store_true', help='Reapply the owner-authorized private/authenticated test profile')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    target = args.root.resolve()
    live = target == Path('/')
    manifest = json.loads((package / 'manifest.json').read_text())
    for name, expected in manifest.items():
        item = (package / name).resolve()
        if not item.is_relative_to(package) or not item.is_file() or item.is_symlink():
            parser.exit(1, 'Invalid package path.\n')
        if hashlib.sha256(item.read_bytes()).hexdigest() != expected:
            parser.exit(1, 'Package checksum mismatch.\n')
    if live:
        if os.geteuid() != 0:
            parser.exit(1, 'Run as root.\n')
        if importlib.util.find_spec('yaml') is None:
            parser.exit(1, 'Install python3-yaml for PPsub configuration inspection.\n')
        release = Path('/etc/os-release').read_text()
        if 'ID=debian' not in release:
            parser.exit(1, 'This installer targets Debian.\n')
        for executable in ('nft', 'ip', 'python3', 'systemctl', 'pgrep', 'pidof', 'nsenter', 'openvpn', 'openssl'):
            if shutil.which(executable) is None:
                parser.exit(1, f'Missing runtime dependency: {executable}\n')
        # A running release is never overwritten beneath its processes.
        for service in ('paopaogw', 'paopaogw-api', 'paopaogw-subscription', 'paopaogw-observe',
                        'paopaogw-maintenance', 'paopaogw-core-action@check',
                        'paopaogw-core-action@update', 'paopaogw-core-action@rollback'):
            state = subprocess.run(['systemctl', 'is-active', '--quiet', service], check=False)
            if state.returncode == 0:
                parser.exit(1, 'Stop Gateway, API, collector and maintenance services before installing an update.\n')
        if subprocess.run(['getent', 'group', 'paopaogw-api'], capture_output=True).returncode:
            subprocess.run(['groupadd', '--system', 'paopaogw-api'], check=True)
        if subprocess.run(['id', '-u', 'paopaogw-api'], capture_output=True).returncode:
            subprocess.run(['useradd', '--system', '--gid', 'paopaogw-api', '--no-create-home',
                            '--shell', '/usr/sbin/nologin', 'paopaogw-api'], check=True)
    os.umask(0o027)
    config = target / 'etc/paopaogw'
    state = target / 'var/lib/paopaogw'
    code = target / 'usr/local/lib/paopaogw'
    for directory in (config, config / 'files', state, state / 'history', state / 'logs', state / 'clash', state / 'clash/clash-dashboard', code):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o750)
    code.chmod(0o755)
    for source in (package / 'lib').rglob('*'):
        if source.is_file():
            dest = code / source.relative_to(package / 'lib')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    unit_dir = target / 'etc/systemd/system'
    unit_dir.mkdir(parents=True, exist_ok=True)
    for source in (package / 'units').iterdir():
        if source.suffix not in {'.service', '.timer'}:
            continue
        shutil.copy2(source, unit_dir / source.name)
    # Copy dashboard only; generated runtime data survives every upgrade.
    shutil.copytree(package / 'ui', state / 'clash/clash-dashboard/ui', dirs_exist_ok=True)
    write_new(config / 'instance-id', str(uuid.uuid4()) + '\n', 0o640)
    write_new(config / 'read-token', secrets.token_hex(32) + '\n', 0o640)
    write_new(config / 'revision-key', secrets.token_hex(32) + '\n', 0o600)
    write_new(config / 'api.json', json.dumps({'listen': '127.0.0.1', 'port': 8788, 'probe_urls': []}, indent=2) + '\n', 0o640)
    write_new(config / 'service.env', 'PPGW_INTERFACE=\nPPGW_IPV6=no\n', 0o640)
    write_new(config / 'network', '# Debian: IPv6 disabled unless configured explicitly\n', 0o640)
    write_new(config / 'maintenance.json', json.dumps({'auto_update': True, 'history_enabled': True,
              'history_retention_days': 30, 'timezone': 'Asia/Shanghai', 'test_defaults': True}, indent=2) + '\n', 0o640)
    write_new(config / 'files/ppgw.ini', '#paopao-gateway\nmode=free\nfake_cidr=7.0.0.0/8\n'
              'dns_ip=223.5.5.5\ndns_port=53\nclash_web_password="' + secrets.token_urlsafe(24) + '"\n'
              'openport=no\nudp_enable=no\nnet_rec=no\n', 0o600)
    profile_marker = config / 'test-profile.initialized'
    if args.enable_test_features or not profile_marker.exists():
        spec = importlib.util.spec_from_file_location('test_profile', code / 'test_profile.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.enable(target)
        write_new(profile_marker, 'private-authenticated-v1\n', 0o600)
    write_new(state / 'hosts', '127.0.0.1 localhost\n::1 localhost\n', 0o640)
    if live:
        import grp
        group = grp.getgrnam('paopaogw-api').gr_gid
        for path in (config, state):
            os.chown(path, 0, group)
        os.chown(state / 'history', -1, group)
        (state / 'history').chmod(0o770)
        for name in ('maintenance.json',):
            os.chown(config / name, 0, group)
        for name in ('instance-id', 'read-token', 'api.json'):
            os.chown(config / name, 0, group)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('Installed; existing identity, credentials, configuration and state preserved.')
    print('Set PPGW_INTERFACE, configure ppgw.ini, then explicitly enable/start the documented services.')


if __name__ == '__main__':
    main()
