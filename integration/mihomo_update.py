#!/usr/bin/env python3
"""Official MetaCubeX/mihomo releases, verified and installed transactionally."""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

API = 'https://api.github.com/repos/MetaCubeX/mihomo/releases/latest'
CODE = Path('/usr/local/lib/paopaogw')
STATE = Path('/var/lib/paopaogw/maintenance')
MAX_COMPRESSED = 64 * 1024 * 1024
MAX_BINARY = 128 * 1024 * 1024


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending = path.with_name(path.name + '.new')
    fd = os.open(pending, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


@contextmanager
def operation_lock(state=STATE):
    import fcntl
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'operation.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('MAINTENANCE_BUSY') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def official_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username
            and not parsed.password and (parsed.hostname in {'api.github.com', 'github.com'}
            or (parsed.hostname or '').endswith('.githubusercontent.com')))


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, target):
        if not official_url(target):
            raise ValueError('UNTRUSTED_RELEASE_REDIRECT')
        return super().redirect_request(request, fp, code, message, headers, target)


def fetch(url, limit):
    if not official_url(url):
        raise ValueError('UNTRUSTED_RELEASE_URL')
    opener = build_opener(ProxyHandler({}), OfficialRedirect())
    request = Request(url, headers={'User-Agent': 'PaoPaoGateway-Debian', 'Accept': 'application/vnd.github+json'})
    with opener.open(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('RELEASE_TOO_LARGE')
    return data


def release_asset(release, machine=None):
    machine = machine or platform.machine()
    if machine not in {'x86_64', 'AMD64', 'amd64'}:
        raise ValueError('UNSUPPORTED_ARCHITECTURE')
    version = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('INVALID_STABLE_RELEASE')
    name = f'mihomo-linux-amd64-compatible-{version}.gz'
    matches = [asset for asset in release.get('assets', []) if asset.get('name') == name]
    if len(matches) != 1:
        raise ValueError('RELEASE_ASSET_MISSING')
    asset = matches[0]
    url = asset.get('browser_download_url', '')
    expected = f'https://github.com/MetaCubeX/mihomo/releases/download/{version}/{name}'
    if url != expected or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or ''):
        raise ValueError('RELEASE_DIGEST_MISSING')
    if type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_COMPRESSED:
        raise ValueError('RELEASE_TOO_LARGE')
    return {'version': version, 'asset': name, 'url': url, 'sha256': asset['digest'][7:], 'size': asset['size']}


def version_of(binary):
    try:
        result = subprocess.run([str(binary), '-v'], capture_output=True, text=True, check=True, timeout=5)
        match = re.search(r'\bMihomo\b.*?\b(v[0-9]+\.[0-9]+\.[0-9]+)\b', result.stdout, re.I)
        return match.group(1) if match else None
    except (OSError, subprocess.SubprocessError):
        return None


def validate_elf(data):
    if (len(data) < 1024 * 1024 or data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack('<H', data[18:20])[0] != 62):
        raise ValueError('INVALID_MIHOMO_ELF')


