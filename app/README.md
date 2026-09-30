# PostgreSQL Data Manager

PostgreSQL Data Manager is a desktop application for browsing and administering PostgreSQL databases through an SSH connection. It includes table previews, imports, version history, controlled destructive actions, data-dictionary tooling, dumps, restores, and role management.

## PostgreSQL setup

The application uses two different PostgreSQL objects:

- Control database: `postgres_data_manager` — the application creates it on the first connection when the PostgreSQL role has `CREATEDB`.
- Control schema: `app_control` — the application creates this automatically inside the control database, together with its internal tables.

For automatic setup, grant the PostgreSQL role permission to create databases once:

```sql
ALTER ROLE your_app_role CREATEDB;
```

If automatic creation is not allowed in your environment, run the following manually as a PostgreSQL administrator, replacing `your_app_role` with the PostgreSQL username entered in the connection window:

```sql
CREATE DATABASE postgres_data_manager OWNER your_app_role;
```

If the database already exists under another owner, grant the application role access instead:

```sql
GRANT CONNECT, CREATE ON DATABASE postgres_data_manager TO your_app_role;
```

The role must be able to connect to `postgres_data_manager` and create the `app_control` schema. The same `CREATEDB` permission also enables the optional database-creation button in the application.

The SSH server must provide the PostgreSQL command-line tools used by the application, including `psql`, `pg_dump`, and `pg_restore`.

## Configuration

Defaults can be overridden without changing the source code:

| Environment variable | Default | Purpose |
|---|---|---|
| `PDM_CONTROL_DB` | `postgres_data_manager` | Control database name |
| `PDM_CONTROL_SCHEMA` | `app_control` | Internal control schema |
| `PDM_LEGACY_CONTROL_SCHEMA` | `app_control_legacy` | Optional legacy schema inspected during migration |
| `PDM_REMOTE_STORAGE_DIR` | `postgres_data_manager_storage` | Storage directory under the SSH user's home |
| `PDM_LOCAL_STORAGE_DIR` | `PostgreSQL Data Manager` | Local application-data directory |
| `PDM_APP_TITLE` | `PostgreSQL Data Manager` | Window title |

Changing these variables does not rename existing PostgreSQL objects. Migrate or rename existing databases and schemas before pointing the application at new names.

## Local installation

Python 3.10 or newer is recommended.

```bash
python -m pip install -r requirements.txt
python main.py
```

On Windows, remembered credentials are encrypted for the current Windows user with DPAPI and stored under the configured local application-data directory.

## Synthetic challenge database

[`generate_database.py`](./generate_database.py) creates the deterministic `kaggle_challenge` database used to exercise the Manager with synthetic data. See [GENERATE_DATABASE_README.md](./GENERATE_DATABASE_README.md) for the Linux server setup, SSH tunnel command, resume behavior, generated metadata, and the six expansion recipes in [`SQL files`](./SQL%20files/).

## Expansion performance report

After a successful cross-table **Expand** operation, the application displays a detailed English-language performance report. It includes preparation and preflight work, destination locks, source-manifest scanning, every destination statement, affected-row counts, version registration, commits, cache invalidation, and interface refresh.

Local work is measured with per-thread CPU time. Remote command work is clocked on the Linux server, and expansion statements are clocked inside PostgreSQL. SSH connection and result-return transit are therefore excluded from the measured processing total. The report can be exported as a UTF-8 `.txt` file.

## Main dependencies

- CustomTkinter
- Paramiko
- pandas
- ReportLab
- openpyxl and xlrd for Excel imports

## License

Licensed under the [Apache License 2.0](./LICENSE).
