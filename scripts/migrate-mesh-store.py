#!/usr/bin/env python3
"""Import one frozen real SQLite backup, preserving rowids and uncertain work.

Run only while all live writers/dispatchers are quiesced. Exact batch receipts
share their SQL transaction: after a lost response inspect the receipt, never
restart the import with another identity. This tool sends no notifications.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notification_center.mesh_store import Connection


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def if_missing(schema):
    return re.sub(r'^(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER)\s+)(?!\s*IF\b)', r'\1IF NOT EXISTS ', schema, count=1, flags=re.IGNORECASE)


def batches(cursor):
    rows, size = [], 0
    for row in cursor:
        encoded = json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > 8 * 1024 * 1024:
            raise RuntimeError('one source row exceeds the bounded migration frame')
        if rows and (len(rows) == 512 or size + len(encoded) > 8 * 1024 * 1024):
            yield rows
            rows, size = [], 0
        rows.append(row)
        size += len(encoded)
    if rows:
        yield rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--socket', required=True)
    ap.add_argument('--receipt', required=True)
    args = ap.parse_args()
    source_path = Path(args.source).resolve(strict=True)
    # A stable frozen backup is the import identity, not the mutable live file.
    hasher = hashlib.sha256()
    with source_path.open('rb') as input_file:
        while block := input_file.read(8 * 1024 * 1024):
            hasher.update(block)
    source_hash = hasher.hexdigest()
    source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
    destination = Connection(args.socket)
    tables = source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    with destination:
        destination.execute("CREATE TABLE IF NOT EXISTS mesh_import_batches(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)")
    existing = destination.execute("SELECT identity FROM mesh_import_batches WHERE identity LIKE ? LIMIT 1", (source_hash+':%',)).fetchone()
    foreign = destination.execute("SELECT identity FROM mesh_import_batches WHERE identity NOT LIKE ? LIMIT 1", (source_hash+':%',)).fetchone()
    if foreign:
        raise RuntimeError('existing import belongs to another frozen source; refusing a second identity')
    if not existing:
        known = destination.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='mesh_import_batches'").fetchall()
        if known:
            raise RuntimeError('destination already contains business tables; reconcile before importing')
        with destination:
            destination.execute('INSERT INTO mesh_import_batches(identity,digest,rows) VALUES(?,?,?)',(source_hash+':start',source_hash,0))
    started = time.monotonic()
    counts = {}
    with destination:
        for name, schema in tables:
            destination.execute(if_missing(schema))
    schemas = dict(tables)
    dependencies = {name: {row[2] for row in source.execute('PRAGMA foreign_key_list('+quote(name)+')') if row[2] != name} for name in schemas}
    ordered, completed = [], set()
    while pending := set(schemas) - completed:
        ready = sorted(name for name in pending if dependencies[name] <= completed)
        if not ready:
            raise RuntimeError('cyclic source foreign keys require an explicit import plan')
        ordered.extend(ready)
        completed.update(ready)
    for name in ordered:
        columns = [row[1] for row in source.execute('PRAGMA table_info('+quote(name)+')')]
        selected = ['rowid', *columns]
        sql = 'INSERT INTO '+quote(name)+'('+','.join(quote(c) for c in selected)+') VALUES('+','.join('?' for _ in selected)+')'
        cursor = source.execute('SELECT '+','.join(quote(c) for c in selected)+' FROM '+quote(name)+' ORDER BY rowid')
        count = 0
        for rows in batches(cursor):
            if any(isinstance(v, bytes) for row in rows for v in row):
                raise RuntimeError('binary column requires an explicit lossless codec; import refused')
            identity = f'{source_hash}:{name}:{rows[-1][0]}'
            digest = hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            previous = destination.execute('SELECT digest,rows FROM mesh_import_batches WHERE identity=?',(identity,)).fetchone()
            if previous:
                if previous['digest'] != digest or previous['rows'] != len(rows):
                    raise RuntimeError('existing batch identity conflicts with frozen source')
            else:
                statements = [(sql,list(row)) for row in rows]
                statements.append(('INSERT INTO mesh_import_batches(identity,digest,rows) VALUES(?,?,?)',[identity,digest,len(rows)]))
                # No automatic retry follows a lost COMMIT response. A later
                # explicit resume reads this same canonical batch receipt.
                destination.batch(statements)
            count += len(rows)
        actual = destination.execute('SELECT COUNT(*) AS count FROM '+quote(name)).fetchone()['count']
        if actual != count:
            raise RuntimeError('imported row count does not match frozen source')
        counts[name] = count
        print(json.dumps({'table':name,'rows':count}), flush=True)
    objects = source.execute("SELECT sql FROM sqlite_master WHERE type IN ('index','trigger') AND sql IS NOT NULL ORDER BY name").fetchall()
    with destination:
        for (schema,) in objects:
            destination.execute(if_missing(schema))
    result = {'status':'imported','source_sha256':source_hash,'tables':counts,'seconds':round(time.monotonic()-started,3),'external_actions':0}
    output = Path(args.receipt)
    output.write_text(json.dumps(result,indent=2)+'\n')
    output.chmod(0o600)
    print(json.dumps({'status':result['status'],'tables':len(counts),'seconds':result['seconds']}))
    destination.close()
    source.close()


if __name__ == '__main__':
    main()
