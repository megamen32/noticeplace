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

LEGACY_CONTROL_TABLE = 'mesh_import_batches'
RESTORE_CONTROL_PREFIX = 'mesh_restore_batches_'


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


def frozen_source_sha256(source_path):
    hasher = hashlib.sha256()
    with source_path.open('rb') as input_file:
        while block := input_file.read(8 * 1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def user_tables(connection):
    return {str(row['name']) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}


def create_control_table(connection, name):
    connection.execute(
        'CREATE TABLE IF NOT EXISTS ' + quote(name) +
        '(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--socket', required=True)
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--restore-snapshot', action='store_true',
                    help='restore a frozen snapshot using a source-specific control ledger')
    args = ap.parse_args()
    source_path = Path(args.source).resolve(strict=True)
    # A stable frozen backup is the import identity, not the mutable live file.
    source_hash = frozen_source_sha256(source_path)
    source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
    tables = source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    source_tables = {str(name) for name, _schema in tables}
    control_table = (RESTORE_CONTROL_PREFIX + source_hash
                     if args.restore_snapshot else LEGACY_CONTROL_TABLE)
    if args.restore_snapshot and control_table in source_tables:
        raise RuntimeError('snapshot contains its own snapshot restore control table; refusing recursive restore')

    destination = Connection(args.socket)
    if args.restore_snapshot:
        names = user_tables(destination)
        other_restore_controls = {name for name in names
                                  if name.startswith(RESTORE_CONTROL_PREFIX)
                                  and name != control_table and name not in source_tables}
        if other_restore_controls:
            raise RuntimeError('destination already contains another snapshot restore lineage')
        has_control = control_table in names
        unrelated = names - source_tables - {control_table}
        if unrelated:
            raise RuntimeError('destination already contains data/control state outside this snapshot')
        if not has_control and names:
            raise RuntimeError('destination already contains data/control state without this snapshot lineage')
        with destination:
            create_control_table(destination, control_table)
        existing = destination.execute(
            'SELECT identity FROM ' + quote(control_table) + ' WHERE identity LIKE ? LIMIT 1',
            (source_hash + ':%',),
        ).fetchone()
        foreign = destination.execute(
            'SELECT identity FROM ' + quote(control_table) + ' WHERE identity NOT LIKE ? LIMIT 1',
            (source_hash + ':%',),
        ).fetchone()
        if foreign:
            raise RuntimeError('snapshot restore control ledger contains a foreign source identity')
        if not existing:
            known = user_tables(destination) - {control_table}
            if known:
                raise RuntimeError('snapshot restore target has data without a matching source receipt')
            with destination:
                destination.execute(
                    'INSERT INTO ' + quote(control_table) + '(identity,digest,rows) VALUES(?,?,?)',
                    (source_hash + ':start', source_hash, 0),
                )
    else:
        with destination:
            destination.execute("CREATE TABLE IF NOT EXISTS mesh_import_batches(identity TEXT PRIMARY KEY,digest TEXT NOT NULL,rows INTEGER NOT NULL)")
        existing = destination.execute("SELECT identity FROM mesh_import_batches WHERE identity LIKE ? LIMIT 1", (source_hash+':%',)).fetchone()
        foreign = destination.execute("SELECT identity FROM mesh_import_batches WHERE identity NOT LIKE ? LIMIT 1", (source_hash+':%',)).fetchone()
        if foreign:
            raise RuntimeError('existing import belongs to another frozen source; refusing a second identity')
        if not existing:
            known = destination.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='mesh_import_batches'"
            ).fetchall()
            if known:
                raise RuntimeError('destination already contains business tables; reconcile before importing')
            with destination:
                destination.execute(
                    'INSERT INTO mesh_import_batches(identity,digest,rows) VALUES(?,?,?)',
                    (source_hash + ':start', source_hash, 0),
                )
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
            previous = destination.execute(
                'SELECT digest,rows FROM ' + quote(control_table) + ' WHERE identity=?', (identity,)
            ).fetchone()
            if previous:
                if previous['digest'] != digest or previous['rows'] != len(rows):
                    raise RuntimeError('existing batch identity conflicts with frozen source')
            else:
                statements = [(sql,list(row)) for row in rows]
                statements.append((
                    'INSERT INTO ' + quote(control_table) + '(identity,digest,rows) VALUES(?,?,?)',
                    [identity, digest, len(rows)],
                ))
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
    for (schema,) in objects:
        # Building a production index can exceed the ordinary query window.
        # Use the bounded import transaction window for each individual DDL;
        # a lost response still stops, and explicit resume reconciles IF NOT
        # EXISTS against the same frozen source instead of replaying effects.
        destination.batch([(if_missing(schema), [])])
    completion_digest=hashlib.sha256(json.dumps(counts,sort_keys=True).encode()).hexdigest()
    completion=source_hash+':complete'
    previous = destination.execute(
        'SELECT digest,rows FROM ' + quote(control_table) + ' WHERE identity=?', (completion,)
    ).fetchone()
    if previous:
        if previous['digest']!=completion_digest or previous['rows']!=sum(counts.values()):
            raise RuntimeError('completed migration receipt disagrees with the frozen source')
    else:
        with destination:
            destination.execute(
                'INSERT INTO ' + quote(control_table) + '(identity,digest,rows) VALUES(?,?,?)',
                (completion, completion_digest, sum(counts.values())),
            )
    result = {'status':'imported','source_sha256':source_hash,'tables':counts,'seconds':round(time.monotonic()-started,3),'external_actions':0}
    if args.restore_snapshot:
        result.update({'mode': 'snapshot_restore', 'control_table': control_table})
    output = Path(args.receipt)
    output.write_text(json.dumps(result,indent=2)+'\n')
    output.chmod(0o600)
    print(json.dumps({'status':result['status'],'tables':len(counts),'seconds':result['seconds']}))
    destination.close()
    source.close()


if __name__ == '__main__':
    main()
