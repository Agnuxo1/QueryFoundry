# Generate the PostgreSQL expansion challenge database

`generate_database.py` creates a standalone, deterministic PostgreSQL database
named `kaggle_challenge`. It uses generic names and synthetic values and does not
import or execute the DB Manager application.

The intended setup is:

- The generator and DB Manager run on Windows.
- PostgreSQL and SSH run on a separate Linux server.
- The generator can open its own SSH tunnel to PostgreSQL on Linux.
- The DB Manager connects to the same server over SSH and administers the
  generated database with the same PostgreSQL role.

## Requirements

- Python 3.10 or newer.
- PostgreSQL and its `psql` command-line client.
- Windows OpenSSH Client (`ssh`) when using the automatic SSH tunnel.
- A dedicated PostgreSQL role allowed to create databases. Use the same role in
  the generator and in the DB Manager connection window.
- Authentication supplied by `PG_KAGGLE_CHALLENGE_PASS`, `--user_pass`,
  `.pgpass`, or another libpq mechanism.

The generator invokes `psql` without an interactive password prompt. A password
received through `--user_pass` is forwarded to `psql` through the child process's
private `PGPASSWORD` environment and is never stored in the generated database.
The generator deliberately ignores an inherited `PGPASSWORD` to prevent a
generic variable from silently selecting the wrong credential. On a shared
machine, prefer `.pgpass` or `PG_KAGGLE_CHALLENGE_PASS`, because command-line
arguments may remain visible in shell history and process listings.

## SSH port and PostgreSQL port are different

- SSH normally listens on TCP port `22`. The DB Manager uses this port to log in
  to Linux and run administrative commands.
- PostgreSQL normally listens on TCP port `5432`. `psql` and the generator use
  this port to speak the PostgreSQL protocol.

Do not pass `22` as `--postgres_port`: an SSH server cannot process PostgreSQL
traffic. Instead, pass `--ssh_port <linux_ssh_port>` (commonly `22`). The
generator then starts OpenSSH, creates a temporary local forwarding port
internally, runs the complete database generation through it, and closes the
tunnel automatically.

## Prepare a small Linux PostgreSQL server

Install PostgreSQL and an SSH server using your Linux distribution's packages.
Create a dedicated PostgreSQL role; do not use the `postgres` superuser for the
regular generator/Manager workflow:

```sql
CREATE ROLE <postgres_user>
    WITH LOGIN CREATEDB
    PASSWORD '<strong_postgres_password>';
```

For the recommended automatic SSH tunnel, PostgreSQL may keep its normal
`localhost` binding. Locate the active authentication file with `SHOW hba_file;`
and ensure that the dedicated role can connect from Linux loopback:

```conf
# pg_hba.conf
host    all    <postgres_user>    127.0.0.1/32    scram-sha-256
```

Only the configured SSH TCP port (`<linux_ssh_port>`) needs to be reachable from
Windows. PostgreSQL port `5432` does not need to be exposed in the Linux or cloud
firewall.

If you intentionally prefer a direct PostgreSQL connection without SSH, configure
PostgreSQL to listen on the server address:

```conf
# postgresql.conf
listen_addresses = 'localhost,<linux_server_ip>'
```

Also add a restricted `pg_hba.conf` rule for `<windows_client_ip>/32`, restart
PostgreSQL, and allow port `5432` only from that Windows address.

## Generate the database from Windows

Install PostgreSQL client tools on Windows and make sure `psql` is in `PATH`.
Replace every value inside angle brackets before running the command. In
PowerShell:

```powershell
$env:PG_KAGGLE_CHALLENGE_PASS = "<strong_postgres_password>"
python generate_database.py `
  --rows <row_num> `
  --host <linux_server_ip> `
  --ssh_port <linux_ssh_port> `
  --ssh_user <linux_ssh_user> `
  --postgres_port 5432 `
  --user <postgres_user> `
  --admin_database postgres
```

For example, `<row_num>` may be `10000000`; the accepted range is 1 through
999,000,000. Do not type the angle brackets in the final command. `--host` and
`--user` are required unless `PGHOST` and `PGUSER` are already configured,
preventing accidental generation on a local PostgreSQL instance or as the
`postgres` superuser.

`--host` remains the Linux server address. `--ssh_port <linux_ssh_port>` tells
the generator to open the tunnel itself; use `22` only when that is the SSH port
configured on the Linux server. `--postgres_port 5432` is the PostgreSQL port as
seen from inside Linux. The user does not need to open another PowerShell window
or replace the host with a Windows/loopback address.

The SSH and PostgreSQL accounts and passwords are independent:

- `<linux_ssh_user>` logs in to Linux. There is intentionally no SSH password
  argument or environment variable. If the server uses password authentication,
  OpenSSH asks for it interactively in the terminal and does not display the
  typed characters. With an SSH key or `ssh-agent`, no password prompt may be
  needed. On the first connection, OpenSSH may also ask for host-key confirmation.
- `<postgres_user>` owns the database. Its password is read from
  `PG_KAGGLE_CHALLENGE_PASS` and forwarded only to the child `psql` processes.

### Optional direct PostgreSQL connection

If port `5432` is deliberately exposed only to the Windows client, omit the SSH
options:

```powershell
$env:PG_KAGGLE_CHALLENGE_PASS = "<strong_postgres_password>"
python generate_database.py `
  --rows <row_num> `
  --host <linux_server_ip> `
  --postgres_port 5432 `
  --user <postgres_user> `
  --admin_database postgres
```

