#!/usr/bin/env python3
"""Read-only collection independent of core and HNM lifetimes."""
from pathlib import Path
import json
import time
from gateway_api import CoreClient
from history_store import store


def main():
    client = CoreClient(Path('/var/lib/paopaogw/core-token'))
    while True:
        try:
            config = json.loads(Path('/etc/paopaogw/maintenance.json').read_bytes())
            if config.get('history_enabled', True):
                info = client.request('/sniffbox/info')
                boot = info['integration']['boot_id']
                response = client.request('/sniffbox/evidence', {'resource': 'connections', 'limit': 200})
                if response.get('boot_id') != boot:
                    continue
                store(boot, 'sniffbox_lifecycle', response.get('data', {}).get('items', []),
                      retention_days=max(1, min(365, int(config.get('history_retention_days', 30)))))
                if info.get('mode') in {'yaml', 'suburl'}:
                    connections = client.request('/connections').get('connections', [])
                    # Fields are allowlisted: never persist arbitrary headers, payloads or credentials.
                    rows = []
                    for row in connections:
                        if not isinstance(row, dict): continue
                        metadata = row.get('metadata') or {}
                        rows.append({key: row.get(key) for key in ('id', 'start', 'upload', 'download', 'chains', 'rule', 'rulePayload')} |
                            {'metadata': {key: metadata.get(key) for key in ('network', 'type', 'sourceIP', 'sourcePort', 'destinationIP', 'destinationPort', 'host')},
                             'decision_source': 'mihomo_connection_snapshot', 'remote_target_verified': False})
                    store(boot, 'mihomo_snapshot', rows,
                          retention_days=max(1, min(365, int(config.get('history_retention_days', 30)))))
        except Exception:
            pass
        time.sleep(1)


if __name__ == '__main__':
    main()
