#!/usr/bin/env python3
"""Merge local policy sections after each Debian subscription refresh."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex
import hashlib
import hmac
import time
import yaml
from clash_ppsub import rename_references, source_identity, apply_features

POLICY = Path('/etc/paopaogw/files/clash-policy.json')
SECTIONS = ('proxy-groups', 'rule-providers', 'rules', 'proxy-providers')
STATE = Path('/var/lib/paopaogw/admin')
LAST_GOOD = STATE / 'clash.last-good.yaml'


def protected_revision(value, key):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


def write_status(value):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o750)
    pending = STATE / 'policy-status.new'
    pending.write_text(json.dumps(value, indent=2) + '\n')
    pending.chmod(0o640)
    pending.replace(STATE / 'policy-status.json')


def merge_policy(document, policy, identity):
    result = deepcopy(document)
    if policy.get('source_identity') != identity:
        return result
    if policy.get('schema') != 1 or set(policy.get('sections', {})) - set(SECTIONS):
        raise ValueError('INVALID_POLICY')
    for key, value in policy['sections'].items():
        result[key] = deepcopy(value)
    rename_references(result, policy.get('reference_renames', {}))
    return apply_features(result, policy.get('features') or {})


def build_policy(before, after, existing, identity, renames):
    policy = deepcopy(existing) if existing and existing.get('source_identity') == identity else {
        'schema': 1, 'source_identity': identity, 'sections': {}, 'reference_renames': {}}
    for key in SECTIONS:
        if before.get(key) != after.get(key):
            policy['sections'][key] = deepcopy(after.get(key, [] if key in {'rules', 'proxy-groups'} else {}))
    for key, value in list(policy['reference_renames'].items()):
        policy['reference_renames'][key] = renames.get(value, value)
    policy['reference_renames'].update(renames)
    return policy


def apply_candidate(target, identity, policy, key, validate):
    """Validate before replacing, including malformed automatic subscription updates."""
    pending = target.with_name(target.name + '.policy-new')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o750)
    old = {}
    try: old = json.loads((STATE / 'policy-status.json').read_bytes())
    except (OSError, ValueError): pass
    try:
        document = yaml.safe_load(target.read_bytes())
        if not isinstance(document, dict): raise ValueError('INVALID_CLASH_DOCUMENT')
        result = merge_policy(document, policy, identity) if policy else deepcopy(document)
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            yaml.safe_dump(result, stream, allow_unicode=True, sort_keys=False)
            stream.flush(); os.fsync(stream.fileno())
        validated = validate(pending)
    except (ValueError, TypeError, KeyError, yaml.YAMLError, subprocess.SubprocessError):
        validated = False
    if not validated:
        pending.unlink(missing_ok=True)
        if LAST_GOOD.is_file() and old.get('source_identity') == identity:
            target.write_bytes(LAST_GOOD.read_bytes()); target.chmod(0o600)
            write_status({**old, 'status': 'conflict', 'last_attempt_at': time.time(),
                          'message': 'Candidate policy failed validation; last successful configuration remains loaded.'})
            return False
        write_status({'status': 'invalid', 'source_identity': identity, 'last_attempt_at': time.time()})
        raise ValueError('INVALID_CANDIDATE_WITHOUT_BACKUP')
    pending.replace(target)
    last = LAST_GOOD.with_suffix('.pending')
    last.write_bytes(target.read_bytes()); last.chmod(0o600); last.replace(LAST_GOOD)
    write_status({'status': 'loaded', 'source_identity': identity, 'loaded_at': time.time(),
                  'source_revision': protected_revision(document, key),
                  'policy_revision': protected_revision(policy, key),
                  'effective_revision': protected_revision(result, key),
                  'revision_scope': 'mihomo_source_policy_effective',
                  'local_sections': list((policy or {}).get('sections', {})),
                  'policy_matches_source': policy is None or policy.get('source_identity') == identity})
    features = (policy or {}).get('features') if policy is not None and policy.get('source_identity') == identity else None
    metadata = STATE.parent / 'clash/clash-dashboard/data/ppsub-policy.json'
    metadata.parent.mkdir(parents=True, exist_ok=True)
    if features:
        metadata.write_text(json.dumps({**features, 'policy_features': True}) + '\n')
        metadata.chmod(0o600)
    else:
        metadata.unlink(missing_ok=True)
    return True


def main():
    target = Path(sys.argv[1])
    export = subprocess.run(['/usr/local/lib/paopaogw/bin/sniffbox', 'ppgw', '-ini-export', '/tmp/ppgw.ini'],
                            capture_output=True, text=True, check=True, timeout=5)
    values = dict(item.split('=', 1) for item in shlex.split(export.stdout) if '=' in item)
    policy = json.loads(POLICY.read_bytes()) if POLICY.is_file() else None
    key = os.environ.get('PPGW_REVISION_KEY', '').encode()
    if not key: key = Path('/etc/paopaogw/revision-key').read_bytes().strip()
    def validate(pending):
        return subprocess.run(['/usr/bin/clash', '-t', '-d', '/etc/config/clash', '-f', str(pending)],
                timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    apply_candidate(target, source_identity(values), policy, key, validate)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # YAML can contain node credentials; never log it or an exception body.
        sys.exit('Local Clash policy could not be loaded.')
