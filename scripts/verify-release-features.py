#!/usr/bin/env python3
"""Run only inside the disposable release-validation container, with synthetic data."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

CODE = Path('/usr/local/lib/paopaogw')
sys.path.insert(0, str(CODE))
from gateway_api import CoreClient
from mihomo_update import Updater
from subscription_admin import update_config, Admin, atomic_write
from clash_ppsub import feature_options, apply_features, logical_document
from history_store import query as history_query
import yaml

ROOT = Path('/var/lib/paopaogw')
CONFIG = Path('/etc/paopaogw/files/ppgw.ini')
client = CoreClient(ROOT / 'core-token')
report = {'environment': 'disposable Debian release-validation, synthetic configuration only', 'checks': {}}


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def wait_info(mode=None):
    for _ in range(100):
        try:
            info = client.request('/sniffbox/info')
            if ((mode is None or info.get('mode') == mode) and
                    (info.get('mode') not in {'yaml', 'suburl'} or info.get('clash_version'))):
                return info
        except Exception:
            pass
        time.sleep(.5)
    raise AssertionError('Validation core not ready')


def restart(mode):
    subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True, timeout=45)
    return wait_info(mode)


def main():
    assert Path('/run/release-validation-only').is_file(), 'Refuse to run outside disposable fixture'
    initial = wait_info('free')
    assert all(initial['features'][key] for key in ('openport', 'udp_enable', 'net_rec', 'dns_burn'))
    assert initial['route']['block_quic'] is False
    assert 'PPGW_IPV6="yes"' in Path('/etc/paopaogw/service.env').read_text()
    assert 'ppgw_tproxy6' in run('nft', 'list', 'table', 'ip6', 'ppgw')
    assert subprocess.run(['pgrep', '-x', 'cloudflared'], capture_output=True).returncode == 1
    report['checks']['new_install_test_defaults_and_no_cloudflared'] = True

    # Fresh online install exercises official API, digest, gzip/ELF/version validation.
    updater = Updater()
    installed = updater.update(force=updater.binary.exists())
    report['official_mihomo'] = {**installed, 'source': updater.status()['source'],
        'sha256_verified': updater.status().get('installed_asset_sha256')}
    assert installed['status'] == 'updated'

    fixture = {'proxies': [{'name': 'LAB-NODE', 'type': 'socks5', 'server': '127.0.0.1', 'port': 9}],
        'proxy-groups': [{'name': 'PRE', 'type': 'select', 'proxies': ['DIRECT']},
                         {'name': 'MAIN', 'type': 'select', 'proxies': ['LAB-NODE', 'DIRECT']}],
        'rules': ['MATCH,DIRECT']}
    atomic_write(CONFIG.parent / 'custom.yaml', yaml.safe_dump(fixture).encode())
    atomic_write(CONFIG, update_config(CONFIG.read_bytes(), {'mode': 'yaml', 'yamlfile': 'custom.yaml'}))
    active = restart('yaml')
    assert active['integration']['mihomo_configuration']['status'] == 'loaded'
    report['checks']['fresh_mihomo_synthetic_yaml_loaded'] = True

    # Actual official binary replacement, namespace config preflight and service health.
    updater.update(force=True)
    old_hash = hashlib.sha256(updater.binary.read_bytes()).hexdigest()
    original_health = updater.restart_and_check
    calls = []
    def fail_once(expected):
        calls.append(expected)
        original_health(expected)
        if len(calls) == 1: raise ValueError('INJECTED_VALIDATION_HEALTH_FAILURE')
    updater.restart_and_check = fail_once
    try:
        updater.update(force=True)
        raise AssertionError('Expected rollback')
    except ValueError as error:
        assert str(error) == 'MIHOMO_UPDATE_ROLLED_BACK'
    updater.restart_and_check = original_health
    assert hashlib.sha256(updater.binary.read_bytes()).hexdigest() == old_hash
    wait_info('yaml')
    updater.rollback()
    report['checks']['official_download_atomic_replace_health_failure_restore_and_manual_rollback'] = len(calls) == 2

    # Materialized pre-proxy/SS/smart configuration is validated by real mihomo.
    settings = {'node-groups': [{'name': 'PRE'}, {'name': 'MAIN', 'smart': True,
                 'use_pre_proxy': True, 'pre_proxy_group': 'PRE'}],
        'global_monitor': {'enable': True, 'url': 'https://example.com/'},
        'restart_cron': {'enable': True, 'hour': 4},
        'shadowsocks_in': {'enable': True, 'cipher': 'aes-128-gcm', 'password': 'synthetic-fixture-secret'}}
    features = feature_options(settings)
    admin = Admin()
    snapshot = admin.current_config()
    runtime_doc = admin.snapshots[snapshot['snapshot_id']]['document']
    materialized = apply_features(runtime_doc, features)
    candidate = ROOT / 'admin/advanced-fixture.yaml'
    atomic_write(candidate, yaml.safe_dump(materialized).encode())
    pid = run('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value')
    subprocess.run(['nsenter', '-t', pid, '-m', '--', str(CODE / 'bin/clash'), '-t',
                    '-d', '/etc/config/clash', '-f', str(candidate)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
    assert logical_document(materialized) == runtime_doc
    policy = {'schema': 1, 'source_identity': __import__('clash_ppsub').source_identity(__import__('subscription_admin').config_values()),
              'sections': {}, 'reference_renames': {}, 'features': features}
    atomic_write(CONFIG.parent / 'clash-policy.json', json.dumps(policy).encode())
    restart('yaml')
    assert client.request('/sniffbox/subscription/config')['config']['restart_cron']['enable']
    assert json.loads((ROOT / 'clash/clash-dashboard/data/ppsub-policy.json').read_bytes())['global_monitor']['enable']
    report['checks']['advanced_preproxy_ss_smart_monitor_restart_real_mihomo_validation'] = True

    # Reset endpoint applies the upstream source again and retains a policy backup.
    admin = Admin(); admin.reset_policy({'base_revision': hashlib.sha256(CONFIG.read_bytes()).hexdigest()})
    for _ in range(120):
        if admin.operation.get('status') != 'applying': break
        time.sleep(.5)
    assert admin.operation['status'] == 'applied', admin.operation
    assert not (CONFIG.parent / 'clash-policy.json').exists()
    report['checks']['restore_upstream_policy_operation'] = True

    # Persist actual gateway lifecycle observations and verify across core/API restart.
    subprocess.run(['curl', '-sS', '--max-time', '15', '--socks5-hostname', '127.0.0.1:1079',
                    '-I', 'https://example.com/'], capture_output=True, timeout=20)
    for _ in range(12):
        rows = history_query(domain='example.com')['items']
        if rows: break
        time.sleep(1)
    assert rows, 'Collector must persist at least one actual lifecycle observation'
    run('systemctl', 'restart', 'paopaogw-api')
    restart('yaml')
    assert history_query(domain='example.com')['items']
    report['checks']['persistent_observations_survive_core_and_api_restart'] = True

    # HUP updates only safe fields, keeping process/boot identity.
    pid = run('pgrep', '-x', 'sniffbox'); before = wait_info('yaml')['integration']
    controller = run('systemctl', 'show', 'paopaogw', '--property=MainPID', '--value')
    private_ini = Path('/proc') / controller / 'root/tmp/ppgw.ini'
    previous = private_ini.read_bytes()
    private_ini.write_bytes(update_config(previous, {'box': 'route.block_quic=true'}))
    run('kill', '-HUP', pid)
    for _ in range(20):
        after = wait_info('yaml')
        if after['integration']['config_revision'] != before['config_revision']: break
        time.sleep(.2)
    assert after['route']['block_quic'] is True
    assert after['integration']['boot_id'] == before['boot_id']
    assert run('pgrep', '-x', 'sniffbox') == pid
    private_ini.write_bytes(update_config(previous, {'mode': 'free'}))
    run('kill', '-HUP', pid); time.sleep(.5)
    assert wait_info('yaml')['integration']['config_revision'] == after['integration']['config_revision']
    restart('yaml')
    report['checks']['safe_hot_reload_revision_and_restart_only_field_rejection'] = True
    report['checks']['controller_log_instance_source'] = (ROOT / 'logs/controller.log').is_file()
    (ROOT / 'release-features-validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
