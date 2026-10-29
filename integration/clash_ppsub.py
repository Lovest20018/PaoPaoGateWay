"""Project a loaded Clash document into PPsub for inspection, without inventing policy."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import re
import subprocess

MAX_YAML_BYTES = 2 * 1024 * 1024


def running_document():
    result = subprocess.run(['systemctl', 'show', 'paopaogw.service', '--property=MainPID', '--value'],
                            capture_output=True, text=True, check=True, timeout=5)
    pid = result.stdout.strip()
    if not pid.isascii() or not pid.isdigit() or int(pid) <= 0:
        raise ValueError('CORE_NOT_RUNNING')
    # MainPID is the controller in the core's private mount namespace.
    path = Path('/proc') / pid / 'root/tmp/clash.yaml'
    with path.open('rb') as stream:
        raw = stream.read(MAX_YAML_BYTES + 1)
    if len(raw) > MAX_YAML_BYTES:
        raise ValueError('CONFIG_TOO_LARGE')
    import yaml
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError('INVALID_CLASH_DOCUMENT')
    return logical_document(document)


def logical_document(document):
    """Undo only our materialized helpers before taking another editor snapshot."""
    result = deepcopy(document)
    managed = result.pop('x-ppgw-features', None)
    if not isinstance(managed, dict) or managed.get('schema') != 1:
        return result
    originals = managed.get('groups', {})
    result['proxy-groups'] = [deepcopy(originals.get(row.get('name'), row))
                              for row in result.get('proxy-groups', [])]
    for name in managed.get('providers', []):
        result.get('proxy-providers', {}).pop(name, None)
    if managed.get('smart_proxy'):
        result['proxies'] = [row for row in result.get('proxies', []) if row.get('name') != 'smartspeedtest@hide']
    if managed.get('ss_listener'):
        result['listeners'] = [row for row in result.get('listeners', []) if row.get('name') != 'ppgw-shadowsocks-in']
    # Preserve the absence of optional sections for a stable no-op fingerprint.
    for name in managed.get('absent_sections', []):
        if not result.get(name): result.pop(name, None)
    return result


def editor_config(document, subscription_url=''):
    groups = document.get('proxy-groups', [])
    rules = document.get('rules', [])
    providers = document.get('rule-providers', {})
    if (not isinstance(groups, list) or not isinstance(rules, list)
            or not isinstance(providers, dict) or not all(isinstance(row, str) for row in rules)):
        raise ValueError('INVALID_CLASH_POLICY')
    subs = []
    for name, provider in (document.get('proxy-providers') or {}).items():
        if isinstance(provider, dict) and isinstance(provider.get('url'), str):
            subs.append({'name': str(name), 'url': provider['url']})
    if not subs and subscription_url:
        subs.append({'name': '当前订阅', 'url': subscription_url})
    for index, row in enumerate(subs):
        row['_clash_id'] = 's:' + str(index)
    node_groups = []
    for index, row in enumerate(groups):
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            raise ValueError('INVALID_CLASH_GROUP')
        members = row.get('proxies') or []
        if not isinstance(members, list) or not all(isinstance(name, str) for name in members):
            raise ValueError('INVALID_CLASH_MEMBERS')
        group = {'_clash_id': 'g:' + str(index), 'name': row['name'],
                 'subs': list(row.get('use') or []), 'include': list(dict.fromkeys(members)),
                 'keywords': ['exp#' + row['filter']] if row.get('filter') else [],
                 'exclude_keywords': ['exp#' + row['exclude-filter']] if row.get('exclude-filter') else []}
        if row.get('include-all') or row.get('include-all-proxies') or row.get('include-all-providers'):
            group['subs'] = ['all']
        if 'smartspeedtest@hide' in group['include']:
            group['include'].remove('smartspeedtest@hide')
            group['smart'] = True
        if row.get('dialer-proxy'):
            group.update(use_pre_proxy=True, pre_proxy_group=row['dialer-proxy'])
        if row.get('type') == 'url-test':
            group.update(speedtest_url=row.get('url', ''), interval=row.get('interval', 600))
        elif row.get('type') == 'fallback':
            group.update(failover=True, monitor_url=row.get('url', ''),
                         expected_status=str(row.get('expected-status', '0')))
        node_groups.append(group)
    rule_sets = []
    for name, row in providers.items():
        if not isinstance(row, dict):
            raise ValueError('INVALID_CLASH_RULE_PROVIDER')
        rule_sets.append({'_clash_id': 'p:' + str(name), 'priority': (len(rule_sets) + 1) * 10, 'type': 'rule-set',
                          'name': str(name), 'url': row.get('url', ''),
                          'behavior': row.get('behavior', 'classical'),
                          'interval': row.get('interval', 86400),
                          'format': row.get('format', 'yaml'), 'proxy': row.get('proxy', '')})
    if rules:
        rule_sets.append({'_clash_id': 'rules', 'priority': (len(rule_sets) + 1) * 10,
                          'remark': '当前完整分流规则（保持原顺序）', 'fixrule': list(rules)})
    config = {'version': 'PaoPaoGateWay 3.1', 'exported_at': 'running-clash-snapshot',
              'subs': subs, 'node-groups': node_groups, 'rules': rule_sets}
    counts = {'node_groups': len(groups), 'rules': len(rules), 'rule_providers': len(providers)}
    return config, counts


def fingerprint(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def source_identity(values):
    return fingerprint({key: values.get(key, '') for key in ('mode', 'suburl', 'yamlfile')})


def strings(value):
    if not isinstance(value, list) or not all(isinstance(item, str) and len(item) <= 8192 for item in value):
        raise ValueError('INVALID_EDITOR_LIST')
    return value


def group_view(row):
    members = strings(row.get('include', []))
    subs = strings(row.get('subs', [])) or ([] if members else ['all'])
    mode = ('smart' if row.get('smart') else 'url-test' if row.get('speedtest_url') or row.get('interval')
            else 'fallback' if row.get('failover') else 'select')
    return {'name': row.get('name', ''), 'include': members, 'subs': subs,
            'keywords': strings(row.get('keywords', [])), 'exclude_keywords': strings(row.get('exclude_keywords', [])),
            'mode': mode, 'url': row.get('speedtest_url', '') if mode == 'url-test' else
                (row.get('monitor_url') or 'https://www.youtube.com/generate_204') if mode == 'fallback' else '',
            'interval': int(row.get('interval') or 600) if mode == 'url-test' else None,
            'expected': str(row.get('expected_status') or '0') if mode == 'fallback' else None,
            'retries': row.get('retries', 3) if mode == 'fallback' else None,
            'pre_proxy': row.get('pre_proxy_group', '') if row.get('use_pre_proxy') else ''}


def provider_view(row):
    return {key: row.get(key, default) or default for key, default in
            (('name', ''), ('url', ''), ('behavior', 'domain'), ('format', 'auto'), ('interval', 86400), ('proxy', ''))}


def rule_lines(row):
    if row.get('url') and row.get('type') != 'rule-set':
        raise ValueError('CLASH_RULE_URL_UNSUPPORTED')
    return [line.strip() for line in strings(row.get('fixrule', [])) if line.strip()]


def regex_keywords(items):
    return '|'.join('(?:' + (item[4:] if item.startswith('exp#') else re.escape(item)) + ')' for item in items)


def rename_rule(rule, names):
    parts = rule.split(',')
    index = len(parts) - 1
    while index > 0 and parts[index].strip().lower() in {'no-resolve', 'src', 'dst', 'no-redir', 'not'}:
        index -= 1
    if index > 0 and parts[index].strip() in names:
        parts[index] = names[parts[index].strip()]
        return ','.join(parts)
    return rule


def rename_references(value, names):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'dialer-proxy', 'proxy'} and isinstance(item, str) and item in names:
                value[key] = names[item]
            else:
                rename_references(item, names)
    elif isinstance(value, list):
        for item in value:
            rename_references(item, names)


def merge_editor(document, baseline, submitted):
    """Apply only changed editor fields to copies of original YAML objects."""
    if not isinstance(submitted, dict):
        raise ValueError('INVALID_EDITOR_CONFIG')
    result = deepcopy(document)
    old_groups = {row['_clash_id']: (row, original) for row, original in
                  zip(baseline['node-groups'], document.get('proxy-groups', []))}
    groups, seen, renames = [], set(), {}
    for row in submitted.get('node-groups', []):
        if not isinstance(row, dict):
            raise ValueError('INVALID_EDITOR_GROUP')
        identity = row.get('_clash_id', '')
        if identity and (identity not in old_groups or identity in seen):
            raise ValueError('INVALID_EDITOR_ID')
        seen.add(identity) if identity else None
        old, original = old_groups[identity] if identity else ({'include': [], 'subs': ['all']}, {})
        previous, edited = group_view(old), group_view(row)
        name = edited['name']
        if not isinstance(name, str) or not name or len(name) > 256 or any(c in name for c in '\r\n,'):
            raise ValueError('INVALID_GROUP_NAME')
        group = deepcopy(original)
        group['name'] = name
        if identity and name != original['name']:
            renames[original['name']] = name
        if edited['include'] != previous['include'] or not identity:
            group['proxies'] = list(dict.fromkeys(edited['include']))
        if edited['subs'] != previous['subs'] or not identity:
            for key in ('use', 'include-all', 'include-all-proxies', 'include-all-providers'):
                group.pop(key, None)
            if 'all' in edited['subs']:
                group['include-all-proxies'] = True
                if document.get('proxy-providers'):
                    group['include-all-providers'] = True
            elif edited['subs']:
                if set(edited['subs']) - set(document.get('proxy-providers', {})):
                    raise ValueError('UNKNOWN_PROXY_PROVIDER')
                group['use'] = edited['subs']
        for field, native in (('keywords', 'filter'), ('exclude_keywords', 'exclude-filter')):
            if edited[field] != previous[field]:
                if edited[field]:
                    group[native] = regex_keywords(edited[field])
                else:
                    group.pop(native, None)
        # Pre-proxy is materialized as provider override, not an ignored group field.
        if not identity or edited['mode'] != previous['mode']:
            group['type'] = 'select' if edited['mode'] == 'smart' else edited['mode']
        if edited['mode'] == 'smart':
            group['proxies'] = list(dict.fromkeys(group.get('proxies', []) + ['smartspeedtest@hide']))
        elif 'proxies' in group and 'smartspeedtest@hide' in group['proxies']:
            group['proxies'].remove('smartspeedtest@hide')
        if edited['mode'] in {'url-test', 'fallback'}:
            if not identity or edited['url'] != previous['url']:
                group['url'] = edited['url'] or 'https://www.youtube.com/generate_204'
            if edited['mode'] == 'url-test' and (not identity or edited['interval'] != previous['interval']):
                if not 30 <= edited['interval'] <= 86400:
                    raise ValueError('INVALID_TEST_INTERVAL')
                group['interval'] = edited['interval']
            if edited['mode'] == 'fallback':
                for field, native in (('expected', 'expected-status'), ('retries', 'max-failed-times')):
                    if edited[field] != previous[field]:
                        group[native] = edited[field]
        groups.append(group)
    if len({row['name'] for row in groups}) != len(groups):
        raise ValueError('DUPLICATE_GROUP_NAME')
    old_rules = {row['_clash_id']: row for row in baseline['rules']}
    providers, lines, seen = {}, [], set()
    for row in submitted.get('rules', []):
        if not isinstance(row, dict):
            raise ValueError('INVALID_EDITOR_RULE')
        identity = row.get('_clash_id', '')
        if identity and (identity not in old_rules or identity in seen):
            raise ValueError('INVALID_EDITOR_ID')
        seen.add(identity) if identity else None
        if row.get('type') == 'rule-set':
            old = old_rules.get(identity, {})
            if identity and old.get('type') != 'rule-set':
                raise ValueError('CLASH_RULE_KIND_CHANGED')
            previous, edited = provider_view(old), provider_view(row)
            original = document.get('rule-providers', {}).get(old.get('name'), {})
            provider = deepcopy(original)
            if not identity:
                provider['type'] = 'http'
            for key in ('url', 'behavior', 'format', 'interval', 'proxy'):
                if edited[key] != previous[key] or not identity:
                    if key == 'format' and edited[key] == 'auto':
                        provider.pop('format', None)
                    elif key == 'proxy' and not edited[key]:
                        provider.pop(key, None)
                    else:
                        provider[key] = edited[key]
            name = edited['name']
            if not isinstance(name, str) or not name or name in providers or any(c in name for c in '\n\r,'):
                raise ValueError('INVALID_RULE_PROVIDER_NAME')
            providers[name] = provider
            if identity and name != old['name']:
                renames['RULE-SET:' + old['name']] = name
        else:
            if identity and old_rules[identity].get('type') == 'rule-set':
                raise ValueError('CLASH_RULE_KIND_CHANGED')
            lines.extend(rule_lines(row))
    if groups != document.get('proxy-groups', []):
        result['proxy-groups'] = groups
    if providers != document.get('rule-providers', {}):
        result['rule-providers'] = providers
    # Whitespace-only normalization must not cause a policy rewrite.
    if lines != [line.strip() for line in document.get('rules', []) if line.strip()]:
        result['rules'] = lines
    group_renames = {key: value for key, value in renames.items() if not key.startswith('RULE-SET:')}
    for group in result.get('proxy-groups', []):
        if 'proxies' in group:
            group['proxies'] = [group_renames.get(name, name) for name in group['proxies']]
    result['rules'] = [rename_rule(line, group_renames) for line in result.get('rules', [])]
    for index, line in enumerate(result['rules']):
        parts = line.split(',')
        if len(parts) > 1 and parts[0].strip().upper() == 'RULE-SET' and 'RULE-SET:' + parts[1].strip() in renames:
            parts[1] = renames['RULE-SET:' + parts[1].strip()]
            result['rules'][index] = ','.join(parts)
    rename_references(result, group_renames)
    source_url = None
    old_subs = {row.get('_clash_id', row['name']): row for row in baseline.get('subs', [])}
    new_subs = submitted.get('subs', [])
    if len(new_subs) != len(old_subs):
        raise ValueError('CLASH_SUBSCRIPTION_STRUCTURE_UNSUPPORTED')
    for row in new_subs:
        key = row.get('_clash_id', row.get('name'))
        if key not in old_subs:
            raise ValueError('CLASH_SUBSCRIPTION_STRUCTURE_UNSUPPORTED')
        old = old_subs[key]
        if row.get('name') != old.get('name'):
            raise ValueError('CLASH_SUBSCRIPTION_RENAME_UNSUPPORTED')
        if row.get('url') != old.get('url'):
            url = row.get('url', '')
            from urllib.parse import urlsplit
            target = urlsplit(url)
            if target.scheme not in {'https', 'http'} or not target.hostname or target.username or target.password or target.fragment or any(ord(c) < 33 for c in url):
                raise ValueError('INVALID_URL')
            if old['name'] in result.get('proxy-providers', {}):
                result['proxy-providers'][old['name']]['url'] = url
            else:
                source_url = url
    return result, source_url, group_renames


def feature_options(config):
    result = {key: deepcopy(config.get(key) or {'enable': False})
              for key in ('global_monitor', 'restart_cron', 'shadowsocks_in')}
    for key, value in list(result.items()):
        if not value.get('enable'): result[key] = {'enable': False}
    result['node-groups'] = [{key: row[key] for key in ('name', 'smart', 'speedtest_url', 'use_pre_proxy', 'pre_proxy_group') if key in row}
                             for row in config.get('node-groups', []) if row.get('smart') or row.get('use_pre_proxy')]
    names = {row.get('name') for row in config.get('node-groups', [])} | {'DIRECT'}
    for row in result['node-groups']:
        if row.get('use_pre_proxy') and (row.get('pre_proxy_group') not in names or row.get('pre_proxy_group') == row.get('name')):
            raise ValueError('INVALID_PRE_PROXY')
    restart = result['restart_cron']
    if restart.get('enable') and (type(restart.get('hour')) is not int or not 0 <= restart['hour'] <= 23):
        raise ValueError('INVALID_RESTART_HOUR')
    ss = result['shadowsocks_in']
    if ss.get('enable'):
        import base64
        if ss.get('cipher') not in {'aes-128-gcm', '2022-blake3-aes-128-gcm'} or not isinstance(ss.get('password'), str) or len(ss['password']) < 16:
            raise ValueError('INVALID_SS_CONFIG')
        if ss['cipher'].startswith('2022-'):
            try:
                if len(base64.b64decode(ss['password'], validate=True)) < 16: raise ValueError()
            except Exception: raise ValueError('INVALID_SS_CONFIG') from None
    return result


def apply_features(document, features):
    result = logical_document(document)
    managed = {'schema': 1, 'groups': {}, 'providers': [], 'absent_sections': [
        key for key in ('proxy-providers', 'proxies', 'listeners') if key not in result]}
    if any(group.get('smart') for group in features.get('node-groups', [])):
        proxies = result.setdefault('proxies', [])
        if not any(row.get('name') == 'smartspeedtest@hide' for row in proxies):
            proxies.append({'name': 'smartspeedtest@hide', 'type': 'reject'})
            managed['smart_proxy'] = True
    inline = {row['name']: row for row in result.get('proxies', []) if isinstance(row, dict) and row.get('name')}
    original_providers = deepcopy(result.get('proxy-providers', {}))
    options = {row['name']: row for row in features.get('node-groups', [])}
    for group in result.get('proxy-groups', []):
        feature = options.get(group['name'], {})
        if not feature.get('use_pre_proxy'): continue
        managed['groups'][group['name']] = deepcopy(group)
        prefix = 'ppgw-chain-' + hashlib.sha256(group['name'].encode()).hexdigest()[:12]
        provider_names = list(original_providers) if group.get('include-all') or group.get('include-all-providers') else list(group.get('use', []))
        selected = list(inline) if group.get('include-all') or group.get('include-all-proxies') else list(group.get('proxies', []))
        payload = [deepcopy(inline[name]) for name in selected if name in inline]
        generated = []
        providers = result.setdefault('proxy-providers', {})
        for index, provider_name in enumerate(provider_names):
            if provider_name not in original_providers: raise ValueError('UNKNOWN_PROXY_PROVIDER')
            name = prefix + '-' + str(index)
            if name in providers: raise ValueError('RESERVED_PROVIDER_NAME')
            provider = deepcopy(original_providers[provider_name])
            provider['override'] = {**provider.get('override', {}), 'dialer-proxy': feature['pre_proxy_group']}
            if provider.get('path'):
                provider['path'] = './providers/' + name + '.yaml'
            providers[name] = provider; generated.append(name)
        if payload:
            name = prefix + '-inline'
            if name in providers: raise ValueError('RESERVED_PROVIDER_NAME')
            providers[name] = {'type': 'inline', 'payload': payload,
                               'override': {'dialer-proxy': feature['pre_proxy_group']}}
            generated.append(name)
        for key in ('include-all', 'include-all-proxies', 'include-all-providers'):
            group.pop(key, None)
        group['proxies'] = [name for name in group.get('proxies', []) if name not in inline]
        group['use'] = generated
        managed['providers'].extend(generated)
    listeners = result.get('listeners', [])
    ss = features.get('shadowsocks_in') or {}
    if ss.get('enable'):
        listeners = [row for row in listeners if row.get('name') != 'ppgw-shadowsocks-in']
        listeners.append({'name': 'ppgw-shadowsocks-in', 'type': 'shadowsocks', 'listen': '127.0.0.1',
                          'port': 8080, 'udp': True, 'password': ss['password'], 'cipher': ss['cipher']})
        result['listeners'] = listeners
        managed['ss_listener'] = True
    elif any(row.get('name') == 'ppgw-shadowsocks-in' for row in listeners):
        result['listeners'] = [row for row in listeners if row.get('name') != 'ppgw-shadowsocks-in']
    if managed['groups'] or managed['providers'] or managed.get('smart_proxy') or managed.get('ss_listener'):
        result['x-ppgw-features'] = managed
    return result