class Updater:
    def __init__(self, code=CODE, state=STATE):
        self.code, self.state = code, state
        self.binary = code / 'bin/clash'
        self.previous = code / 'bin/clash.previous'

    def status(self):
        try:
            status = json.loads((self.state / 'status.json').read_bytes())
        except (OSError, ValueError):
            status = {}
        return {**status, 'installed_version': version_of(self.binary), 'rollback_available': self.previous.is_file(),
                'source': 'MetaCubeX/mihomo', 'channel': 'stable', 'architecture': 'amd64-compatible'}

    def check(self):
        with operation_lock(self.state):
            return self._check()

    def _check(self):
        release = release_asset(json.loads(fetch(API, 2 * 1024 * 1024)))
        current = self.status()
        status = {**current, 'latest_version': release['version'], 'checked_at': time.time(),
                  'update_available': current['installed_version'] != release['version'], 'latest_sha256': release['sha256'],
                  'last_check_result': 'success'}
        atomic_json(self.state / 'status.json', status)
        return release

    def validate_config(self, binary):
        result = subprocess.run(['systemctl', 'show', 'paopaogw', '--property=MainPID', '--value'],
                                capture_output=True, text=True, check=True, timeout=5)
        pid = result.stdout.strip()
        if not pid.isdigit() or int(pid) <= 0:
            return
        root = Path('/proc') / pid / 'root'
        if not (root / 'tmp/clash.yaml').is_file():
            return
        subprocess.run(['nsenter', '-t', pid, '-m', '--', str(binary), '-t',
                        '-d', '/etc/config/clash', '-f', '/tmp/clash.yaml'], check=True, timeout=40,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def restart_and_check(self, expected):
        from gateway_api import CoreClient
        client = CoreClient(Path('/var/lib/paopaogw/core-token'))
        active = subprocess.run(['systemctl', 'is-active', '--quiet', 'paopaogw']).returncode == 0
        if not active:
            return
        subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True, timeout=40,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                info = client.request('/sniffbox/info')
                if info.get('mode') not in {'yaml', 'suburl'} or info.get('clash_version') == expected:
                    return
            except Exception:
                pass
            time.sleep(.5)
        raise ValueError('MIHOMO_HEALTH_CHECK_FAILED')

    def update(self, force=False):
        with operation_lock(self.state):
            release = self._check()
            if version_of(self.binary) == release['version'] and not force:
                return {'status': 'unchanged', 'version': release['version']}
            compressed = fetch(release['url'], MAX_COMPRESSED)
            if len(compressed) != release['size'] or hashlib.sha256(compressed).hexdigest() != release['sha256']:
                raise ValueError('MIHOMO_CHECKSUM_MISMATCH')
            self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix='mihomo-', dir=self.state) as temporary:
                compressed_path = Path(temporary) / 'asset.gz'
                compressed_path.write_bytes(compressed)
                with gzip.open(compressed_path, 'rb') as stream:
                    data = stream.read(MAX_BINARY + 1)
                if len(data) > MAX_BINARY:
                    raise ValueError('MIHOMO_BINARY_TOO_LARGE')
                validate_elf(data)
                staged = Path(temporary) / 'clash'
                staged.write_bytes(data)
                staged.chmod(0o755)
                if version_of(staged) != release['version']:
                    raise ValueError('MIHOMO_VERSION_MISMATCH')
                self.validate_config(staged)
                old_version = version_of(self.binary)
                self.binary.parent.mkdir(parents=True, exist_ok=True)
                replacement = self.binary.with_name('clash.pending')
                shutil.copyfile(staged, replacement)
                replacement.chmod(0o755)
                if self.binary.exists():
                    backup = self.previous.with_name('clash.previous.pending')
                    shutil.copy2(self.binary, backup)
                    backup.replace(self.previous)
                replacement.replace(self.binary)
                try:
                    self.restart_and_check(release['version'])
                except Exception:
                    if self.previous.exists():
                        rollback = self.binary.with_name('clash.rollback')
                        shutil.copy2(self.previous, rollback)
                        rollback.replace(self.binary)
                        self.restart_and_check(old_version)
                    else:
                        self.binary.unlink(missing_ok=True)
                    raise ValueError('MIHOMO_UPDATE_ROLLED_BACK') from None
            status = {**self.status(), 'installed_version': release['version'], 'updated_at': time.time(),
                      'update_available': False, 'last_update_result': 'success', 'installed_asset_sha256': release['sha256']}
            atomic_json(self.state / 'status.json', status)
            return {'status': 'updated', 'version': release['version']}

    def rollback(self):
        with operation_lock(self.state):
            previous = version_of(self.previous)
            if not previous:
                raise ValueError('MIHOMO_ROLLBACK_UNAVAILABLE')
            self.validate_config(self.previous)
            with tempfile.TemporaryDirectory(dir=self.state) as temporary:
                current = Path(temporary) / 'current'
                shutil.copy2(self.binary, current)
                candidate = self.binary.with_name('clash.rollback')
                shutil.copy2(self.previous, candidate)
                candidate.replace(self.binary)
                try:
                    self.restart_and_check(previous)
                except Exception:
                    shutil.copy2(current, candidate)
                    candidate.replace(self.binary)
                    self.restart_and_check(version_of(self.binary))
                    raise ValueError('MIHOMO_ROLLBACK_FAILED') from None
                shutil.copy2(current, self.previous)
            atomic_json(self.state / 'status.json', {**self.status(), 'last_update_result': 'rolled_back', 'updated_at': time.time()})
            return {'status': 'rolled_back', 'version': previous}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'update', 'rollback', 'install'))
    args = parser.parse_args()
    updater = Updater()
    try:
        if args.action == 'check':
            updater.check()
            result = updater.status()
        elif args.action == 'rollback':
            result = updater.rollback()
        else:
            result = updater.update()
        print(json.dumps(result))
    except Exception:
        # No upstream exception bodies or configuration contents in logs.
        raise SystemExit('Official mihomo operation failed; installed core was preserved or restored.')


if __name__ == '__main__':
    main()
