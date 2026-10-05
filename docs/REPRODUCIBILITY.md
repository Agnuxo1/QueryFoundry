# Reproduce the pinned QueryFoundry submission

Use the exact commit linked in Kaggle, not a moving branch. The primary profile is payload_once with recovery; lazy_keys is experimental. See WRITEUP.md for scope and unresolved malformed-input differences.

## Windows setup

Use Python 3.13, Windows OpenSSH and Docker Desktop with Linux containers (WSL2). Keep runtime and Docker storage on a drive with sufficient space. The measured server was PostgreSQL 17.11, two CPUs, 2 GiB; infra/Dockerfile pins its base digest. The official generator's seed is unchanged.

```powershell
git clone https://github.com/Agnuxo1/QueryFoundry.git
cd QueryFoundry
git checkout <40-character-submission-commit-from-Kaggle>
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-lock.txt
python scripts/verify_upstream.py
python scripts/download_postgres.py
python scripts/docker_lab.py prepare
python scripts/docker_lab.py build
python scripts/docker_lab.py start
python scripts/docker_lab.py generate --rows 100000
```

The vendor downloader records the binary URL and SHA-256 in ignored .runtime/vendor.json. Preparation generates secrets and a dedicated SSH key in ignored .runtime; never include them in a submission. Start pins the server host key and creates a loopback SSH configuration at port 55222. Generate on a fresh isolated lab; preserve existing official datasets rather than regenerate them accidentally.

## Run the app and benchmark

```powershell
python launch.py --recipe-mode payload_once --recovery
```

Use the organizer GUI to select your SSH/database connection, raw_data source and six official expansion recipes. Follow app/README.md for connection fields. The dedicated lab uses loopback port 55222, SSH user qf and the local key, database user challenge. Secret values remain local. An independently configured Linux SSH/PostgreSQL server can replace Docker for normal operation.

The benchmark configures real GUI widgets and executes their worker against the dedicated Docker lab. It resets the lab's six destination tables between runs; do not use production databases.

```powershell
$env:QF_REPORT_SUBDIR='my-replication'
python scripts/benchmark_gui.py --durable --repeats 3 --compare-mode payload_once --compare-mode lazy_keys --no-resource-monitor
```

This rotates three profiles across three rounds and verifies six-table equivalence after every run. Its metric follows the organizer's MEASURED PROCESSING TOTAL, not end-to-end latency. Records are under reports/my-replication/durable-gui/. Baseline has no receipts; candidates include strengthened recovery. Resource fields are null with observer disabled. Use a new report subdirectory to preserve submitted observations.

## Tests and recovery

```powershell
python -m unittest discover -s tests
cd app
python -m unittest discover -s tests
cd ..
python scripts/verify_upstream.py
python scripts/research_ssh_disconnect.py
```

The final script clones 64 official source rows into disposable data/control databases. It closes actual SSH transports at three transaction boundaries, reconnects with the same UUID, checks outputs/receipts/versions and deletes clones. Reserve the lab exclusively. This is functional recovery evidence, not a throughput benchmark.

For a new client process, retain the UUID under .runtime/jobs and start `python launch.py --recipe-mode payload_once --recovery --resume-job UUID`. Repeat the same source, destinations, connection identity and version/request metadata. Committed outputs must still match. Changed metadata, altered outputs, mismatched restores or damaged receipts require reconciliation; never remove guards to force replay. RECOVERY.md explains these limits.

```powershell
python scripts/docker_lab.py stop
```

Stopping preserves volumes. The separate 1M pilot is historical n=1 evidence, not validation of 300M capacity. Larger replication needs advance disk/RAM planning and independent baselines. No Claude, JEV or remote AI dependency is needed at runtime.
