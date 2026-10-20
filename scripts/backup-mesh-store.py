#!/usr/bin/env python3
"""Attempt a consistent Notice Dqlite export with canonical dqlite-utils.

When the pinned tool supports a writable SQLite destination, this creates a
snapshot from one Dqlite node's Raft-backed state. It never copies the node's
Raft files and never mutates the source node. Failed exports publish no files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile


DQLITE_UTILS_VERSION = "0.2.2"
DQLITE_UTILS_SOURCE = "https://github.com/canonical/dqlite-utils"
DQLITE_UTILS_COMMIT = "543cf331fda11ce73e58355a47ba65a4df9585ea"
NOTICE_DATABASE = "noticeplace"
OUTPUT_SCHEMA = "noticeplace.dqlite-sql-backup.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def publish_artifact_pair(snapshot: Path, manifest_tmp: Path, output: Path, receipt: Path) -> None:
    """Publish both frozen files without replacing existing paths."""
    try:
        os.link(snapshot, output)
    except FileExistsError:
        raise FileExistsError("refusing to overwrite an artifact or manifest")
    try:
        os.link(manifest_tmp, receipt)
    except BaseException as publication_error:
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        except OSError as rollback_error:
            raise RuntimeError(
                f"manifest publication failed ({publication_error!r}); "
                f"artifact rollback failed ({rollback_error!r}); inspect {output}"
            ) from publication_error
        if isinstance(publication_error, FileExistsError):
            raise FileExistsError("refusing to overwrite an artifact or manifest") from publication_error
        raise
    directory_fd = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, help="Dqlite node state directory")
    parser.add_argument("--output", required=True, help="new private frozen SQLite artifact path")
    parser.add_argument("--receipt", required=True, help="new private JSON manifest path")
    parser.add_argument("--dqlite-utils", default="dqlite-utils", help="canonical dqlite-utils v0.2.2 executable")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    state_dir = Path(args.state_dir).resolve(strict=True)
    if not state_dir.is_dir():
        raise RuntimeError("--state-dir must name a Dqlite node directory")
    tool = shutil.which(args.dqlite_utils)
    if not tool:
        raise RuntimeError("dqlite-utils executable was not found")
    tool = str(Path(tool).resolve(strict=True))
    version = subprocess.run([tool, "--version"], check=True, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    if not re.fullmatch(r"dqlite_utils 0\.2\.2", version):
        raise RuntimeError(f"unsupported dqlite-utils version: {version!r}; require 0.2.2")

    output_arg = Path(args.output).expanduser()
    receipt_arg = Path(args.receipt).expanduser()
    output_parent = output_arg.parent.resolve(strict=True)
    receipt_parent = receipt_arg.parent.resolve(strict=True)
    if output_parent != receipt_parent:
        raise RuntimeError("artifact and manifest must share one existing directory")
    output = output_parent / output_arg.name
    receipt = receipt_parent / receipt_arg.name
    if output == receipt:
        raise RuntimeError("artifact and manifest paths must differ")
    if output.exists() or receipt.exists():
        raise FileExistsError("refusing to overwrite an artifact or manifest")

    with tempfile.TemporaryDirectory(prefix=".noticeplace-dqlite-export-", dir=output_parent) as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        # dqlite-utils registers the read-only Dqlite VFS as its default for
        # the source. Select SQLite's already-registered writable Unix VFS
        # explicitly for VACUUM INTO's destination, leaving source selection
        # and consistency semantics unchanged.
        destination_uri = snapshot.as_uri() + "?vfs=unix"
        sql = f".open; VACUUM {NOTICE_DATABASE} INTO {sql_string(destination_uri)}"
        exported = subprocess.run([tool, "--dir", str(state_dir), "-c", sql], check=False,
                                  capture_output=True, text=True, timeout=args.timeout)
        if exported.returncode:
            raise RuntimeError(f"dqlite-utils snapshot command failed: {exported.stderr[-3000:]}")
        if not snapshot.is_file() or snapshot.stat().st_size == 0:
            raise RuntimeError("dqlite-utils completed without producing a non-empty snapshot")
        os.chmod(snapshot, 0o600)
        with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True) as db:
            check = db.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise RuntimeError("exported SQLite snapshot failed PRAGMA quick_check")
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            if not tables:
                raise RuntimeError("exported snapshot contains no Notice tables")
            counts = {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                      for name in tables}

        manifest = {
            "schema": OUTPUT_SCHEMA,
            "snapshot_file": output.name,
            "snapshot_sha256": sha256_file(snapshot),
            "snapshot_bytes": snapshot.stat().st_size,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dqlite_utils_version": DQLITE_UTILS_VERSION,
            "dqlite_utils_source": DQLITE_UTILS_SOURCE,
            "dqlite_utils_commit": DQLITE_UTILS_COMMIT,
            "snapshot_method": ".open latest; VACUUM noticeplace INTO file:<private-absolute-path>?vfs=unix",
            "database": NOTICE_DATABASE,
            "table_rows": counts,
            "external_actions": 0,
        }
        manifest_tmp = Path(tmp) / "manifest.json"
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        os.chmod(manifest_tmp, 0o600)
        for path in (snapshot, manifest_tmp):
            with path.open("rb") as stream:
                os.fsync(stream.fileno())

        # Hard-link publication is atomic per path and fails rather than
        # replacing a path created by another operator after the initial check.
        publish_artifact_pair(snapshot, manifest_tmp, output, receipt)

    print(json.dumps({"status": "backed_up", "snapshot_sha256": manifest["snapshot_sha256"],
                      "snapshot_bytes": manifest["snapshot_bytes"], "tables": len(manifest["table_rows"]),
                      "external_actions": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
