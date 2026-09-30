# Recovery architecture and verified scope

`dbperf/receipts.py` provides a small PostgreSQL transaction-receipt primitive.
It uses durable logged storage, a job UUID, an input SHA-256 and a transaction
advisory lock. Effects and the completion receipt commit together. A retry of
the same job returns its stored result; changed inputs under the same ID fail.

`scripts/test_recovery.py` verified against real PostgreSQL:

- lost client reply after commit: replay does not duplicate effects;
- changed request identity: rejected;
- killed backend during execution: effects and receipt both roll back;
- retry after backend termination: completes successfully.

`DurableQueryFoundryService` now integrates receipts with the unchanged GUI
worker. Expansion effects and their receipt commit in the data database;
the six versions and their second receipt commit in the control database.
There is no distributed transaction. A restart in the gap resumes version
finalization from the data receipt without inserting data again. The request
signature binds recipes, source versions, columns, raw_hash manifest and row
count. The source footprint is rechecked inside the expansion snapshot.
It does not detect falsified raw payloads that retain their old raw_hash.

`reports/durable-recovery.json` verifies real Windows/SSH/Linux expansion:
precommit SQL failure rolls back all output and the receipt; a lost data reply
returns committed results; a new connection resumes pending finalization;
a lost version reply and repeated registration return the same six versions;
reusing the UUID with changed inputs is rejected.

Launch `python launch.py --recovery`. Job IDs are saved before execution in
ignored `.runtime/jobs/*.json`, without credentials. After restart use
`python launch.py --resume-job UUID` and repeat with exactly the same database,
source, schemas, recipes, requester and workstation. Close that GUI after
recovery: the argument fixes one job for the session. Never truncate outputs
between the attempt and its retry. Without --recovery, the measured recipe
optimization and original transactional recovery are used.

Research revision: the data job lock is acquired at session level before
REPEATABLE READ so duplicate waiters see committed receipts without repeating
DML. Session locks are released after commit or when psql exits on an error.
Lock acquisition/release and receipt persistence are timed in fresh GUI runs.
Same-service retries with unchanged signatures reuse their pending UUID;
after recreating the service/process use --resume-job explicitly.

New experimental receipts record counts and two seeded sums of PostgreSQL typed
row hashes for all six destinations at commit. Recovery compares these before
reporting success, detecting tested deleted rows and same-count content changes.
Column names, types, nullability and collations are also bound; a renamed column
is rejected even if the row-hash sums remain unchanged.
The checksum is noncryptographic and bound to PostgreSQL version, encoding and
locale; it is not adversarial integrity proof or cross-version migration support.
Complete dump/restore with fresh table OIDs is tested under the same server version.
Older count-only receipts require explicit independent validation and are refused.
Restoring a database must preserve its owner/permissions as well as table data.

This release retains one data transaction and recovers after a lost
commit response. A precommit failure repeats the full expansion; there are no
chunk checkpoints. Historical recovered timings are diagnostic, excluded from
the current recovery total, and must not be compared as a fresh expansion.
A later chunked design must avoid repeated DISTINCT dimension
rows, preserve source snapshots and natural-key mapping, publish only consistent
outputs, and limit intermediates to one bounded chunk. No unlogged table may
serve as the sole durable checkpoint. Every added lock, receipt, validation,
commit and finalization cost must be counted in the report.

Verified research failpoints: concurrent duplicate request, server restart,
dump restoration and replay, deletion, same-count content change and schema rename.
Remaining: source mutation, full GUI process termination, independent replication
and larger-scale validation of the strengthened fingerprints. The new100k benchmark
is in reports/research-fingerprint-100k/durable-gui/; older pilot numbers do not
measure this implementation.