### Why `--admin_database postgres`?

`admin_database` is an existing maintenance database used only as the connection
context for checking, creating, replacing, or resuming `kaggle_challenge`.
PostgreSQL cannot create a database while connected to that not-yet-existing
database, and it cannot drop the database currently in use. The standard
`postgres` database is therefore the recommended value. It does not receive the
generated rows and it does not grant administrative privileges; permissions come
from `<postgres_user>`.

The libpq variables `PGHOST`, `PGPORT`, `PGUSER`, and `PGDATABASE` are honored.
All connection settings can also be supplied explicitly. Both underscore and
hyphen spellings are accepted for multiword options, for example
`--ssh_port`/`--ssh-port`, `--ssh_user`/`--ssh-user`,
`--postgres_port`/`--postgres-port`, `--admin_database`/`--admin-database`, and
`--user_pass`/`--user-pass`. The legacy `--port` alias remains accepted for the
PostgreSQL port.

To remove the password from the current PowerShell session afterward:

```powershell
Remove-Item Env:PG_KAGGLE_CHALLENGE_PASS
```

The generator refuses to overwrite an existing `kaggle_challenge` database.
For an intentional clean regeneration, add `--replace`.

## Manage the generated database from Windows

After generation, open PostgreSQL Data Manager on Windows and connect with:

- SSH address: `<linux_server_ip>`
- SSH port: `<linux_ssh_port>`
- SSH credentials: a Linux account allowed to access the server
- PostgreSQL port: `5432` unless PostgreSQL is configured on another port
- PostgreSQL username: the same `<postgres_user>` used by the generator
- PostgreSQL password: `<strong_postgres_password>`

The Manager connects over SSH and runs PostgreSQL tools on the Linux server. The
generator ensures the Manager control database/schema exists and registers the
Raw counter there. The Manager will then list `kaggle_challenge` as an
administrable database. The generated database remains on Linux; only the
graphical Manager runs on Windows.

## Determinism across dataset sizes

Every record is a pure deterministic function of the fixed seed and `raw_id`.
The first 100,000 records generated with `--rows 100000` are therefore byte-for-byte
the same records as raw IDs 1 through 100,000 in runs with one million, ten million,
or one hundred million rows.

To make that guarantee possible, categorical profiles use a deterministic draw per
`raw_id`. Their proportions converge to the published profile as the dataset grows;
the generator does not force exact category counts for every possible value of
`--rows`, because doing that would make earlier records depend on the final size.

## Chunk commits and resume

Raw data is committed in independent transactions of 500,000 rows by default. Set
a different size with `--chunk_rows`, for example:

```powershell
python generate_database.py --rows <row_num> --chunk_rows 250000 --host <linux_server_ip> --ssh_port <linux_ssh_port> --ssh_user <linux_ssh_user> --postgres_port 5432 --user <postgres_user> --admin_database postgres
```

During an incomplete load, the temporary table
`challenge_metadata.generation_progress` records the last committed `raw_id`,
generator version, fixed seed, requested row count, and chunk size. If generation
is interrupted, rerun the same command with `--resume` instead of `--replace`:

```powershell
python generate_database.py --rows <row_num> --resume --host <linux_server_ip> --ssh_port <linux_ssh_port> --ssh_user <linux_ssh_user> --postgres_port 5432 --user <postgres_user> --admin_database postgres
```

Resume validates the seed, generator version, and requested row count before it
writes anything. A different `--chunk_rows` may be used after resuming. The chunk
that was active when a failure occurred is rolled back; all earlier chunks remain
committed. After the complete Raw row count is validated, the temporary
`challenge_metadata` schema is deleted. A finished challenge database therefore
contains no generator manifest, progress, mapping, or confirmation tables.

Generator version 6 uses generic column names in `table1` through `table6` and
always assigns `group001` to `raw_schema`, regardless of the requested row count.
A database created with an earlier version must be regenerated with `--replace`;
`--resume` intentionally rejects it so a single database cannot mix generator
rules.

## DB Manager metadata

