#!/usr/bin/env python3
"""Versioned, read-only HNM facade for the local sniffbox management API.

Python 3.11+, standard library only. Never proxies a caller-selected API path.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
from pathlib import Path
import socket
import re
import threading
import time
from typing import Any
from staged_probe import StagedProbe
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
import uuid


MAX_CORE_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 4096
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CONNECTIONS = 200
MAX_AGE = 25
READ_PATHS = {
    "/api/v1/instance", "/api/v1/capabilities", "/api/v1/health",
    "/api/v1/runtime", "/api/v1/metrics", "/api/v1/connections",
    "/api/v1/fakeip/lookup", "/api/v1/decisions",
    "/api/v1/history", "/api/v1/mihomo/connections",
}
PROBE_PATH = "/api/v1/diagnostics/probe"


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_probe_url(value: str) -> bool:
    """An exact public HTTP(S) URL; sniffbox also checks its strict profile."""
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        p = urlsplit(value)
        if (p.scheme not in {"http", "https"} or not p.hostname or p.username
                or p.password or p.fragment or p.port not in {None, 80, 443}
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)
                or "\\" in value or "%" in p.netloc):
            return False
        try:
            return ipaddress.ip_address(p.hostname).is_global
        except ValueError:
            host = p.hostname.lower().rstrip(".")
            return ("." in host and not host.endswith((".local", ".localhost", ".internal"))
                    and all(c.isascii() and (c.isalnum() or c in ".-") for c in host))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Settings:
    instance_id: str
    read_token: str
    version: str = "development"
    build_commit: str = "unknown"
    source_sha256: str | None = None
    name: str = "PaoPaoGateway"
    listen: str = "127.0.0.1"
    port: int = 8788
    probe_urls: tuple[str, ...] = ()

    def __post_init__(self):
        uuid.UUID(self.instance_id)
        if len(self.read_token) < 32 or len(self.read_token) > 256:
            raise ValueError("read-token must contain 32..256 characters")
        if not self.read_token.isascii() or any(c.isspace() for c in self.read_token):
            raise ValueError("read-token must be printable ASCII without whitespace")
        if any(ord(c) < 33 or ord(c) > 126 for c in self.read_token):
            raise ValueError("invalid token characters")
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 100:
            raise ValueError("invalid instance name")
        ipaddress.ip_address(self.listen)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("invalid API port")
        if len(self.probe_urls) > 16 or not all(valid_probe_url(u) for u in self.probe_urls):
            raise ValueError("probe_urls must contain at most 16 public HTTP(S) URLs")


class CoreClient:
    """Only code-owned loopback paths are passed to this client."""
    def __init__(self, token_file: Path, base_url: str = "http://127.0.0.1:80"):
        self.token_file, self.base_url = token_file, base_url
        # A host HTTP_PROXY must never receive the core credential.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path: str, body: dict | None = None, timeout: float = 3) -> dict:
        try:
            token = self.token_file.read_text(encoding="ascii").strip()
            if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
                raise ValueError("invalid core credential")
            req = Request(self.base_url + path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={"Authorization": "Bearer " + token,
                                   "Content-Type": "application/json"})
            with self.opener.open(req, timeout=timeout) as res:
                raw = res.read(MAX_CORE_BYTES + 1)
                if len(raw) > MAX_CORE_BYTES:
                    raise ApiError(502, "CORE_RESPONSE_TOO_LARGE")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("core object expected")
                return result
        except ApiError:
            raise
        except (OSError, ValueError, HTTPError, URLError):
            # Never expose a URL, upstream body, exception text or credential.
            raise ApiError(503, "CORE_UNAVAILABLE") from None


def project(value: Any, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    return {k: value[k] for k in keys if k in value}


def metric_number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


class Application:
    def __init__(self, settings: Settings, core: CoreClient, probe_runner=None):
        self.settings, self.core = settings, core
        self.probes = threading.BoundedSemaphore(2)
        self.probe_runner = probe_runner
        self.last_probe = None
        self.probe_lock = threading.Lock()

    def envelope(self, data: dict, sampled_at: float | None = None) -> dict:
        return {"api_version": "1", "instance_id": self.settings.instance_id,
                "fetched_at": time.time(), "sampled_at": sampled_at, "data": data}

    def capabilities(self, runtime: dict) -> dict:
        mode = runtime['mode']
        evidence = runtime['evidence_schema'] == 1
        decision = evidence and mode in {'free', 'socks5', 'ovpn'}
        return {"api_version": "1", "instance_id": self.settings.instance_id,
                "access_role": "observer",
                "permissions": {"observe": True, "diagnose": bool(self.settings.probe_urls),
                                "configure": False, "maintain": False},
                "features": {"runtime": True, "metrics": True, "connections": True,
                             "diagnostics_probe": bool(self.settings.probe_urls),
                             "fakeip_lookup": evidence, "decision_history": decision,
                             "connection_history": evidence, "persistent_history": True,
                             "mihomo_connection_snapshots": mode in {'yaml', 'suburl'}, "configuration_write": False},
                "feature_states": {"fakeip_lookup": "available" if evidence else "unsupported",
                    "decisions": "mode_dispatch_only" if decision else ("unavailable" if evidence else "unsupported"),
                    "connection_history": "tcp_after_sniff" if evidence else "unsupported", "probe_address_pinning": "unsupported",
                    "probe_selected_outbound": "unsupported", "probe_udp_quic": "unsupported",
                    "probe_forced_ipv6": "unsupported",
                    "probe_stage_timings": "available" if self.probe_runner else "unsupported"},
                "mode": mode, "supported_modes": ["free", "socks5", "yaml", "suburl", "ovpn"],
                "address_families": {"observation": ["ipv4", "ipv6"], "probe": ["auto"]},
                "probe_policy": "exact_url_allowlist", "probe_method": "HEAD",
                "probe_source": "gateway_local_proxy", "follows_redirects": False,
                "protocols": ["http", "https"], "config_revision": runtime['config_revision'],
                "status": runtime['status'], "freshness": runtime['freshness'],
                "probe_urls": list(self.settings.probe_urls),
                "limits": {"connections": MAX_CONNECTIONS, "probe_timeout_ms": 10000,
                           "probe_concurrency": 2, "history_capacity": 1024,
                           "history_retention_seconds": 300}}

    def runtime(self) -> dict:
        v = self.core.request("/sniffbox/info")
        sampled = time.time()
        timestamp = v.get("timestamp")
        age = (max(0, sampled - timestamp) if type(timestamp) in (int, float) and math.isfinite(timestamp)
               and 0 <= timestamp <= sampled + 5 else None)
        identity = v.get('integration')
        if (not isinstance(identity, dict) or identity.get('schema_version') != 1
                or not isinstance(identity.get('boot_id'), str)
                or not isinstance(identity.get('config_revision'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', identity.get('config_revision', ''))
                or not isinstance(v.get('mode'), str)
                or v.get('mode') not in {'free', 'socks5', 'yaml', 'suburl', 'ovpn'}):
            raise ApiError(502, 'INCOMPATIBLE_CORE')
        try:
            uuid.UUID(identity['boot_id'])
        except ValueError:
            raise ApiError(502, 'INCOMPATIBLE_CORE') from None
        runtime = {"status": "stale" if age is None or age > MAX_AGE else "available",
                "core_responding": True, "outbound_reachable": "unknown",
                "forwarding_verified": False, "source": "sniffbox",
                "boot_id": identity['boot_id'], "config_revision": identity['config_revision'],
                "configuration": project(identity, ('configuration_state', 'loaded_at',
                    'last_load_result', 'revision_scope', 'reload_policy')),
                "core_version": identity.get('core_version'),
                "build_commit": identity.get('build_commit'),
                "evidence_schema": identity.get('evidence_schema', 0),
                "outbound": {"mode": v['mode'],
                    "selected_id": {'free': 'direct', 'socks5': 'socks5-upstream', 'ovpn': 'openvpn-tunnel'}.get(v['mode']),
                    "reachability": "unknown",
                    "component_state": ('not_required' if v['mode'] == 'free' else
                        ('running' if metric_number(project(v.get('memory'), ('clash_uptime', 'ovpn_uptime')).get(
                            'ovpn_uptime' if v['mode'] == 'ovpn' else 'clash_uptime')) is not None
                         else 'unknown'))},
                "trusted_dns": project(project(v.get('dns'), ('trusted',)).get('trusted'), ('ip', 'port')),
                "source_sampled_at": timestamp if age is not None else None,
                "freshness": {"age_seconds": age, "max_age_seconds": 25,
                              "stale": age is None or age > 25},
                "mode": v.get("mode"), "version": v.get("version"),
                "uptime_seconds": v.get("uptime"),
                "fakeip": project(v.get("fakeip"), ("cidr", "mapped")),
                "features": project(v.get("features"), ("net_rec", "openport", "udp_enable")),
                "connections": project(v.get("connections"), ("active", "tcp", "udp", "clients")),
                "traffic": project(v.get("traffic"), ("downloadTotal", "uploadTotal")),
                "memory": project(v.get("memory"), ("total", "available", "sniffbox_rss"))}
        with self.probe_lock:
            last = self.last_probe
            if last and last['boot_id'] == identity['boot_id'] and last['config_revision'] == identity['config_revision']:
                runtime['last_probe'] = {**last, 'age_seconds': max(0, sampled - last['sampled_at']),
                                       'stale': sampled - last['sampled_at'] > MAX_AGE}
            else:
                runtime['last_probe'] = None
        if isinstance(identity.get('mihomo_configuration'), dict):
            runtime['mihomo_configuration'] = project(identity['mihomo_configuration'], (
                'status', 'source_revision', 'policy_revision', 'effective_revision', 'loaded_at', 'revision_scope'))
        return runtime

    def dispatch(self, method: str, target: str, raw: bytes = b"") -> dict:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ApiError(400, "INVALID_TARGET")
        path = parsed.path
        if path not in READ_PATHS and path != PROBE_PATH:
            raise ApiError(404, "NOT_FOUND")
        if method != ("POST" if path == PROBE_PATH else "GET"):
            raise ApiError(405, "METHOD_NOT_ALLOWED")
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=10)
        if parsed.query and path not in {"/api/v1/connections", "/api/v1/fakeip/lookup", "/api/v1/decisions", "/api/v1/history", "/api/v1/mihomo/connections"}:
            raise ApiError(400, "INVALID_QUERY")
        if path in {'/api/v1/history', '/api/v1/mihomo/connections'}:
            if set(query) - {'limit', 'before', 'domain', 'client', 'source'} or any(len(value) != 1 for value in query.values()):
                raise ApiError(400, 'INVALID_QUERY')
            try:
                from history_store import query as history_query
                limit = int(query.get('limit', ['100'])[0])
                if not 1 <= limit <= 500: raise ValueError()
                if path.endswith('/connections'):
                    if set(query) - {'limit'}: raise ValueError()
                    runtime = self.runtime()
                    if runtime['mode'] not in {'yaml', 'suburl'}:
                        raise ApiError(409, 'MIHOMO_NOT_ACTIVE')
                    result = self.core.request('/connections')
                    rows = result.get('connections', [])
                    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                        raise ApiError(502, 'INVALID_CORE_RESPONSE')
                    items = []
                    for row in rows[:limit]:
                        item = project(row, ('id', 'start', 'upload', 'download', 'chains', 'rule', 'rulePayload'))
                        item['metadata'] = project(row.get('metadata'), ('network', 'type', 'sourceIP', 'sourcePort', 'destinationIP', 'destinationPort', 'host'))
                        item['decision_status'] = 'observed_snapshot' if item.get('chains') or item.get('rule') else 'unknown'
                        items.append(item)
                    return self.envelope({'items': items, 'source': 'mihomo_connection_snapshot', 'complete_history': False,
                        'source_age_seconds': None, 'remote_target_verified': False,
                        'limitations': ['Mihomo can cache snapshots; an empty snapshot does not prove no traffic.']})
                before = query.get('before', [None])[0]
                if before is not None and not 0 < int(before) < 2**63: raise ValueError()
                source = query.get('source', [None])[0]
                if source not in {None, 'mihomo_snapshot', 'sniffbox_lifecycle'}: raise ValueError()
                domain = query.get('domain', [None])[0]
                if domain is not None:
                    domain = domain.lower().rstrip('.')
                    if not domain or len(domain) > 253 or not re.fullmatch(r'[a-z0-9._-]+', domain): raise ValueError()
                client = query.get('client', [None])[0]
                if client is not None: client = str(ipaddress.ip_address(client))
                result = history_query(limit, before, domain, client, source)
                return self.envelope(result)
            except (ValueError, OverflowError):
                raise ApiError(400, 'INVALID_QUERY') from None
        if path == "/api/v1/instance":
            runtime = self.runtime()
            return self.envelope({"id": self.settings.instance_id, "kind": "paopaogateway",
                                  "edition": "debian", "name": self.settings.name,
                                  "version": self.settings.version,
                                  "build_commit": self.settings.build_commit,
                                  "source_sha256": self.settings.source_sha256,
                                  "core_version": runtime['core_version'],
                                  "boot_id": runtime['boot_id'],
                                  "config_revision": runtime['config_revision']}, runtime['source_sampled_at'])
        if path == "/api/v1/capabilities":
            runtime = self.runtime()
            return self.envelope(self.capabilities(runtime), runtime['source_sampled_at'])
        if path in {'/api/v1/fakeip/lookup', '/api/v1/decisions'}:
            return self.evidence(path, query)
        if path in {"/api/v1/runtime", "/api/v1/health", "/api/v1/metrics"}:
            runtime = self.runtime()
            if path == "/api/v1/health":
                data = project(runtime, ("core_responding", "outbound_reachable", "forwarding_verified",
                                         "freshness", "source_sampled_at", "status", "boot_id", "config_revision"))
            elif path == "/api/v1/metrics":
                data = {"source": "sniffbox", "source_sampled_at": runtime["source_sampled_at"],
                        "freshness": runtime["freshness"], "metrics": [
                            {"name": name, "unit": "bytes", "sampled_at": runtime['source_sampled_at'],
                             "value": metric_number(runtime["traffic"].get(key)),
                             "available": metric_number(runtime["traffic"].get(key)) is not None,
                             "kind": "counter", "reset": "core_restart", "boot_id": runtime['boot_id']}
                            for name, key in (("download_total", "downloadTotal"),
                                              ("upload_total", "uploadTotal"))]}
                for name, unit, source, key in (
                    ('active_connections', 'connections', 'connections', 'active'),
                    ('memory_total', 'bytes', 'memory', 'total'),
                    ('memory_available', 'bytes', 'memory', 'available'),
                    ('core_memory_used', 'bytes', 'memory', 'sniffbox_rss')):
                    value = metric_number(runtime[source].get(key))
                    data['metrics'].append({'name': name, 'unit': unit, 'kind': 'gauge',
                        'value': value, 'available': value is not None,
                        'sampled_at': runtime['source_sampled_at'], 'reset': None, 'boot_id': runtime['boot_id']})
            else:
                data = runtime
            return self.envelope(data, runtime['source_sampled_at'])
        if path == "/api/v1/connections":
            if query.get('view', ['snapshot']) != ['snapshot']:
                return self.evidence(path, query)
            if set(query) - {"limit", "offset", "domain", "client", "since", "view"} or any(len(v) != 1 for v in query.values()):
                raise ApiError(400, "INVALID_QUERY")
            try:
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get('offset', ['0'])[0])
            except ValueError:
                raise ApiError(400, "INVALID_LIMIT") from None
            if not 1 <= limit <= MAX_CONNECTIONS or not 0 <= offset <= 10000:
                raise ApiError(400, "INVALID_LIMIT")
            source = self.core.request("/connections")
            rows = source.get("connections")
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ApiError(502, "INVALID_CORE_RESPONSE")
            # The upstream has a current, potentially cached snapshot, not an event history.
            domain = query.get('domain', [''])[0].lower().rstrip('.')
            client = query.get('client', [''])[0]
            if domain and (len(domain) > 253 or not re.fullmatch(r'[a-z0-9._-]+', domain)):
                raise ApiError(400, 'INVALID_DOMAIN')
            if client:
                try:
                    client = str(ipaddress.ip_address(client))
                except ValueError:
                    raise ApiError(400, 'INVALID_CLIENT') from None
            if 'since' in query:
                raise ApiError(501, 'TIME_FILTER_UNSUPPORTED')
            filtered = [r for r in rows if
                (not domain or r.get('metadata', {}).get('host', '').lower().rstrip('.') == domain) and
                (not client or r.get('metadata', {}).get('sourceIP') == client)]
            items = []
            for row in filtered[offset:offset + limit]:
                item = project(row, ("id", "start", "upload", "download"))
                item["metadata"] = project(row.get("metadata"), ("network", "type", "sourceIP",
                    "sourcePort", "destinationIP", "destinationPort", "host"))
                item.update(ended_at=None, last_observed_at=None, state='snapshot',
                    actual_target_address=None, outbound_id=None,
                    source_identity='observed_address_only',
                    decision={'status': 'unavailable', 'reason': 'not_instrumented'})
                items.append(item)
            return self.envelope({"source": "sniffbox_connection_snapshot", "items": items,
                "status": 'available' if items else 'no_records',
                "total": len(filtered), "truncated": offset + limit < len(filtered),
                "offset": offset, "next_offset": offset + limit if offset + limit < len(filtered) else None,
                "pagination": 'best_effort_live_offset',
                "source_sampled_at": None, "source_age_seconds": None,
                "freshness_known": False, "complete_history": False,
                "limitations": ["Upstream may cache connections; source age is unavailable.",
                                "Empty rule/chains mean unknown; absence does not prove no traffic."]})
        return self.probe(raw)

    def evidence(self, path: str, query: dict) -> dict:
        runtime = self.runtime()
        if runtime['evidence_schema'] != 1:
            raise ApiError(501, 'UNSUPPORTED')
        if any(len(v) != 1 for v in query.values()):
            raise ApiError(400, 'INVALID_QUERY')
        values = {k: v[0] for k, v in query.items()}
        if path.endswith('fakeip/lookup'):
            if set(values) not in ({'domain'}, {'ip'}):
                raise ApiError(400, 'INVALID_QUERY')
            payload = {'resource': 'fakeip', **values}
            if 'ip' in values:
                try:
                    payload['ip'] = str(ipaddress.IPv4Address(values['ip']))
                except ValueError:
                    raise ApiError(400, 'INVALID_IP') from None
        else:
            if set(values) - {'view', 'limit', 'offset', 'domain', 'client', 'since', 'until', 'cursor'}:
                raise ApiError(400, 'INVALID_QUERY')
            view = values.get('view', 'recent')
            if view not in {'recent', 'observed_active'} or (view == 'observed_active' and 'cursor' in values):
                raise ApiError(400, 'INVALID_VIEW')
            try:
                limit = int(values.get('limit', '100'))
                offset = int(values.get('offset', '0'))
                if not 1 <= limit <= 200 or not 0 <= offset <= 10000:
                    raise ValueError()
                payload = {'resource': 'decisions' if path.endswith('decisions') else 'connections',
                           'active': view == 'observed_active', 'limit': limit, 'offset': offset}
                for key in ('since', 'until'):
                    if key in values:
                        value = float(values[key])
                        if not math.isfinite(value) or not 0 <= value <= 32503680000:
                            raise ValueError()
                        payload[key] = value
                if 'since' in payload and 'until' in payload and payload['since'] > payload['until']:
                    raise ValueError()
            except ValueError:
                raise ApiError(400, 'INVALID_QUERY') from None
            for key in ('domain', 'client'):
                if key in values:
                    payload[key] = values[key]
            if 'client' in payload:
                try:
                    payload['client'] = str(ipaddress.ip_address(payload['client']))
                except ValueError:
                    raise ApiError(400, 'INVALID_CLIENT') from None
        if 'domain' in payload:
            domain = payload['domain'].lower().rstrip('.')
            if not domain or len(domain) > 253 or not re.fullmatch(r'[a-z0-9._-]+', domain):
                raise ApiError(400, 'INVALID_DOMAIN')
            payload['domain'] = domain
        # Cursor is scoped to core boot, endpoint and normalized filters, never to a configuration write.
        context = {k: v for k, v in payload.items() if k not in {'limit', 'offset'}}
        def cursor_signature(boot, sequence):
            message = json.dumps([boot, sequence, context], sort_keys=True, separators=(',', ':'))
            return hmac.new(self.settings.read_token.encode(), message.encode(), hashlib.sha256).hexdigest()[:24]
        if 'cursor' in values:
            if 'offset' in values:
                raise ApiError(400, 'CURSOR_OFFSET_CONFLICT')
            parts = values['cursor'].split(':')
            if len(parts) != 3 or not parts[1].isdigit() or len(parts[1]) > 20:
                raise ApiError(400, 'INVALID_CURSOR')
            boot, sequence, signature = parts
            if not hmac.compare_digest(signature, cursor_signature(boot, int(sequence))):
                raise ApiError(400, 'INVALID_CURSOR')
            if boot != runtime['boot_id']:
                raise ApiError(409, 'CURSOR_EXPIRED')
            payload['after'] = int(sequence)
        result = self.core.request('/sniffbox/evidence', payload)
        code = result.get('error')
        if code:
            raise ApiError(501 if code == 'UNSUPPORTED' else 400,
                           code if code in {'UNSUPPORTED', 'INVALID_QUERY', 'INVALID_DOMAIN'} else 'INVALID_CORE_RESPONSE')
        sampled = result.get('sampled_at')
        if (type(sampled) not in (int, float) or not math.isfinite(sampled)
                or sampled < 0 or sampled > time.time() + 5
                or not isinstance(result.get('data'), dict)):
            raise ApiError(502, 'INVALID_CORE_RESPONSE')
        if result.get('boot_id') != runtime['boot_id']:
            raise ApiError(409, 'CORE_CHANGED')
        data = result['data']
        if payload['resource'] == 'fakeip':
            data = project(data, ('status', 'source', 'domain', 'fakeip', 'cidr', 'dns_ttl_seconds',
                'valid_until', 'validity', 'algorithm_prediction', 'historical_ownership_verified'))
        else:
            data = project(data, ('status', 'items', 'total', 'truncated', 'next_sequence', 'oldest_sequence',
                'latest_sequence', 'gap', 'discarded_records', 'capacity', 'retention_seconds',
                'complete_history', 'scope', 'view'))
            if not isinstance(data.get('items'), list) or len(data['items']) > payload['limit']:
                raise ApiError(502, 'INVALID_CORE_RESPONSE')
            projected = []
            for row in data['items']:
                if not isinstance(row, dict):
                    raise ApiError(502, 'INVALID_CORE_RESPONSE')
                item = project(row, ('id', 'started_at', 'ended_at', 'last_observed_at', 'state', 'sequence',
                    'transport', 'inbound', 'source_address', 'source_port', 'original_destination',
                    'destination_port', 'domain', 'domain_source', 'actual_target_address', 'upstream_peer',
                    'upload', 'download'))
                item['resolution'] = project(row.get('resolution'), ('source', 'server', 'domain',
                    'selected_address', 'complete_answers'))
                item['decision'] = project(row.get('decision'), ('status', 'source', 'rule_id', 'rule_kind',
                    'reason', 'outbound_id', 'fallback', 'config_revision', 'decided_at', 'connect_result',
                    'failure_stage', 'application_result'))
                projected.append(item)
            data['items'] = projected
            sequence = data.get('next_sequence')
            if type(sequence) is not int or sequence < payload.get('after', 0):
                raise ApiError(409, 'CURSOR_EXPIRED')
            if not payload['active']:
                data['next_cursor'] = f"{runtime['boot_id']}:{sequence}:{cursor_signature(runtime['boot_id'], sequence)}"
        data.update(boot_id=runtime['boot_id'], source_sampled_at=sampled,
                    freshness={'age_seconds': max(0, time.time() - sampled), 'max_age_seconds': 25,
                               'stale': time.time() - sampled > 25})
        return self.envelope(data, sampled)

    def probe(self, raw: bytes) -> dict:
        try:
            request = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ApiError(400, "INVALID_JSON") from None
        if (not isinstance(request, dict) or set(request) - {"url", "timeout_ms", "correlation_id", "address_family", "outbound_id"}
                or not isinstance(request.get("url"), str)):
            raise ApiError(400, "INVALID_PROBE")
        if request["url"] not in self.settings.probe_urls:
            raise ApiError(403, "TARGET_NOT_ALLOWED")
        if request.get('address_family', 'auto') != 'auto' or request.get('outbound_id') is not None:
            raise ApiError(501, 'UNSUPPORTED')
        correlation = request.get('correlation_id')
        if correlation is not None and (not isinstance(correlation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', correlation)):
            raise ApiError(400, 'INVALID_CORRELATION_ID')
        timeout = request.get("timeout_ms", 5000)
        if type(timeout) is not int or not 1000 <= timeout <= 10000:
            raise ApiError(400, "INVALID_TIMEOUT")
        if not self.probes.acquire(blocking=False):
            raise ApiError(429, "PROBE_BUSY")
        try:
            before = self.runtime()
            if before['status'] == 'stale':
                raise ApiError(503, 'STALE_DATA')
            if self.probe_runner:
                result = self.probe_runner(request['url'], timeout)
            else:
                result = self.core.request("/sniffbox/probe", {
                    "url": request["url"], "method": "HEAD", "follow": False,
                    "timeoutMs": timeout, "maxBody": 1,
                }, timeout=timeout / 1000 + 2)
            after = self.runtime()
            changed = any(before[k] != after[k] for k in ('boot_id', 'config_revision'))
            status = result.get('status')
            http_completed = result.get('ok') is True and type(status) is int and 100 <= status <= 599
            connection = None
            if self.probe_runner and before['evidence_schema'] == 1 and not changed:
                source_port = result.get('local_source_port')
                # Exact same loopback socket port, domain, and current boot. Time alone is not a join.
                for active in (False, True):
                    try:
                        evidence = self.core.request('/sniffbox/evidence', {
                            'resource': 'connections', 'domain': urlsplit(request['url']).hostname,
                            'client': '127.0.0.1', 'active': active, 'limit': 200})
                        if evidence.get('boot_id') == before['boot_id']:
                            candidates = [row for row in evidence.get('data', {}).get('items', [])
                                if row.get('source_port') == source_port and row.get('inbound') == 'healthcheck']
                            if len(candidates) == 1:
                                connection = candidates[0]
                                break
                    except ApiError:
                        pass
            stages = result.get('stages', {"tcp": "unknown", "tls": "unknown",
                                          "http": 'completed' if http_completed else 'unknown'})
            failure = result.get('failure_stage', None if http_completed and status < 400 else ('http' if http_completed else 'unknown'))
            if connection:
                connect_result = connection.get('decision', {}).get('connect_result')
                if connect_result in {'connected', 'failed'}:
                    stages['tcp'] = connect_result
                if connect_result == 'failed':
                    failure = 'outbound_connect'
            target_address = connection.get('actual_target_address') if connection else None
            resolution = connection.get('resolution', {}) if connection else {}
            data = {"probe_id": str(uuid.uuid4()), "connection_id": connection.get('id') if connection else None,
                "correlation_id": correlation, "config_revision": before['config_revision'],
                "boot_id": before['boot_id'], "configuration_changed": changed,
                "status": 'unknown' if changed else ('passed' if http_completed and 200 <= status < 400 else 'failed'),
                "probe_source": "gateway_local_proxy", "url": request["url"],
                "method": "HEAD", "follow_redirects": False,
                "result": project(result, ("ok", "denied", "status", "ms")),
                "failure_stage": failure,
                "dns": {"status": 'selected_address_observed' if resolution.get('selected_address') else 'unknown',
                    "source": resolution.get('source', 'unknown'), "server": resolution.get('server'),
                    "addresses": [resolution['selected_address']] if resolution.get('selected_address') else None,
                    "complete_answers": False},
                "connected_address": target_address,
                "outbound_id": connection.get('decision', {}).get('outbound_id') if connection else None,
                "stages": stages, "timings_ms": result.get('timings_ms', {}),
                "timed_out": result.get('timed_out', False),
                "client_path_verified": False,
                "limitations": ["This probes the gateway outbound, not an end device's path.",
                    "Complete DNS answers and remote proxy node/target addresses may be unavailable."]}
            sampled = time.time()
            with self.probe_lock:
                self.last_probe = {'sampled_at': sampled, 'boot_id': before['boot_id'],
                    'config_revision': before['config_revision'], 'status': data['status'],
                    'probe_id': data['probe_id']}
            return self.envelope(data, sampled)
        finally:
            self.probes.release()


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, application: Application):
        self.application = application
        self.slots = threading.BoundedSemaphore(8)
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "PaoPaoGateway-API/1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)
        self.request_id = str(uuid.uuid4())

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': {'code': 'INVALID_HTTP_REQUEST'}})

    def log_message(self, format, *args):
        # Requests can contain credentials or a malicious URL. Log status only.
        pass

    def handle_api(self):
        app = self.server.application
        incoming = self.headers.get_all('X-Request-ID', [])
        self.request_id = (incoming[0] if len(incoming) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{8,64}', incoming[0]) else str(uuid.uuid4()))
        try:
            headers = self.headers.get_all("Authorization", [])
            expected = "Bearer " + app.settings.read_token
            if (len(headers) != 1 or not hmac.compare_digest(
                    headers[0].encode("utf-8"), expected.encode("ascii"))):
                raise ApiError(401, "UNAUTHORIZED")
            if len(self.path) > 4096 or self.headers.get("Transfer-Encoding"):
                raise ApiError(400, "INVALID_REQUEST")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1:
                raise ApiError(400, "INVALID_LENGTH")
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                raise ApiError(400, "INVALID_LENGTH") from None
            if length < 0 or length > MAX_BODY_BYTES:
                raise ApiError(413, "BODY_TOO_LARGE")
            if self.command != "POST" and length:
                raise ApiError(400, "UNEXPECTED_BODY")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ApiError(400, "INCOMPLETE_BODY")
            data = app.dispatch(self.command, self.path, raw)
            self.respond(200, data)
        except ApiError as error:
            self.respond(error.status, {"api_version": "1", "instance_id": app.settings.instance_id,
                                       "error": {"code": error.code}})
        except (ValueError, UnicodeError):
            self.respond(400, {"error": {"code": "INVALID_REQUEST"}})
        except (TimeoutError, ConnectionError, OSError):
            self.close_connection = True
        except Exception:
            self.respond(503, {'error': {'code': 'UNAVAILABLE'}})

    def respond(self, status: int, data: dict):
        data['request_id'] = self.request_id
        data.setdefault('api_version', '1')
        data.setdefault('instance_id', self.server.application.settings.instance_id)
        data.setdefault('fetched_at', time.time())
        data.setdefault('sampled_at', None)
        raw = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_RESPONSE_BYTES:
            status = 502
            raw = json.dumps({'api_version': '1', 'request_id': self.request_id,
                'instance_id': self.server.application.settings.instance_id,
                'fetched_at': time.time(), 'sampled_at': None,
                'error': {'code': 'RESPONSE_TOO_LARGE'}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header('X-Request-ID', self.request_id)
        self.send_header("Connection", "close")
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_TRACE = do_CONNECT = handle_api


def load_settings(path: Path) -> Settings:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) - {"name", "listen", "port", "probe_urls"}:
        raise ValueError("unknown API setting")
    urls = config.pop("probe_urls", [])
    if not isinstance(urls, list):
        raise ValueError("probe_urls must be an array")
    build = json.loads(Path("/usr/local/lib/paopaogw/build.json").read_text())
    return Settings(instance_id=(path.parent / "instance-id").read_text().strip(),
                    read_token=(path.parent / "read-token").read_text().strip(),
                    version=build["version"], build_commit=build['build_commit'],
                    source_sha256=build.get('source_sha256'), probe_urls=tuple(urls), **config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/paopaogw/api.json"))
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        server = ApiServer((settings.listen, settings.port), Application(settings,
            CoreClient(Path("/var/lib/paopaogw/core-token")), StagedProbe()))
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "Invalid API configuration or unreadable credentials.\n")
    print(f"PaoPaoGateway API v1 listening on {settings.listen}:{settings.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
