# Disposable Dqlite backup and isolated restore proof

This procedure and integration check use only disposable local Notice clusters.
They do not authorize or perform production backup, restore, migration,
activation, deployment, sending, or replay.

## Supported export and restore route

The source export uses Canonical `dqlite-utils` v0.2.2. Its `.open` command
opens the selected Dqlite point-in-time through the tool's read-only `dqlite`
VFS. SQLite's `VACUUM INTO` writes to a different VFS selected explicitly by an
absolute file URI:

```sql
.open; VACUUM noticeplace INTO 'file:/absolute/private/path/snapshot.db?vfs=unix'
```

`vfs=unix` selects SQLite's normal registered Unix VFS for the destination;
the Dqlite source remains opened through the canonical point-in-time VFS. URI
support is enabled by the pinned rusqlite default `OpenFlags` (including
`SQLITE_OPEN_URI`), and this build uses `bundled-full`; no undocumented build
flag or tool/module patch is used. See SQLite's [`VACUUM INTO` URI behavior](https://www.sqlite.org/lang_vacuum.html)
and [URI `vfs=NAME` parameter](https://www.sqlite.org/uri.html). SQLite also
warns that `VACUUM` can change hidden rowids when a table lacks an
`INTEGER PRIMARY KEY` alias. The fixture deliberately creates a gap in
`events.rowid` and the readback compares every table's rowids as well as rows;
the pinned disposable run retained that gapped rowid set. Notice uses hidden
rowids as ordering tie-breakers in several queries, including event history in
`notification_center/core.py::_source_health_recovery_matches`. Therefore a
rowid mismatch fails the chain with the exact table and source/target rowids;
this fixture result does not establish a general SQLite guarantee.

The exporter writes into a mode-0700 private temporary directory, checks
`PRAGMA quick_check`, records table counts and SHA-256 in a private manifest,
and fsyncs both files. It publishes each final name with a non-replacing hard
link. If manifest publication fails after the artifact link succeeds, it rolls
back the artifact link and re-raises the original error. If rollback itself
fails, it raises an explicit ambiguous-publication error naming the artifact;
the operator must inspect that path before retrying. A deterministic fault
injection test covers a generic `EIO` on the second hard link and confirms no
final paths remain and unrelated files are preserved.

The isolated restore uses the existing `scripts/migrate-mesh-store.py` import
path with `--restore-snapshot` against a separately started empty three-voter
Dqlite cluster. The source includes nontrivial historical `mesh_import_batches`
and prior `mesh_restore_batches_*` rows; they are copied as source data, while
the importer creates a separate current control table derived from the full
snapshot SHA-256. The checker runs the same frozen source twice to prove
same-SHA resume, then reads every source business and historical ledger table
from each target voter and compares exact rows and rowids. It separately checks
the complete SHA-derived control-ledger rowset across all voters before and
after retry, plus fixture event/delivery IDs and `queued`/`sending`/`uncertain`
states. No provider or delivery worker runs.

## Reproduce

Build the Notice mesh binary with the repository's pinned script and prepare
the locally scoped pinned Canonical exporter as described in the task record.
Then run:

```bash
PYTHONDONTWRITEBYTECODE=1 \
NOTICEPLACE_MESH_STORE_BINARY="$PWD/.tmp/mesh-build/noticeplace-mesh-store" \
NOTICEPLACE_DQLITE_UTILS="$PWD/.tmp/noticeplace-mesh-backup-restore/cargo-target/release/dqlite-utils" \
python3 -m unittest discover -s tests -p test_mesh_backup_restore.py -v
```

The tests inject a second-hardlink failure for rollback behavior and start a
real disposable three-voter source cluster. The chain creates accepted fixture
incidents, a deliberate hidden `events.rowid` gap, historical migration/restore
ledger rows, and `queued`, `sending`, and `uncertain` delivery rows. It then
exports a snapshot, imports it into a separate empty three-voter target with
`--restore-snapshot`, repeats the same-SHA import, and checks exact readback on
all voters. It asserts that the uncertain effect remains uncertain and never
invokes a provider. All fixture clusters, sockets, certificates, snapshots,
and manifests are removed on exit. The command is not suitable for production
paths or sockets.

## Limits and independent restore-lineage work

The composed disposable test now seeds both historical ledger forms and uses
the importer owner's frozen `--restore-snapshot` mode. The importer itself and
its new test remain outside this lane and were not edited. The test verifies
the historical ledger rows and rowids remain source data, and that the current
restore's `mesh_restore_batches_<full-source-SHA>` completion record is kept
separate. This does not prove a production restore or change default legacy
migration behavior.

No activation gate, live source snapshot, production restoration, runtime
change, or task acceptance is proven here. Do not rerun a production
`migrate-mesh-store.py` invocation as part of routine code deployment.