The generator prepares the following tables inside
`postgres_data_manager.app_control` using the same structure expected by the DB
Manager:

- `raw_id_counters`: receives the next available Raw identifier. It is updated
  after every committed chunk and synchronized once more at completion.
- `data_dictionary`: receives 40 generic conceptual entries for the generated
  columns. Columns connected by a foreign key share a single entry; unrelated
  columns remain separate even when their physical names happen to match.
- `data_dictionary_usage`: receives 46 physical links: all 45 columns from
  `table1` through `table6`, plus `raw_data.raw_id`. This reproduces the links
  created through the Manager interface while preserving relational identity.
- `table_versions`: receives lightweight initial version metadata for every
  generated data table.
- `table_version_dependencies`: is created empty so later Expand operations can
  register their dependency manifests normally.

`table1` through `table6` each receive version `0000` with their compact CREATE
TABLE statement stored in `sql_recipe`. `raw_data` also receives only version
`0000`, registered with `raw_schema = group001`. Every generated Raw row uses this
same scope, regardless of the requested row count. The registration is required
because Expand discovers and validates its selectable Raw scope through
`table_versions.raw_schema`.

All these synthetic initial versions keep `raw_dump_path`, `raw_hash`, and
`raw_ingested_at` null. No snapshot or replay dump is generated; the metadata is
intended to exercise the Manager and enable Expand without spending time or disk
space duplicating the generated dataset.

The shared dictionary identities follow the declared foreign keys:

- `table1.id_column_1`, `table2.id_column_2`, `table5.id_column_2`, and
  `table6.id_column_2` share one entry.
- `table3.id_column_1` and `table1.id_column_4` share one entry.
- `table4.id_column_1` and `table6.id_column_3` share one entry.
- `raw_data.raw_id` and `table1.id_column_3` share one entry.

## Expand recipes

The [`SQL files`](./SQL%20files/) directory contains the six SQL recipes for the
Manager's **Expand** action. Add the destinations in filename order because later
recipes read rows inserted by earlier destinations in the same transaction:

1. `01_table3.sql` -> `public.table3`
2. `02_table4.sql` -> `public.table4`
3. `03_table1.sql` -> `public.table1`
4. `04_table5.sql` -> `public.table5`
5. `05_table6.sql` -> `public.table6`
6. `06_table2.sql` -> `public.table2`

Select `public.raw_data` as the read-only source and keep `group001` as both ends
of the Raw scope. Every recipe uses the required `{{source}}` placeholder and only
the `INSERT INTO ... SELECT` form accepted by Expand. Run the six destinations as
one batch so their generated identifiers and foreign-key relationships are
resolved atomically.

The defaults match the DB Manager. If that application was configured with
custom `PDM_CONTROL_DB` or `PDM_CONTROL_SCHEMA` values, export the same variables
before generation or pass `--control_database` and `--control_schema`. Do not use
the DB Manager to append Raw records while the initial generation is still in
progress.

Before creating the database, the command prints an estimate for the textual
`COPY` stream. PostgreSQL heap, JSONB, indexes, temporary files, and WAL require
additional space, so the machine must have substantially more free disk than
this estimate.

## Objects

- `public.raw_data`: populated deterministic Raw source.
- `public.table1`: empty generic relational destination table 1.
- `public.table2`: empty generic relational destination table 2.
- `public.table3`: empty generic relational destination table 3.
- `public.table4`: empty generic relational destination table 4.
- `public.table5`: empty generic relational destination table 5.
- `public.table6`: empty generic relational destination table 6.

Columns in `table1` through `table6` use the same generic naming families as the
JSONB payload. Numbering restarts within each table:

- `id_column_N`: primary keys, foreign keys, and other identifier values.
- `text_column_N`: textual values.
- `integer_column_N`: integer and small-integer values that are not identifiers.
- `numeric_column_N`: decimal values.
- `datetime_column_N`: date/time values.

The generic relational topology enforced by PostgreSQL is:

- `table1.id_column_3` references `raw_data.raw_id` and is unique.
- `table1.id_column_4` references `table3.id_column_1`.
- `table2.id_column_2` references `table1.id_column_1`.
- `table5.id_column_2` references `table1.id_column_1`.
- `table6.id_column_2` references `table1.id_column_1`.
- `table6.id_column_3` references `table4.id_column_1`.

`raw_id` is the Raw table primary key and `(raw_schema, raw_id)` has a B-tree
index. The six relational destinations contain the primary and foreign keys,
unique constraints, cascading actions, and partial unique indexes needed for the
external expansion exercise. They remain empty after generation.

The synthetic source uses anonymized categorical tokens. The generator does not
store source-to-target rules, mappings, or information about any original
dataset. Expansion rules are intentionally supplied outside the database.
