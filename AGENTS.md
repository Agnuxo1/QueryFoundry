# QueryFoundry development constraints

Work directly; no subagents unless the user explicitly requests them.
Read docs/STATUS.md and .cognition/checkpoint.md if it exists when resuming.
For team work, read coordinacion/TABLON.md, AGENDA.md and TAREAS.md first.
Claim task ownership and reserve shared database compute before execution.
Record ideas and evidence in coordinacion/THINKTANK.md and the research queue.
Do not modify app/generate_database.py, its fixed seed, or official benchmark input.
Verify app/ with scripts/verify_upstream.py. Extend behavior under dbperf/.
Consult JEV for substantial decisions using D:/PROJECTS/.cognition/jev-workflow.md.
Keep databases, downloads, caches and experiments on D: or E:.
Preserve the existing Docker VHDX disks on E:. Never unregister or reset them.
Use a disposable benchmark database and never erase a remote database implicitly.
Every performance claim must identify input rows, environment, measured scope,
repeats, correctness checks and unavailable resource measurements.
Do not present SQL diagnostic timings as the official GUI metric.
Preserve source/destination guards, manifests, row counters, version/dump behavior.
Do not enable experimental recovery in the GUI before cross-database version
finalization and failpoints are verified. Do not publish a competitive result
without a public exact commit and an independently reproducible full benchmark.
