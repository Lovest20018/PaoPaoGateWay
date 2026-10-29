#!/usr/bin/env python3
"""Loopback PPsub save/apply helper for the authenticated native panel."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import OrderedDict
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import threading
import time
from urllib.parse import urlsplit
from gateway_api import CoreClient, ApiError
from clash_ppsub import editor_config, running_document, merge_editor, fingerprint, source_identity, feature_options, apply_features, logical_document
from clash_policy import build_policy, SECTIONS

CONFIG = Path('/etc/paopaogw/files/ppgw.ini')
FILES = CONFIG.parent
STATE = Path('/var/lib/paopaogw/admin')
CODE = Path(__file__).resolve().parent
CORE_TOKEN = Path('/var/lib/paopaogw/core-token')
POLICY = FILES / 'clash-policy.json'


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


def config_values():
    exported = subprocess.run([str(CODE / 'bin/sniffbox'), 'ppgw', '-ini-export', str(CONFIG)],
                              capture_output=True, text=True, check=True, timeout=5)
    return dict(value.split('=', 1) for value in shlex.split(exported.stdout) if '=' in value)


def native_ppsub_source(values):
    return values.get('mode') == 'suburl' and (
        values.get('suburl', '').startswith('ppsub@') or (FILES / 'ppsub.json').is_file())


def update_config(raw: bytes, changes: dict) -> bytes:
    text = raw.decode('utf-8')
    if not text.startswith('#paopao-gateway'):
        raise ValueError('INVALID_CONFIG')
    lines = text.replace('\r\n', '\n').splitlines()
    output = [line for line in lines if not any(re.match(r'^\s*' + re.escape(k) + r'\s*=', line) for k in changes)]
    for key, value in changes.items():
        if '\n' in value or '\r' in value or '"' in value or '\x00' in value:
            raise ValueError('INVALID_VALUE')
        output.append(f'{key}="{value}"')
    return ('\n'.join(output) + '\n').encode()


def validate_subscription(body):
    if not isinstance(body, dict) or set(body) - {'kind', 'value', 'base_revision', 'operation_id'}:
        raise ValueError('INVALID_REQUEST')
    kind, value = body.get('kind'), body.get('value')
    if kind not in {'url', 'yaml', 'ppsub'} or not isinstance(value, str):
        raise ValueError('INVALID_SUBSCRIPTION')
    if kind == 'url':
        if not 1 <= len(value) <= 4096 or any(ord(c) < 33 for c in value) or '"' in value:
            raise ValueError('INVALID_URL')
        target = urlsplit(value)
        if target.scheme not in {'http', 'https'} or not target.hostname or target.username or target.password or target.fragment:
            raise ValueError('INVALID_URL')
        _ = target.port
        return {'mode': 'suburl', 'suburl': value, 'fast_node': 'no'}, None
    if len(value.encode()) > 1024 * 1024:
        raise ValueError('FILE_TOO_LARGE')
    if kind == 'yaml':
        if not re.search(r'^proxies\s*:', value, re.MULTILINE):
            raise ValueError('YAML_PROXIES_REQUIRED')
        return {'mode': 'yaml', 'yamlfile': 'custom.yaml', 'fast_node': 'no'}, ('custom.yaml', value.encode())
    parsed = json.loads(value)
    if (not isinstance(parsed, dict) or not isinstance(parsed.get('subs'), list)
            or not parsed.get('subs') or not parsed.get('exported_at')):
        raise ValueError('INVALID_PPSUB')
    return {'mode': 'suburl', 'suburl': 'ppsub@http://127.0.0.1/ppsub.json', 'fast_node': 'no'}, ('ppsub.json', value.encode())


def atomic_write(path, raw):
    pending = path.with_name(path.name + '.admin-new')
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


class Admin:
    def __init__(self):
        self.lock = threading.Lock()
        self.operation = None
        self.snapshots = OrderedDict()
        self.core = CoreClient(CORE_TOKEN)

    def authenticated(self, authorization):
        expected = 'Bearer ' + CORE_TOKEN.read_text().strip()
        return isinstance(authorization, str) and hmac.compare_digest(
            authorization.encode('utf-8'), expected.encode('ascii'))

    def maintenance(self):
        from mihomo_update import Updater, STATE as update_state
        def read(path, default):
            try: return json.loads(path.read_bytes())
            except (OSError, ValueError): return default
        return {'core': Updater().status(), 'operation': read(update_state / 'operation.json', None),
                'policy': read(STATE / 'policy-status.json', {'status': 'not_applied'}),
                'has_local_policy': POLICY.exists(), 'timezone': os.environ.get('TZ', 'Asia/Shanghai'),
                'log_source': 'instance_controller', 'cloudflared': 'removed'}

    def core_action(self, action):
        from mihomo_update import atomic_json, STATE as update_state
        if action not in {'check', 'update', 'rollback'}:
            raise ValueError('INVALID_OPERATION')
        with self.lock:
            if self.operation and self.operation.get('status') == 'applying':
                raise ValueError('BUSY')
            for unit in ('paopaogw-maintenance', 'paopaogw-core-action@check',
                         'paopaogw-core-action@update', 'paopaogw-core-action@rollback'):
                if subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0:
                    raise ValueError('BUSY')
            atomic_json(update_state / 'operation.json', {'status': 'running', 'action': action, 'started_at': time.time()})
            subprocess.run(['systemctl', 'start', '--no-block', 'paopaogw-core-action@' + action], check=True, timeout=5)
        return {'status': 'running', 'action': action}

    def reset_policy(self, body):
        if not isinstance(body, dict) or set(body) != {'base_revision'}:
            raise ValueError('INVALID_REQUEST')
        with self.lock:
            if self.operation and self.operation.get('status') == 'applying':
                raise ValueError('BUSY')
            if revision(CONFIG.read_bytes()) != body['base_revision']:
                raise ValueError('CONFIG_CHANGED')
            if not POLICY.is_file(): return {'status': 'unchanged'}
            old = POLICY.read_bytes()
            self.operation = {'id': 'policy-reset-' + str(int(time.time())), 'status': 'applying', 'message': '正在恢复订阅原始分流策略…'}
            thread = threading.Timer(.5, self.reset_worker, args=(old,))
            thread.daemon = True; thread.start()
            return dict(self.operation)

    def reset_worker(self, old):
        try:
            STATE.mkdir(parents=True, exist_ok=True)
            atomic_write(STATE / 'clash-policy.before-reset.json', old)
            POLICY.unlink()
            subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    if self.core.request('/sniffbox/info').get('clash_version'):
                        with self.lock: self.operation.update(status='applied', message='已恢复订阅原始策略。')
                        return
                except ApiError: pass
                time.sleep(1)
            raise ValueError('NOT_READY')
        except Exception:
            if not POLICY.exists():
                atomic_write(POLICY, old)
                subprocess.run(['systemctl', 'restart', 'paopaogw'], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with self.lock: self.operation.update(status='failed', message='恢复原始策略失败，已保留此前配置。')

    def current_config(self):
        raw = CONFIG.read_bytes()
        # Read the Rust parser's quoted assignments without executing a shell.
        values = config_values()
        suburl = values.get('suburl', '')
        local = FILES / 'ppsub.json'
        runtime = Path('/var/lib/paopaogw/clash/clash-dashboard/data/ppsub.json')
        if native_ppsub_source(values) and (local.is_file() or runtime.is_file()):
            data = json.loads((local if local.is_file() else runtime).read_bytes())
            source, notice = 'ppsub', '已载入当前 PPsub 配置，编辑完成后可直接保存并应用。'
        elif values.get('mode') in {'suburl', 'yaml'}:
            document = running_document()
            data, counts = editor_config(document, suburl if values.get('mode') == 'suburl' else '')
            existing_policy = json.loads(POLICY.read_bytes()) if POLICY.is_file() else None
            if existing_policy and existing_policy.get('source_identity') == source_identity(values):
                for key in ('global_monitor', 'restart_cron', 'shadowsocks_in'):
                    data[key] = (existing_policy.get('features') or {}).get(key, {'enable': False})
                for group in data['node-groups']:
                    for feature in (existing_policy.get('features') or {}).get('node-groups', []):
                        if feature.get('name') == group['name']:
                            group.update({key: value for key, value in feature.items() if key != 'name'})
            source = 'clash_runtime'
            notice = (f"已读取实际运行配置：{counts['node_groups']} 个节点组、{counts['rules']} 条分流规则、"
                      f"{counts['rule_providers']} 个规则订阅。编辑后会在原配置上合并修改，"
                      '其他字段保留，订阅节点继续更新；修改的分组和规则作为本地策略保存。')
            snapshot_id = secrets.token_urlsafe(24)
            with self.lock:
                self.snapshots[snapshot_id] = {'created': time.monotonic(), 'document': document,
                    'baseline': data, 'revision': revision(raw), 'values': values,
                    'policy': POLICY.read_bytes() if POLICY.exists() else None}
                while len(self.snapshots) > 16:
                    self.snapshots.popitem(last=False)
            return {'config': data, 'source': source, 'notice': notice, 'counts': counts,
                    'base_revision': revision(raw), 'write_allowed': True,
                    'edit_kind': 'clash_patch', 'snapshot_id': snapshot_id,
                    'node_choices': [row['name'] for row in document.get('proxies', [])
                                     if isinstance(row, dict) and isinstance(row.get('name'), str)]}
        else:
            data = {'version': 'PaoPaoGateWay 3.1', 'exported_at': 'new-config',
                    'subs': [], 'node-groups': [], 'rules': []}
            source, notice = 'empty', '当前没有 PPsub 配置，请添加订阅、节点组和规则后保存。'
        return {'config': data, 'source': source, 'notice': notice, 'base_revision': revision(raw), 'write_allowed': True}

    def status(self):
        raw = CONFIG.read_bytes()
        mode = re.search(rb'^mode=["\']?([a-z0-9]+)', raw, re.MULTILINE)
        info = {}
        try:
            info = self.core.request('/sniffbox/info')
            running_mode = info.get('mode')
            ready = bool(running_mode and (running_mode not in {'yaml', 'suburl'} or info.get('clash_version')))
        except ApiError:
            ready, running_mode = False, None
        with self.lock:
            operation = dict(self.operation) if self.operation else None
        return {'base_revision': revision(raw), 'saved_mode': mode.group(1).decode() if mode else 'unknown',
                'running_mode': running_mode, 'ready': ready,
                'mihomo_installed': (CODE / 'bin/clash').is_file(), 'operation': operation,
                'ppsub_write_allowed': ready,
                'edit_kind': 'clash_patch' if running_mode in {'yaml', 'suburl'} and not info.get('ppsub') else 'ppsub'}

    def apply(self, body):
        if isinstance(body, dict) and body.get('kind') == 'clash_patch':
            return self.apply_clash(body)
        changes, upload = validate_subscription(body)
        if body.get('kind') == 'ppsub':
            values = config_values()
            if values.get('mode') in {'yaml', 'suburl'} and not native_ppsub_source(values):
                raise ValueError('CLASH_PPSUB_NOT_EQUIVALENT')
        if not (CODE / 'bin/clash').is_file():
            raise ValueError('MIHOMO_REQUIRED')
        operation_id = body.get('operation_id')
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', operation_id):
            raise ValueError('INVALID_OPERATION_ID')
        with self.lock:
            if self.operation and self.operation['id'] == operation_id:
                return dict(self.operation)
            if self.operation and self.operation['status'] == 'applying':
                raise ValueError('BUSY')
            original = CONFIG.read_bytes()
            if body.get('base_revision') != revision(original):
                raise ValueError('CONFIG_CHANGED')
            candidate = update_config(original, changes)
            self.operation = {'id': operation_id, 'status': 'applying', 'message': '正在保存并加载订阅…'}
            # Allow the native panel's 202 response to finish before restarting its core.
            thread = threading.Timer(0.5, self.worker, args=(original, candidate, upload))
            thread.daemon = True
            thread.start()
            return dict(self.operation)

    def apply_clash(self, body):
        if (set(body) != {'kind', 'value', 'base_revision', 'operation_id', 'snapshot_id'}
                or not isinstance(body['value'], str) or len(body['value'].encode()) > 1024 * 1024):
            raise ValueError('INVALID_REQUEST')
        operation_id = body['operation_id']
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', operation_id):
            raise ValueError('INVALID_OPERATION_ID')
        if not isinstance(body['snapshot_id'], str):
            raise ValueError('CONFIG_CHANGED')
        submitted = json.loads(body['value'])
        with self.lock:
            if self.operation and self.operation['id'] == operation_id:
                return dict(self.operation)
            if self.operation and self.operation['status'] == 'applying':
                raise ValueError('BUSY')
            snapshot = self.snapshots.get(body['snapshot_id'])
            if not snapshot or time.monotonic() - snapshot['created'] > 1800:
                raise ValueError('CONFIG_CHANGED')
            original = CONFIG.read_bytes()
            if (body['base_revision'] != revision(original) or revision(original) != snapshot['revision']
                    or fingerprint(running_document()) != fingerprint(snapshot['document'])
                    or (POLICY.read_bytes() if POLICY.exists() else None) != snapshot['policy']):
                raise ValueError('CONFIG_CHANGED')
            merged, source_url, renames = merge_editor(snapshot['document'], snapshot['baseline'], submitted)
            features = feature_options(submitted)
            old_features = feature_options(snapshot['baseline'])
            changes = {'suburl': source_url} if source_url else {}
            if merged == snapshot['document'] and not changes and features == old_features:
                self.operation = {'id': operation_id, 'status': 'unchanged', 'message': '配置未改变，无需重启。'}
                return dict(self.operation)
            candidate = update_config(original, changes) if changes else original
            values = dict(snapshot['values'], **changes)
            existing = json.loads(snapshot['policy']) if snapshot['policy'] else None
            policy = build_policy(snapshot['document'], merged, existing, source_identity(values), renames)
            policy['features'] = features
            merged = apply_features(merged, features)
            self.operation = {'id': operation_id, 'status': 'applying', 'message': '正在校验并保存本地分流策略…'}
            thread = threading.Timer(0.5, self.clash_worker,
                args=(snapshot, original, candidate, policy, merged))
            thread.daemon = True
            thread.start()
            return dict(self.operation)

    def clash_worker(self, snapshot, original, candidate, policy, document):
        import yaml
        policy_bytes = (json.dumps(policy, ensure_ascii=False, indent=2) + '\n').encode()
        written = False
        check_file = STATE / ('candidate-' + self.operation['id'] + '.yaml')
        try:
            STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write(check_file, yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode())
            pid = subprocess.check_output(['systemctl', 'show', 'paopaogw', '--property=MainPID', '--value'],
                                          text=True, timeout=5).strip()
            if not pid.isascii() or not pid.isdigit() or int(pid) <= 0:
                raise ValueError('CORE_NOT_RUNNING')
            # Validate against the same files/rule cache as the live core, without loading it.
            subprocess.run(['nsenter', '-t', pid, '-m', '--', str(CODE / 'bin/clash'), '-t',
                            '-d', '/etc/config/clash', '-f', str(check_file)],
                           check=True, timeout=25, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if (CONFIG.read_bytes() != original or fingerprint(running_document()) != fingerprint(snapshot['document'])
                    or (POLICY.read_bytes() if POLICY.exists() else None) != snapshot['policy']):
                raise ValueError('CONFIG_CHANGED')
            if not (CODE / 'clash_policy.py').is_file():
                raise ValueError('POLICY_HELPER_MISSING')
            atomic_write(STATE / 'ppgw.before-policy.ini', original)
            if snapshot['policy'] is not None:
                atomic_write(STATE / 'clash-policy.before.json', snapshot['policy'])
            atomic_write(POLICY, policy_bytes)
            written = True
            if candidate != original:
                atomic_write(CONFIG, candidate)
            subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    info = self.core.request('/sniffbox/info')
                    if info.get('clash_version'):
                        actual = running_document()
                        expected = logical_document(document)
                        if all(actual.get(key) == expected.get(key) for key in policy['sections']):
                            with self.lock:
                                self.operation.update(status='applied', message='修改已合并到完整配置，订阅节点仍按原设置更新。')
                            return
                except (ApiError, OSError, ValueError, subprocess.SubprocessError):
                    pass
                time.sleep(1)
            raise RuntimeError('NOT_READY')
        except Exception:
            restored = not written
            if written:
                try:
                    if POLICY.read_bytes() == policy_bytes and CONFIG.read_bytes() in (original, candidate):
                        if snapshot['policy'] is None:
                            POLICY.unlink()
                        else:
                            atomic_write(POLICY, snapshot['policy'])
                        if candidate != original:
                            atomic_write(CONFIG, original)
                        subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True, timeout=30,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        restored = True
                except Exception:
                    restored = False
            with self.lock:
                self.operation.update(status='failed', message=('配置校验或加载失败，原配置已保留/恢复。请检查节点引用和规则。'
                    if restored else '加载失败且配置已被外部修改，请检查运行状态。'))
        finally:
            check_file.unlink(missing_ok=True)

    def worker(self, original, candidate, upload):
        old_files = {}
        try:
            STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write(STATE / 'ppgw.before-subscription.ini', original)
            # A stale local PPsub must not override a subsequently imported URL/YAML.
            for name in ('ppsub.json', 'custom.yaml'):
                path = FILES / name
                old_files[name] = path.read_bytes() if path.exists() else None
            for name in ('ppsub.json', 'custom.yaml'):
                if upload and upload[0] == name:
                    atomic_write(FILES / name, upload[1])
                elif name == 'ppsub.json' and (FILES / name).exists():
                    (FILES / name).unlink()
            atomic_write(CONFIG, candidate)
            subprocess.run(['systemctl', 'restart', 'paopaogw.service'], check=True, timeout=25,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    info = self.core.request('/sniffbox/info')
                    if info.get('mode') in {'yaml', 'suburl'} and info.get('clash_version'):
                        with self.lock:
                            self.operation.update(status='applied', message='PPsub 配置已加载，可以到代理页选择节点。')
                        return
                except ApiError:
                    pass
                time.sleep(1)
            raise RuntimeError('NOT_READY')
        except Exception:
            # Restore only when the candidate is still ours, preserving later external edits.
            restored = False
            try:
                if CONFIG.read_bytes() == candidate:
                    atomic_write(CONFIG, original)
                    for name, content in old_files.items():
                        if content is None:
                            (FILES / name).unlink(missing_ok=True)
                        else:
                            atomic_write(FILES / name, content)
                    restored = True
                    try:
                        subprocess.run(['systemctl', 'restart', 'paopaogw.service'], timeout=25,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
            except Exception:
                restored = False
            finally:
                with self.lock:
                    self.operation.update(status='failed', message=('订阅未能加载，已恢复原配置。请检查链接/文件后重试。'
                        if restored else '订阅未能加载，检测到外部配置变化，请检查运行状态。'))


class Handler(BaseHTTPRequestHandler):
    server_version = 'PaoPaoGateway-Subscription/1'
    def setup(self):
        super().setup()
        self.connection.settimeout(10)
    def log_message(self, *args):
        pass
    def respond(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        for key, value in [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store'),
                           ('Content-Length', str(len(raw))), ('X-Content-Type-Options', 'nosniff')]:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if not self.server.admin.authenticated(self.headers.get('Authorization')):
            return self.respond(401, {'error': 'LOGIN_REQUIRED'})
        try:
            if self.path == '/api/status':
                self.respond(200, self.server.admin.status())
            elif self.path == '/api/config':
                self.respond(200, self.server.admin.current_config())
            elif self.path == '/api/maintenance':
                self.respond(200, self.server.admin.maintenance())
            else:
                self.respond(404, {'error': 'NOT_FOUND'})
        except Exception:
            self.respond(503, {'error': '配置暂时无法读取，请稍后重试。'})
    def do_POST(self):
        try:
            if self.headers.get('Origin') not in {'http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')}:
                return self.respond(403, {'error': 'ORIGIN_REJECTED'})
            if (self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding')
                    or len(self.headers.get_all('Content-Length', [])) != 1):
                raise ValueError('INVALID_REQUEST')
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 < length <= 1100000:
                raise ValueError('INVALID_LENGTH')
            body = json.loads(self.rfile.read(length))
            if not self.server.admin.authenticated(self.headers.get('Authorization')):
                return self.respond(401, {'error': 'LOGIN_REQUIRED'})
            if self.path == '/api/subscription':
                return self.respond(202, self.server.admin.apply(body))
            if self.path in {'/api/core/check', '/api/core/update', '/api/core/rollback'}:
                if body != {}: raise ValueError('INVALID_REQUEST')
                return self.respond(202, self.server.admin.core_action(self.path.rsplit('/', 1)[-1]))
            if self.path == '/api/policy/reset':
                return self.respond(202, self.server.admin.reset_policy(body))
            self.respond(404, {'error': 'NOT_FOUND'})
        except ValueError as error:
            messages = {
                'CONFIG_CHANGED': (409, '运行配置已变化或编辑快照已过期，请重新载入“当前配置”。'),
                'CLASH_GLOBAL_OPTIONS_UNSUPPORTED': (400, '完整配置当前支持节点组、规则和订阅链接编辑；全局监测、定时重启与 Shadowsocks 入站请保留原设置。'),
                'CLASH_GROUP_OPTION_UNSUPPORTED': (400, '该节点组选项无法直接映射到 Clash，请保留原模式或使用手动选择、测速、故障转移。'),
                'CLASH_RULE_URL_UNSUPPORTED': (400, '请将规则填写为手写规则，或使用 RULE-SET 规则订阅。'),
                'CLASH_SUBSCRIPTION_STRUCTURE_UNSUPPORTED': (400, '当前完整订阅请保留提供商条目，可修改链接；多订阅合并请使用原生 PPsub 配置。'),
                'CLASH_SUBSCRIPTION_RENAME_UNSUPPORTED': (400, '当前完整订阅请保留提供商名称，可修改订阅链接。'),
            }
            if str(error) in messages:
                status, message = messages[str(error)]
                return self.respond(status, {'error': message})
            if str(error) == 'CLASH_PPSUB_NOT_EQUIVALENT':
                return self.respond(409, {'error': '当前为完整 Clash/Mihomo 配置，不能以 PPsub 简化配置覆盖。当前配置可完整读取查看。'})
            self.respond(400, {'error': '请求无效、配置已变化或有任务正在运行，请刷新后重试。'})
        except (UnicodeError, KeyError):
            self.respond(400, {'error': '请求无效、配置已变化或有任务正在运行，请刷新后重试。'})
        except Exception:
            self.respond(503, {'error': '服务暂不可用'})


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8789), Handler)
    server.admin = Admin()
    server.serve_forever()
