#!/usr/bin/env python3
"""Run owner-requested official core maintenance in an independent service."""
from pathlib import Path
import argparse
import json
import time
from mihomo_update import Updater, STATE, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('scheduled', 'check', 'update', 'rollback'))
    args = parser.parse_args()
    updater = Updater()
    try:
        if args.action == 'check':
            updater.check()
        elif args.action == 'rollback':
            updater.rollback()
        elif args.action == 'update':
            updater.update()
        else:
            config = json.loads(Path('/etc/paopaogw/maintenance.json').read_bytes())
            updater.check()
            if config.get('auto_update', True) and updater.status().get('update_available'):
                updater.update()
        atomic_json(STATE / 'operation.json', {'status': 'complete', 'action': args.action, 'completed_at': time.time()})
    except Exception:
        atomic_json(STATE / 'operation.json', {'status': 'failed', 'action': args.action,
                    'message': 'Official core operation failed; previous binary is preserved/restored.', 'completed_at': time.time()})
        raise SystemExit(1)


if __name__ == '__main__':
    main()
