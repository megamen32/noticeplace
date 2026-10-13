"""SQLite-compatible local IPC for the real quorum-backed Dqlite store.

No statement or commit is replayed after a lost response. A new business
request reconciles the existing idempotency key/send reservation instead.
"""
from __future__ import annotations

import json
import socket
import sqlite3
import time
import threading
from pathlib import Path
from typing import Any

MAX_FRAME = 16 * 1024 * 1024
ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


class Row:
    """Preserve sqlite3.Row's integer/name access and dict(row) protocol."""
    def __init__(self, columns, values):
        self._columns, self._values = tuple(columns), tuple(values)

    def keys(self):
        return list(self._columns)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        if isinstance(key,str):
            folded=key.translate(ASCII_LOWER)
            for index,column in enumerate(self._columns):
                if column.translate(ASCII_LOWER)==folded:
                    return self._values[index]
        raise IndexError("No item with that key")

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class Cursor:
    def __init__(self, result: dict[str, Any]) -> None:
        self.rowcount = int(result.get("rowcount", -1))
        self.lastrowid = result.get("lastrowid")
        columns = result.get("columns", [])
        self.description = [(name, None, None, None, None, None, None) for name in columns] or None
        self._rows = [Row(columns, values) for values in result.get("rows", [])]
        self._offset = 0

    def fetchone(self) -> Row | None:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self) -> list[Row]:
        rows = self._rows[self._offset:]
        self._offset = len(self._rows)
        return rows

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class Connection:
    """One pinned SQL connection with serialized frames and transaction ownership."""

    row_factory: Any = None

    def __init__(self, socket_path: str, *, readonly: bool = False) -> None:
        if not Path(socket_path).is_absolute():
            raise ValueError("mesh database socket must be absolute")
        self.socket_path = socket_path
        self._io_lock = threading.RLock()
        self._socket: socket.socket | None = None
        self._reader: Any = None
        self._depth = 0
        self._transaction_failed = False
        self.readonly = readonly
        self._readonly_applied = False

    def close(self) -> None:
        with self._io_lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._reader is not None:
            self._reader.close()
        if self._socket is not None:
            self._socket.close()
        self._reader = self._socket = None
        self._readonly_applied = False

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._io_lock:
            return self._request_locked(payload)

    def _request_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(data) > MAX_FRAME:
            raise sqlite3.DataError("bounded SQL request exceeded")
        try:
            if self._socket is None:
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._socket.settimeout(12)
                self._socket.connect(self.socket_path)
                self._reader = self._socket.makefile("rb")
            if self.readonly and not self._readonly_applied:
                self._readonly_applied = True
                self._request({"op": "exec", "sql": "PRAGMA query_only=ON", "params": []})
            self._socket.settimeout(75 if payload.get('op')=='batch' else 12)
            self._socket.sendall(data)
            raw = self._reader.readline(MAX_FRAME + 1)
            if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
                raise OSError("incomplete or oversized SQL response")
            result = json.loads(raw)
        except (OSError, ValueError) as error:
            self.close()
            self._transaction_failed = self._depth > 0
            raise sqlite3.OperationalError("mesh SQL outcome unavailable; request was not replayed") from error
        if not isinstance(result, dict):
            self.close()
            raise sqlite3.OperationalError("invalid mesh SQL response")
        if result.get("error"):
            if result.get("error_class") == "integrity":
                raise sqlite3.IntegrityError(str(result["error"]))
            self.close()
            self._transaction_failed = self._depth > 0
            raise sqlite3.OperationalError(str(result["error"]))
        return result

    def execute(self, statement: str, parameters: Any = ()) -> Cursor:
        with self._io_lock:
            return self._execute_locked(statement, parameters)

    def _execute_locked(self, statement: str, parameters: Any = ()) -> Cursor:
        if self._transaction_failed and self._depth:
            raise sqlite3.OperationalError("mesh transaction lost; cannot continue on another connection")
        word = statement.lstrip().split(None, 1)[0].upper()
        operation = "query" if word in {"SELECT", "PRAGMA", "EXPLAIN", "WITH"} else "exec"
        try:
            return Cursor(self._request({"op": operation, "sql": statement, "params": list(parameters)}))
        except sqlite3.Error as error:
            raise type(error)(f"mesh SQL {operation}/{word}: {error}") from error

    def executemany(self, statement: str, parameters: Any) -> Cursor:
        count = 0
        last = None
        for values in parameters:
            cursor = self.execute(statement, values)
            count += max(0, cursor.rowcount)
            last = cursor.lastrowid
        return Cursor({"rowcount": count, "lastrowid": last})

    def executescript(self, script: str) -> None:
        current = ""
        for character in script:
            current += character
            if character == ";" and sqlite3.complete_statement(current):
                # Journal configuration belongs to Dqlite's replicated VFS,
                # not to an application connection. The core ignores this
                # configuration statement's result on both backends.
                journal_configuration = current.strip().rstrip(";").upper() == "PRAGMA JOURNAL_MODE=WAL"
                if current.strip() and not journal_configuration:
                    self.execute(current)
                current = ""
        if current.strip():
            self.execute(current)

    def __enter__(self):
        self._io_lock.acquire()
        try:
            return self._enter_locked()
        except BaseException:
            self._io_lock.release()
            raise

    def _enter_locked(self):
        if self._depth == 0:
            self._transaction_failed = False
            # BEGIN has no business side effect. A busy writer can be waited
            # for before admission; statements/COMMIT are never retried.
            deadline = time.monotonic() + 8
            while True:
                try:
                    self.execute("BEGIN" if self.readonly else "BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                        raise
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self._exit_locked(exc_type, exc, traceback)
        finally:
            self._io_lock.release()

    def _exit_locked(self, exc_type, exc, traceback):
        self._depth -= 1
        if exc_type is not None:
            self._transaction_failed = True
        if self._depth == 0:
            failed = self._transaction_failed
            self._transaction_failed = False
            if failed:
                # A dropped socket already rolled back the uncommitted
                # connection; opening another connection cannot complete it.
                if self._socket is not None:
                    self.execute("ROLLBACK")
            else:
                self.execute("COMMIT")
        return False

    def commit(self) -> None:
        if self._depth:
            raise sqlite3.OperationalError("explicit commit inside mesh transaction is unsupported")

    def cluster(self) -> dict[str, Any]:
        return self._request({"op": "cluster"})

    def batch(self, statements: list[tuple[str, list[Any]]]) -> Cursor:
        with self._io_lock:
            return self._batch_locked(statements)

    def _batch_locked(self, statements: list[tuple[str, list[Any]]]) -> Cursor:
        if self._depth or self.readonly:
            raise sqlite3.OperationalError("SQL batch requires an independent write connection")
        return Cursor(self._request({"op": "batch", "statements": [
            {"op": "exec", "sql": sql, "params": params} for sql, params in statements]}))


def connect(database: str, *, readonly: bool = False) -> Connection:
    return Connection(database.removeprefix("mesh:"), readonly=readonly)
