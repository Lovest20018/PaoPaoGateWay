#!/usr/bin/env python3
"""Bounded persisted observations; polling gaps and unknown remote decisions stay explicit."""
from pathlib import Path
from contextlib import contextmanager
import json
import os
import sqlite3
import time

DATABASE = Path('/var/lib/paopaogw/history/observations.sqlite3')
MAX_ROWS = 100000


@contextmanager
def connect(path=DATABASE, readonly=False):
    if readonly:
        db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=3)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        db = sqlite3.connect(path, timeout=3)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('CREATE TABLE IF NOT EXISTS observations (boot TEXT NOT NULL, id TEXT NOT NULL, source TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, domain TEXT, client TEXT, value TEXT NOT NULL, PRIMARY KEY(boot,id,source))')
        db.execute('CREATE INDEX IF NOT EXISTS seen ON observations(last_seen)')
        db.execute('CREATE INDEX IF NOT EXISTS domain ON observations(domain)')
        os.chmod(path, 0o640)
    try:
        with db:
            yield db
    finally:
        db.close()


def store(boot, source, rows, path=DATABASE, retention_days=30, now=None):
    now = time.time() if now is None else now
    with connect(path) as db:
        for row in rows:
            if not isinstance(row, dict) or not row.get('id'): continue
            metadata = row.get('metadata') or {}
            domain = row.get('domain') or metadata.get('host')
            client = row.get('source_address') or metadata.get('sourceIP')
            db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(boot,id,source) DO UPDATE SET last_seen=excluded.last_seen,domain=excluded.domain,client=excluded.client,value=excluded.value',
                       (boot, str(row['id']), source, now, now, domain, client,
                        json.dumps(row, ensure_ascii=False, separators=(',', ':'))))
        db.execute('DELETE FROM observations WHERE last_seen < ?', (now - retention_days * 86400,))
        db.execute('DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations ORDER BY last_seen DESC LIMIT -1 OFFSET ?)', (MAX_ROWS,))


def query(limit=100, before=None, domain=None, client=None, source=None, path=DATABASE):
    if not 1 <= limit <= 500: raise ValueError('INVALID_LIMIT')
    if not path.is_file():
        return {'status': 'no_records', 'items': [], 'next_before': None, 'complete_history': False}
    conditions, args = [], []
    for column, value in (('domain', domain), ('client', client), ('source', source)):
        if value is not None:
            conditions.append(column + '=?'); args.append(value)
    if before is not None:
        conditions.append('rowid < ?'); args.append(int(before))
    sql = ('SELECT rowid,boot,id,source,first_seen,last_seen,value FROM observations' +
           (' WHERE ' + ' AND '.join(conditions) if conditions else '') + ' ORDER BY rowid DESC LIMIT ?')
    with connect(path, True) as db:
        rows = db.execute(sql, args + [limit + 1]).fetchall()
    truncated = len(rows) > limit
    items = [{'boot_id': row[1], 'id': row[2], 'source': row[3], 'first_seen_at': row[4],
              'last_seen_at': row[5], 'record': json.loads(row[6])} for row in rows[:limit]]
    return {'status': 'available' if items else 'no_records', 'items': items,
            'next_before': rows[limit - 1][0] if truncated else None,
            'complete_history': False, 'max_rows': MAX_ROWS,
            'limitations': ['Polling can miss short mihomo connections; absence is not proof of no traffic.',
                            'A missing later snapshot does not prove a connection ended or succeeded.',
                            'Remote proxy DNS and final remote target addresses are not inferred.']}
