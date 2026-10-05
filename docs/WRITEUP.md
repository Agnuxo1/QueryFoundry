# QueryFoundry: Verifiable JSONB Expansion and Durable SSH Recovery

QueryFoundry extends the organizer's Windows GUI, preserving all 32 official files byte-for-byte. It reduces repeated JSONB extraction and adds optional transactional recovery across the data and version-control databases. The primary entry is `payload_once` with `--recovery`; `lazy_keys` remains an explicitly experimental opt-in profile.

Public source: https://github.com/Agnuxo1/QueryFoundry (Apache-2.0). The Kaggle submission pins the exact 40-character Git commit and includes a source archive from that commit. This report and all raw observations are included in the archive.

## Design and preserved behavior

The unchanged GUI worker runs on Windows and reaches PostgreSQL on Linux through real SSH. The six organizer recipes execute in order `table3, table4, table1, table5, table6, table2`. `payload_once` reuses `raw->'values'` through a LATERAL expression with an OFFSET 0 barrier; it retains original casts, filters, joins and validation. Rewrites apply only to the six recognized organizer recipes; custom SQL is preserved.

Expansion remains one REPEATABLE READ transaction. Preflight checks, source/version manifests, destination checks, row counts, control-version registration and GUI refreshes remain in the measured workflow. Neither the official generator nor its fixed seed is changed. SHA-256 integrity checks verify all 32 organizer files, including inside the release ZIP.

Recovery stores a receipt atomically with data effects and a separate receipt atomically with the six control-version records. A session advisory lock is acquired before the snapshot, preventing concurrent duplicate UUIDs. Resuming the same request and UUID verifies committed outputs before replaying the receipt, then completes version registration. There is no distributed transaction and no block checkpointing: a failure before the data commit repeats the full expansion.

## Final measured series: 5 October 2026

100,000 official generated rows; three rotating rounds per variant; all nine runs retained; Windows GUI worker with real widgets (window hidden for automation), actual SSH, Linux PostgreSQL 17.11, two CPUs and a 2 GiB container limit. Every run inserted 532,416 destination rows and passed bidirectional EXCEPT ALL multiset comparison on all six tables, normalizing surrogate keys while preserving relationships.

| Variant | Median MEASURED PROCESSING TOTAL | Runs | Local reduction versus original |
|---|---:|---:|---:|
| Original, without recovery | 16.045867 s | 3 | reference |
| payload_once, with strengthened recovery | 15.447215 s | 3 | 3.73% |
| lazy_keys, with strengthened recovery (experimental) | 12.792425 s | 3 | 20.28% |

Evidence: `reports/release-20261005/durable-gui/` includes individual JSON/TXT reports, run order, source hashes and aggregates. All nine runtime-source fingerprints match the release code. Records identify base commit 55f26f8 plus explicit hashes for benchmark metadata changes; those hashes pin the actual measured sources. The packaging commit adds documentation and evidence without changing the measured runtime.

The metric is the organizer's MEASURED PROCESSING TOTAL, including its remote-server and local-CPU components. It is not end-to-end stopwatch latency: SSH transit and blocking time excluded by the organizer's instrumentation remain excluded. Recovery and verification costs participate in the candidate workflow. The resource observer was disabled in this final series, so memory and disk peaks are unavailable, not zero. Small sample sizes, overlapping timing ranges and a single host do not establish statistical significance or general speedup.

## Larger pilot and experimental profile

The earlier 1,000,000-row three-way pilot ran once per variant: original 164.109 s, payload_once 152.427 s, lazy_keys 126.772 s. All six tables matched (5,321,553 destination rows). Sampled container working sets were 1.779 / 1.763 / 1.826 GiB respectively. These are historical n=1 observations, not repeated final-release measurements. Evidence: `reports/research-lazy-1m/`.

Lazy extraction targets the dominant table2 stage. Linux differential testing matched 800 lazy_keys and 150 payload_once cases against the original; a deliberately rejected rewrite provided a positive detector control. Independent Windows planner testing added 1,600 lazy_keys cases across 16 settings and 300 payload_once controls. The 940 lazy cases with a single error source matched exactly. Multiple simultaneous invalid cells exposed six message differences, including two SQLSTATE differences still unresolved. Consequently lazy_keys is not the default, and we do not claim universal error equivalence. Windows JIT settings did not test actual JIT; real Linux parallel/JIT coverage remains future work. See `coordinacion/entregas/I-005b-lazy-keys-plans-claude.md` and raw artifacts.

The earlier sparse_positions optimization was rejected because two of eight malformed-input cases changed an error into acceptance. It is not used by the entry.

## Recovery validation

Real Linux gates covered simultaneous requests, database restart, dump/restore with changed OIDs, deletion, same-count content edits, column rename and version races. An independent Windows replica used a separate local PostgreSQL installation; it is complementary evidence rather than a substitute for Linux/SSH.

The final functional gate closed the actual Paramiko SSH transport before the data commit, after the data commit, and after the control commit. Each case recovered over a new connection with the same UUID, equivalent six-table outputs, exactly one data receipt, one control receipt, six version records and no residual advisory locks. Tests used disposable 64-row clones, removed afterward, and verified the official source was unchanged. Evidence: `reports/ssh-disconnect-final.json` and `scripts/research_ssh_disconnect.py`.

The first SSH experiment failed when PostgreSQL underwent SIGPIPE-triggered crash recovery and the observer queried too soon. The failed attempt is retained in `reports/ssh-disconnect-attempt-1.json`. Only observer and cleanup probes received bounded recovery waits; the service does not blindly retry data writes. Direct GUI window termination was not separately tested.

## Resource measurement and boundaries

The disk sampler was calibrated against 14,647,296 bytes of disposable TEMP/TOAST/index/tablespace storage and a 9,235,232-byte held sort spill. The eight-run observer-on/off control retained a 91.441 s outlier and did not establish a fixed or zero observer cost. Its observed ephemeral-file maximum was 156.445 MiB in that separate 100k experiment. Non-atomic sampling may miss peaks or combine noncoexistent states; it is not a guaranteed global peak or lower bound. WAL, persistent receipts and client staging are outside that sampler. See `docs/DISK-MEASUREMENT.md` and calibration/overhead reports.

Receipt fingerprints use two seeded record-hash sums, counts, schema and PostgreSQL/encoding/locale metadata. They are not cryptographic and do not support automatic cross-version migration. Source manifests validate stored raw_hash values: editing JSON while preserving raw_hash is a documented detection gap. Restores must preserve matching database/request identity and both databases; missing receipt constraints or changed version metadata require independent reconciliation. No automatic bypass is offered.

No run validates 300 million source rows. There is no organizer score, established significance or claimed rank. Future work includes larger paired tests, source-content verification cost, Linux parallel/JIT malformed-input tests and durable block checkpoints; none is represented as implemented.

## Reproduction and deliverables

Follow `docs/REPRODUCIBILITY.md` from the pinned submission commit. Dependencies are locked, the Linux base image is digest-pinned, the official generator and manifest are preserved, and local credentials/runtime/datasets are excluded from Git and the source ZIP. Normal operation needs Windows, Python, SSH and PostgreSQL; no Claude, JEV or remote AI service is a runtime dependency.

Deliverables: source ZIP, exact public commit, technical report, reproduction guide, unit tests, original-file manifest, nine final GUI records, larger historical pilot, independent audits, negative experiments and actual SSH recovery evidence. Kaggle publication state is recorded separately after formal submission; repository publication alone is not a contest submission.
