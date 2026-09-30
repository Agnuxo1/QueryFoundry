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

This release retains one data transaction and recovers after a lost
commit response. A precommit failure repeats the full expansion; there are no
chunk checkpoints. Historical recovered timings are diagnostic, excluded from
the current recovery total, and must not be compared as a fresh expansion.
A later chunked design must avoid repeated DISTINCT dimension
rows, preserve source snapshots and natural-key mapping, publish only consistent
outputs, and limit intermediates to one bounded chunk. No unlogged table may
serve as the sole durable checkpoint. Every added lock, receipt, validation,
commit and finalization cost must be counted in the report.

Remaining failpoints: concurrent duplicate request, source mutation, server
restart, process termination of the full GUI, dump restoration and replay.
