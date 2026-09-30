# -*- coding: latin-1 -*-
import csv
import hashlib
import io
import json
import re
import shlex
import sys
import tempfile
import threading
import time
import warnings
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import paramiko
import pandas as pd

from config import (
    CONTROL_DB,
    CONTROL_SCHEMA,
    DATA_DICTIONARY_TABLE,
    DATA_DICTIONARY_USAGE_TABLE,
    LEGACY_CONTROL_SCHEMA,
    REMOTE_STORAGE_DIR,
)
from utils import (
    format_version_history_entry,
    int_to_version,
    slugify,
    sql_ident,
    sql_literal,
    split_table_name,
    version_to_int,
)


def _configure_csv_field_limit():
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


_configure_csv_field_limit()

CONTROL_SCHEMA_IDENT = sql_ident(CONTROL_SCHEMA)
CONTROL_TABLE_VERSIONS = f"{CONTROL_SCHEMA_IDENT}.{sql_ident('table_versions')}"
CONTROL_TABLE_DELETE_AUDIT = f"{CONTROL_SCHEMA_IDENT}.{sql_ident('delete_audit')}"
CONTROL_TABLE_DATA_DICTIONARY = f"{CONTROL_SCHEMA_IDENT}.{sql_ident(DATA_DICTIONARY_TABLE)}"
CONTROL_TABLE_DATA_DICTIONARY_USAGE = f"{CONTROL_SCHEMA_IDENT}.{sql_ident(DATA_DICTIONARY_USAGE_TABLE)}"
CONTROL_TABLE_RAW_ID_COUNTERS = f"{CONTROL_SCHEMA_IDENT}.{sql_ident('raw_id_counters')}"
CONTROL_TABLE_VERSION_DEPENDENCIES = (
    f"{CONTROL_SCHEMA_IDENT}.{sql_ident('table_version_dependencies')}"
)
DATA_TABLE_ROW_COUNTS_NAME = "pgdm_table_row_counts"
DATA_TABLE_ROW_COUNTS = f"{sql_ident('public')}.{sql_ident(DATA_TABLE_ROW_COUNTS_NAME)}"
LEGACY_CONTROL_TABLE_VERSIONS = f"{sql_ident(LEGACY_CONTROL_SCHEMA)}.{sql_ident('table_versions')}"
LEGACY_CONTROL_TABLE_DELETE_AUDIT = f"{sql_ident(LEGACY_CONTROL_SCHEMA)}.{sql_ident('delete_audit')}"


class OperationCancelledError(RuntimeError):
    pass


class PostgresAdminService:
    VERSION_HISTORY_FORMAT_FULL = "full"
    VERSION_HISTORY_FORMAT_ENTRY = "entry"
    CROSS_TABLE_EXPANSION_OPERATION_KIND = "cross_table_expand_v1"
    CROSS_TABLE_EXPANSION_DEPENDENCY_KIND = "raw_schema_expand_v1"
    CROSS_TABLE_SOURCE_PLACEHOLDER = "{{source}}"
    RAW_REVIEW_PREVIEW_LIMIT = 500
    EXCEL_EXTENSIONS = {".xls", ".xlsx", ".xlsm"}
    HIDDEN_DATABASES = {"postgres", "metabase"}
    RAW_SYSTEM_COLUMNS = {"raw_hash", "raw_ingested_at", "raw_schema", "raw_tab", "raw_id", "raw"}
    RAW_COLUMN_ORDER = ("raw_hash", "raw_ingested_at", "raw_schema", "raw_tab", "raw_id", "raw")
    RAW_BASE_COLUMNS = ("raw_hash", "raw_ingested_at", "raw_schema", "raw_id", "raw")
    JSON_DISCOVERY_SAMPLE_LIMIT = 96
    JSON_DISCOVERY_HEAD_SAMPLES = 48
    RAW_IMPORT_MIN_FREE_BYTES = 128 * 1024 * 1024
    RAW_IMPORT_DISK_MULTIPLIER = 2
    RAW_IMPORT_DISK_OVERHEAD_BYTES = 128 * 1024 * 1024
    RAW_IMPORT_SPOOL_MAX_MEMORY_BYTES = 8 * 1024 * 1024
    RAW_EXPAND_TYPE_MAP = {
        "varchar": "varchar",
        "boolean": "boolean",
        "integer": "integer",
        "bigint": "bigint",
        "smallint": "smallint",
        "numeric": "numeric",
        "real": "real",
        "double precision": "double precision",
        "date": "date",
        "timestamp": "timestamp",
        "timestampz": "timestamptz",
        "timestamptz": "timestamptz",
        "time": "time",
        "uuid": "uuid",
        "jsonb": "jsonb",
    }
    CREATE_TABLE_COLUMN_TYPE_MAP = {
        **RAW_EXPAND_TYPE_MAP,
        "serial": "serial",
        "bigserial": "bigserial",
    }
    DATA_DICTIONARY_TYPE_MAP = {
        **RAW_EXPAND_TYPE_MAP,
        "integer[]": "integer[]",
        "smallint[]": "smallint[]",
        "serial": "integer",
        "bigserial": "bigint",
    }
    SERIAL_BASE_TYPE_MAP = {
        "serial": "integer",
        "bigserial": "bigint",
    }
    QUALITY_NUMERIC_RULES = {
        "numeric_gt",
        "numeric_gte",
        "numeric_lt",
        "numeric_lte",
        "numeric_between",
        "numeric_mean_stddev_within",
        "numeric_max_mean_stddev",
        "numeric_min_mean_stddev",
    }
    QUALITY_BLANK_RULES = {"not_blank", "is_blank"}
    ROLE_PERMISSION_DEFINITIONS = [
        {
            "key": "attr:login",
            "label": "LOGIN",
            "description": "Permite que o usuario faca login no PostgreSQL.",
            "kind": "attribute",
            "attribute": "rolcanlogin",
            "grant_sql": "LOGIN",
            "revoke_sql": "NOLOGIN",
        },
        {
            "key": "membership:pg_read_all_data",
            "label": "Ler todos os dados",
            "description": "Concede a role pg_read_all_data para consultar tabelas, views e sequencias.",
            "kind": "membership",
            "role_name": "pg_read_all_data",
        },
        {
            "key": "membership:pg_write_all_data",
            "label": "Escrever todos os dados",
            "description": "Concede a role pg_write_all_data para inserir, atualizar e apagar dados.",
            "kind": "membership",
            "role_name": "pg_write_all_data",
        },
        {
            "key": "attr:createdb",
            "label": "Criar bases",
            "description": "Permite criar novas bases de dados.",
            "kind": "attribute",
            "attribute": "rolcreatedb",
            "grant_sql": "CREATEDB",
            "revoke_sql": "NOCREATEDB",
        },
        {
            "key": "attr:createrole",
            "label": "Criar e gerenciar roles",
            "description": "Permite criar roles e alterar/conceder roles dentro dos limites do PostgreSQL.",
            "kind": "attribute",
            "attribute": "rolcreaterole",
            "grant_sql": "CREATEROLE",
            "revoke_sql": "NOCREATEROLE",
        },
        {
            "key": "membership:pg_monitor",
            "label": "Monitorar servidor",
            "description": "Concede acesso a estatisticas e funcoes de monitoramento.",
            "kind": "membership",
            "role_name": "pg_monitor",
        },
        {
            "key": "membership:pg_read_all_settings",
            "label": "Ler configuracoes",
            "description": "Permite consultar configuracoes do servidor PostgreSQL.",
            "kind": "membership",
            "role_name": "pg_read_all_settings",
        },
        {
            "key": "membership:pg_read_all_stats",
            "label": "Ler estatisticas",
            "description": "Permite consultar estatisticas de atividade e uso.",
            "kind": "membership",
            "role_name": "pg_read_all_stats",
        },
        {
            "key": "membership:pg_stat_scan_tables",
            "label": "Escanear estatisticas de tabelas",
            "description": "Permite executar funcoes de estatisticas que varrem tabelas.",
            "kind": "membership",
            "role_name": "pg_stat_scan_tables",
        },
        {
            "key": "membership:pg_signal_backend",
            "label": "Encerrar consultas/sessoes",
            "description": "Permite sinalizar processos de backend, como cancelar consultas.",
            "kind": "membership",
            "role_name": "pg_signal_backend",
        },
        {
            "key": "attr:inherit",
            "label": "Herdar permissoes",
            "description": "Permite herdar automaticamente permissoes de roles das quais participa.",
            "kind": "attribute",
            "attribute": "rolinherit",
            "grant_sql": "INHERIT",
            "revoke_sql": "NOINHERIT",
        },
        {
            "key": "attr:replication",
            "label": "Replicacao",
            "description": "Permite conexoes e operacoes de replicacao.",
            "kind": "attribute",
            "attribute": "rolreplication",
            "grant_sql": "REPLICATION",
            "revoke_sql": "NOREPLICATION",
        },
        {
            "key": "attr:bypassrls",
            "label": "Ignorar RLS",
            "description": "Ignora politicas de row-level security.",
            "kind": "attribute",
            "attribute": "rolbypassrls",
            "grant_sql": "BYPASSRLS",
            "revoke_sql": "NOBYPASSRLS",
        },
        {
            "key": "membership:pg_read_server_files",
            "label": "Ler arquivos do servidor",
            "description": "Permite ler arquivos acessiveis pelo processo do PostgreSQL.",
            "kind": "membership",
            "role_name": "pg_read_server_files",
        },
        {
            "key": "membership:pg_write_server_files",
            "label": "Escrever arquivos no servidor",
            "description": "Permite escrever arquivos acessiveis pelo processo do PostgreSQL.",
            "kind": "membership",
            "role_name": "pg_write_server_files",
        },
        {
            "key": "membership:pg_execute_server_program",
            "label": "Executar programas no servidor",
            "description": "Permite executar programas no servidor pelo PostgreSQL.",
            "kind": "membership",
            "role_name": "pg_execute_server_program",
        },
        {
            "key": "membership:pg_checkpoint",
            "label": "Executar checkpoint",
            "description": "Permite executar checkpoints manualmente quando a role existir.",
            "kind": "membership",
            "role_name": "pg_checkpoint",
        },
        {
            "key": "membership:pg_maintain",
            "label": "Manutencao",
            "description": "Permite operacoes de manutencao quando a role pg_maintain existir.",
            "kind": "membership",
            "role_name": "pg_maintain",
        },
        {
            "key": "attr:superuser",
            "label": "SUPERUSER",
            "description": "Controle total do PostgreSQL. Use somente para administradores.",
            "kind": "attribute",
            "attribute": "rolsuper",
            "grant_sql": "SUPERUSER",
            "revoke_sql": "NOSUPERUSER",
        },
    ]

    def __init__(self):
        self.ssh_client = None
        self.ssh_host = None
        self.ssh_port = None
        self.ssh_username = None
        self.ssh_password = None
        self.postgres_port = None
        self.sql_username = None
        self.sql_password = None
        self._active_channels = set()
        self._active_channels_lock = threading.Lock()
        self._data_metadata_ready_databases = set()
        self._data_metadata_lock = threading.Lock()
        self._expansion_timing_local = threading.local()

    @staticmethod
    def create_cross_table_expansion_timing_report(
        database_name: str,
        source_table_name: str,
        raw_schemas,
        destination_names,
    ) -> dict:
        return {
            "report_version": 1,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "completed_at": None,
            "database_name": str(database_name or "").strip(),
            "source_table_name": str(source_table_name or "").strip(),
            "raw_schemas": [str(value) for value in (raw_schemas or [])],
            "destination_names": [str(value) for value in (destination_names or [])],
            "source_rows": 0,
            "total_rows_inserted": 0,
            "destination_rows": [],
            "items": [],
        }

    @staticmethod
    def _append_expansion_timing(
        report: dict | None,
        stage: str,
        name: str,
        seconds: float,
        measurement: str,
        *,
        rows: int | None = None,
        destination: str | None = None,
        details: str | None = None,
        include_in_total: bool = True,
    ):
        if not isinstance(report, dict):
            return
        report.setdefault("items", []).append(
            {
                "stage": str(stage or "other"),
                "name": str(name or "Unnamed step"),
                "seconds": max(float(seconds or 0.0), 0.0),
                "measurement": str(measurement or "unknown"),
                "rows": int(rows) if rows is not None else None,
                "destination": str(destination) if destination else None,
                "details": str(details) if details else None,
                "include_in_total": bool(include_in_total),
            }
        )

    @contextmanager
    def expansion_timing_scope(
        self,
        report: dict | None,
        stage: str,
        name: str,
        *,
        record_local_cpu: bool = True,
        remote_include_in_total: bool = True,
    ):
        previous_context = getattr(
            self._expansion_timing_local,
            "context",
            None,
        )
        context = {
            "report": report,
            "stage": str(stage or "other"),
            "name": str(name or "Unnamed step"),
            "remote_count": 0,
            "remote_include_in_total": bool(remote_include_in_total),
        }
        self._expansion_timing_local.context = context
        cpu_started_at = time.thread_time()
        try:
            yield
        finally:
            if record_local_cpu:
                self._append_expansion_timing(
                    report,
                    stage,
                    f"{name} - local Python CPU",
                    time.thread_time() - cpu_started_at,
                    "local_python_cpu",
                    details=(
                        "Per-thread CPU time; blocking, SSH transit, and remote "
                        "database wait are excluded."
                    ),
                )
            self._expansion_timing_local.context = previous_context

    @staticmethod
    def _trace_elapsed_ms(trace_context: dict | None) -> float:
        if not trace_context:
            return 0.0
        return (time.perf_counter() - trace_context["started_at"]) * 1000

    def _trace_table_open(self, trace_context: dict | None, message: str):
        if not trace_context:
            return

        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        request_id = trace_context.get("request_id", "?")
        table_name = trace_context.get("table_name", "?")
        print(
            f"[table-open #{request_id} {stamp} +{self._trace_elapsed_ms(trace_context):8.1f} ms {table_name}] {message}",
            flush=True,
        )

    def is_connected(self) -> bool:
        return self.ssh_client is not None

    @property
    def remote_storage_root(self) -> str:
        if not self.ssh_username:
            raise RuntimeError("Usuário SSH não disponível.")
        return f"/home/{self.ssh_username}/{REMOTE_STORAGE_DIR}"

    def connect(
        self,
        host: str,
        ssh_port: int,
        ssh_username: str,
        ssh_password: str,
        postgres_port: int,
        sql_username: str,
        sql_password: str,
    ):
        self.close()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=ssh_port,
            username=ssh_username,
            password=ssh_password,
            timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        self.ssh_client = client
        self.ssh_host = host
        self.ssh_port = ssh_port
        self.ssh_username = ssh_username
        self.ssh_password = ssh_password
        self.postgres_port = int(postgres_port)
        self.sql_username = sql_username
        self.sql_password = sql_password

    def close(self):
        self.cancel_active_commands()
        if self.ssh_client:
            try:
                self.ssh_client.close()
            finally:
                self.ssh_client = None
        self.ssh_host = None
        self.ssh_port = None
        self.ssh_username = None
        self.ssh_password = None
        self.postgres_port = None
        self.sql_username = None
        self.sql_password = None
        self._data_metadata_ready_databases.clear()

    def _register_active_channel(self, channel):
        with self._active_channels_lock:
            self._active_channels.add(channel)

    def _unregister_active_channel(self, channel):
        with self._active_channels_lock:
            self._active_channels.discard(channel)

    def cancel_active_commands(self):
        with self._active_channels_lock:
            channels = list(self._active_channels)

        for channel in channels:
            try:
                channel.close()
            except Exception:
                pass

    @staticmethod
    def _raise_if_cancelled(cancel_event=None, channel=None):
        if cancel_event and cancel_event.is_set():
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
            raise OperationCancelledError("Operacao cancelada pelo usuario.")

    def _write_stdin_stream(self, stdin, stdin_text, cancel_event=None, progress_callback=None):
        if stdin_text is None:
            return

        chunk_size = 64 * 1024
        sent_bytes = 0

        if hasattr(stdin_text, "read"):
            total_bytes = None
            try:
                current_offset = stdin_text.tell()
                stdin_text.seek(0, io.SEEK_END)
                total_bytes = stdin_text.tell() - current_offset
                stdin_text.seek(current_offset)
            except Exception:
                total_bytes = None

            while True:
                self._raise_if_cancelled(cancel_event, stdin.channel)
                chunk = stdin_text.read(chunk_size)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                stdin.channel.sendall(chunk)
                sent_bytes += len(chunk)

                if progress_callback:
                    if total_bytes:
                        progress = (sent_bytes / total_bytes) * 100
                        message = f"Sending data to the database: {sent_bytes:,}/{total_bytes:,} bytes."
                    else:
                        progress = 0.0
                        message = f"Sending data to the database: {sent_bytes:,} bytes."
                    progress_callback(progress, message)

            if progress_callback and sent_bytes == 0:
                progress_callback(100.0, "Payload sent to the remote process.")
            stdin.channel.shutdown_write()
            return

        payload = stdin_text.encode("utf-8") if isinstance(stdin_text, str) else stdin_text
        total_bytes = len(payload)

        if total_bytes == 0:
            if progress_callback:
                progress_callback(100.0, "Payload sent to the remote process.")
            stdin.channel.shutdown_write()
            return

        while sent_bytes < total_bytes:
            self._raise_if_cancelled(cancel_event, stdin.channel)

            chunk = payload[sent_bytes:sent_bytes + chunk_size]
            stdin.channel.sendall(chunk)
            sent_bytes += len(chunk)

            if progress_callback:
                progress = (sent_bytes / total_bytes) * 100
                progress_callback(progress, f"Sending data to the database: {sent_bytes:,}/{total_bytes:,} bytes.")

        stdin.channel.shutdown_write()

    def run_remote_command(
        self,
        command: str,
        stdin_text: str | bytes | io.IOBase | None = None,
        cancel_event=None,
        stdin_progress_callback=None,
        timeout_seconds: float | None = None,
        trace_context: dict | None = None,
        trace_label: str | None = None,
    ) -> str:
        if not self.ssh_client:
            raise RuntimeError("SSH n?o conectado.")
        if self.sql_password is None:
            raise RuntimeError("Senha do PostgreSQL n?o dispon?vel.")

        command_name = trace_label or "remote command"
        command_started_at = time.perf_counter()
        self._trace_table_open(trace_context, f"{command_name}: iniciou comando remoto")

        safe_password = shlex.quote(self.sql_password)
        safe_sql_port = shlex.quote(str(self.postgres_port or 5432))
        wrapped_command = f"export PGPASSWORD={safe_password} PGPORT={safe_sql_port}; {command}"
        timing_context = getattr(
            self._expansion_timing_local,
            "context",
            None,
        )
        timing_marker = None
        if timing_context:
            timing_marker = (
                "__PGDM_REMOTE_TIMING_"
                f"{threading.get_ident()}_{time.time_ns()}__="
            )
            wrapped_command = (
                "pgdm_timer_started=$(date +%s%N); "
                f"{{ {wrapped_command}; }}; "
                "pgdm_timer_status=$?; "
                "pgdm_timer_finished=$(date +%s%N); "
                f"printf '\\n{timing_marker}%s\\n' "
                '"$((pgdm_timer_finished - pgdm_timer_started))" >&2; '
                'exit "$pgdm_timer_status"'
            )

        stdin, stdout, stderr = self.ssh_client.exec_command(wrapped_command)
        channel = stdout.channel
        self._register_active_channel(channel)
        output_chunks = []
        error_chunks = []
        started_at = time.monotonic()

        def drain_channel():
            while channel.recv_ready():
                output_chunks.append(channel.recv(65536).decode("utf-8", errors="replace"))
            while channel.recv_stderr_ready():
                error_chunks.append(channel.recv_stderr(65536).decode("utf-8", errors="replace"))

        try:
            self._raise_if_cancelled(cancel_event, channel)

            if stdin_text is not None:
                self._write_stdin_stream(
                    stdin,
                    stdin_text,
                    cancel_event=cancel_event,
                    progress_callback=stdin_progress_callback,
                )

            while True:
                self._raise_if_cancelled(cancel_event, channel)

                now = time.monotonic()
                if (
                        timeout_seconds is not None
                        and now - started_at > timeout_seconds
                ):
                    try:
                        channel.close()
                    finally:
                        raise TimeoutError(
                            f"Comando remoto excedeu "
                            f"{timeout_seconds:.0f}s."
                        )

                # Consome toda a saída atualmente disponível.
                drain_channel()

                # Não basta o processo informar que terminou.
                # Também precisamos garantir que stdout e stderr
                # foram completamente consumidos.
                if (
                        channel.exit_status_ready()
                        and not channel.recv_ready()
                        and not channel.recv_stderr_ready()
                ):
                    break

                time.sleep(0.05)

            exit_status = channel.recv_exit_status()

            # Drenagem defensiva final.
            drain_channel()
            output = "".join(output_chunks)
            error = "".join(error_chunks)
            if timing_marker:
                timing_matches = re.findall(
                    re.escape(timing_marker) + r"(\d+)",
                    error,
                )
                error = re.sub(
                    r"(?:\r?\n)?"
                    + re.escape(timing_marker)
                    + r"\d+(?:\r?\n)?",
                    "\n",
                    error,
                ).strip()
                if timing_matches:
                    timing_context["remote_count"] += 1
                    remote_index = timing_context["remote_count"]
                    remote_name = timing_context["name"]
                    if remote_index > 1:
                        remote_name += f" - server command {remote_index}"
                    self._append_expansion_timing(
                        timing_context["report"],
                        timing_context["stage"],
                        remote_name,
                        int(timing_matches[-1]) / 1_000_000_000.0,
                        "remote_server_wall",
                        details=(
                            "Measured by the Linux server around the remote command; "
                            "SSH connection and result-return transit are excluded."
                        ),
                        include_in_total=timing_context[
                            "remote_include_in_total"
                        ],
                    )

            if exit_status != 0:
                raise RuntimeError(error.strip() or output.strip() or f"Comando remoto falhou com status {exit_status}.")
            if error.strip() and not output.strip():
                raise RuntimeError(error.strip())

            result = output.strip()
            self._trace_table_open(
                trace_context,
                (
                    f"{command_name}: comando remoto conclu?do em "
                    f"{(time.perf_counter() - command_started_at) * 1000:.1f} ms "
                    f"(stdout_chars={len(result)}, stderr_chars={len(error.strip())})"
                ),
            )
            return result
        except Exception as exc:
            self._trace_table_open(
                trace_context,
                (
                    f"{command_name}: falhou em "
                    f"{(time.perf_counter() - command_started_at) * 1000:.1f} ms "
                    f"({type(exc).__name__}: {exc})"
                ),
            )
            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operacao cancelada pelo usuario.")
            raise
        finally:
            try:
                channel.close()
            except Exception:
                pass
            self._unregister_active_channel(channel)

    def iter_remote_command_lines(self, command: str, cancel_event=None):
        if not self.ssh_client:
            raise RuntimeError("SSH nÃ£o conectado.")
        if self.sql_password is None:
            raise RuntimeError("Senha do PostgreSQL nÃ£o disponÃ­vel.")

        safe_password = shlex.quote(self.sql_password)
        safe_sql_port = shlex.quote(str(self.postgres_port or 5432))
        wrapped_command = f"export PGPASSWORD={safe_password} PGPORT={safe_sql_port}; {command}"

        _stdin, stdout, _stderr = self.ssh_client.exec_command(wrapped_command)
        channel = stdout.channel
        self._register_active_channel(channel)
        error_chunks = []
        text_buffer = ""

        try:
            while True:
                self._raise_if_cancelled(cancel_event, channel)
                made_progress = False

                while channel.recv_ready():
                    made_progress = True
                    text_buffer += channel.recv(65536).decode("utf-8", errors="replace")
                    lines = text_buffer.splitlines(keepends=True)
                    if lines and not lines[-1].endswith(("\n", "\r")):
                        text_buffer = lines.pop()
                    else:
                        text_buffer = ""
                    for line in lines:
                        yield line

                while channel.recv_stderr_ready():
                    made_progress = True
                    error_chunks.append(channel.recv_stderr(65536).decode("utf-8", errors="replace"))

                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break

                if not made_progress:
                    time.sleep(0.1)

            if text_buffer:
                yield text_buffer

            exit_status = channel.recv_exit_status()
            while channel.recv_stderr_ready():
                error_chunks.append(channel.recv_stderr(65536).decode("utf-8", errors="replace"))
            error = "".join(error_chunks)
            self._raise_if_cancelled(cancel_event, channel)

            if exit_status != 0:
                raise RuntimeError(error.strip() or f"Comando remoto falhou com status {exit_status}.")
        except Exception:
            if cancel_event and cancel_event.is_set():
                raise OperationCancelledError("Operacao cancelada pelo usuario.")
            raise
        finally:
            try:
                channel.close()
            except Exception:
                pass
            self._unregister_active_channel(channel)

    @staticmethod
    def _format_byte_count(byte_count: int | None) -> str:
        if byte_count is None:
            return "desconhecido"
        value = float(max(int(byte_count), 0))
        units = ["B", "KB", "MB", "GB", "TB"]
        unit_index = 0
        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(value)} {units[unit_index]}"
        return f"{value:.1f} {units[unit_index]}"

    def _current_cluster_data_directory(self, database_name: str, cancel_event=None) -> str:
        sql = "SHOW data_directory;"
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -c {shlex.quote(sql)}"
        )
        return self.run_remote_command(command, cancel_event=cancel_event).strip()

    def _read_remote_filesystem_status(self, path: str, cancel_event=None) -> dict:
        quoted_path = shlex.quote(path)
        blocks_command = f"LC_ALL=C df -kP {quoted_path} | tail -n 1"
        inodes_command = f"LC_ALL=C df -iP {quoted_path} | tail -n 1"

        blocks_line = self.run_remote_command(blocks_command, cancel_event=cancel_event).strip()
        inodes_line = self.run_remote_command(inodes_command, cancel_event=cancel_event).strip()

        block_parts = blocks_line.split()
        inode_parts = inodes_line.split()
        if len(block_parts) < 6 or len(inode_parts) < 6:
            raise RuntimeError(f"Nao foi possivel ler o filesystem remoto de {path}.")

        return {
            "filesystem": block_parts[0],
            "mount_point": block_parts[5],
            "available_kb": int(block_parts[3]),
            "available_bytes": int(block_parts[3]) * 1024,
            "used_percent": block_parts[4],
            "available_inodes": int(inode_parts[3]),
            "inode_used_percent": inode_parts[4],
        }

    def get_postgres_data_directory_status(self, database_name: str, cancel_event=None) -> dict:
        data_directory = self._current_cluster_data_directory(database_name, cancel_event=cancel_event)
        filesystem_status = self._read_remote_filesystem_status(data_directory, cancel_event=cancel_event)
        filesystem_status["data_directory"] = data_directory
        return filesystem_status

    def _estimate_raw_import_required_free_bytes(self, copy_payload: str | bytes | int) -> int:
        if isinstance(copy_payload, int):
            payload_size = copy_payload
        elif isinstance(copy_payload, bytes):
            payload_size = len(copy_payload)
        else:
            payload_size = len(copy_payload.encode("utf-8"))
        return max(
            self.RAW_IMPORT_MIN_FREE_BYTES,
            (payload_size * self.RAW_IMPORT_DISK_MULTIPLIER) + self.RAW_IMPORT_DISK_OVERHEAD_BYTES,
        )

    def _raise_if_postgres_storage_too_low(
        self,
        database_name: str,
        required_bytes: int,
        cancel_event=None,
    ) -> dict:
        status = self.get_postgres_data_directory_status(database_name, cancel_event=cancel_event)
        available_bytes = int(status.get("available_bytes") or 0)
        if available_bytes >= required_bytes:
            return status

        raise RuntimeError(
            "Espaco insuficiente no filesystem do PostgreSQL para concluir a carga Raw. "
            f"data_directory={status['data_directory']} | montagem={status['mount_point']} | "
            f"livre={self._format_byte_count(available_bytes)} | "
            f"estimado_minimo={self._format_byte_count(required_bytes)} | "
            f"inodes_livres={status['available_inodes']}."
        )

    def _rewrite_storage_error(
        self,
        exc: Exception,
        database_name: str,
        cancel_event=None,
        required_bytes: int | None = None,
    ) -> RuntimeError:
        message = str(exc)
        normalized = message.lower()
        if "no space left on device" not in normalized and "filefallocate()" not in normalized:
            return RuntimeError(message)

        details = []
        try:
            status = self.get_postgres_data_directory_status(database_name, cancel_event=cancel_event)
        except Exception:
            status = None

        details.append(
            "O PostgreSQL ficou sem espaco no filesystem do proprio data_directory durante a carga Raw."
        )
        if status:
            details.append(
                f"data_directory={status['data_directory']} | montagem={status['mount_point']} | "
                f"livre={self._format_byte_count(status['available_bytes'])} | "
                f"uso={status['used_percent']} | inodes_livres={status['available_inodes']}."
            )
        if required_bytes is not None:
            details.append(f"Estimativa minima para esta carga: {self._format_byte_count(required_bytes)}.")
        details.append("Ajuste o volume do PostgreSQL, mova o data_directory/PGDATA ou use um tablespace em particao maior.")
        details.append(f"Detalhe original: {message}")
        return RuntimeError(" ".join(details))

    @staticmethod
    def parse_csv_output(output: str):
        if not output.strip():
            return [], []

        reader = csv.reader(io.StringIO(output))
        rows = list(reader)

        if not rows:
            return [], []

        headers = rows[0]
        data = rows[1:]
        return headers, data

    def _normalize_csv_row(
        self,
        row,
        expected_length: int,
        trace_context: dict | None = None,
        trace_label: str | None = None,
    ):
        values = list(row)
        if len(values) >= expected_length:
            return values

        self._trace_table_open(
            trace_context,
            (
                f"{trace_label or 'csv_row'}: row curta detectada; "
                f"expected={expected_length}; actual={len(values)}; padding aplicado"
            ),
        )
        values.extend([""] * (expected_length - len(values)))
        return values

    @staticmethod
    def _normalize_raw_value(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            if pd.isna(value):
                return None
            return value
        if pd.isna(value):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _is_blank_raw_value(value) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def _should_skip_blank_raw_record(self, values) -> bool:
        return not values or all(self._is_blank_raw_value(value) for value in values)

    @staticmethod
    def _normalize_raw_record_widths(records):
        normalized_records = list(records or [])
        max_width = max(
            (len(record.get("values", [])) for record in normalized_records),
            default=0,
        )
        if max_width <= 0:
            return normalized_records

        for record in normalized_records:
            values = list(record.get("values", []))
            if len(values) >= max_width:
                continue
            record["values"] = values + ([""] * (max_width - len(values)))
        return normalized_records

    @staticmethod
    def _raw_source_row_count(raw_source: dict | None) -> int:
        if not raw_source:
            return 0
        if "row_count" in raw_source:
            return int(raw_source["row_count"])
        return len(raw_source.get("records", []))

    @classmethod
    def _get_raw_source_system_columns(cls, raw_source: dict | None) -> list[str]:
        requested_columns = {
            str(column).strip()
            for column in (raw_source or {}).get("raw_system_columns", [])
            if str(column).strip()
        }
        if not requested_columns:
            if (raw_source or {}).get("source_type") == "json":
                requested_columns = set(cls.RAW_BASE_COLUMNS)
            else:
                requested_columns = set(cls.RAW_COLUMN_ORDER)
        return [column for column in cls.RAW_COLUMN_ORDER if column in requested_columns]

    @staticmethod
    def _build_raw_batch_label(file_names) -> str:
        cleaned_names = [str(name).strip() for name in file_names if str(name).strip()]
        if not cleaned_names:
            return "Raw"
        if len(cleaned_names) == 1:
            return cleaned_names[0]

        preview_names = ", ".join(cleaned_names[:2])
        remaining_count = len(cleaned_names) - 2
        if remaining_count > 0:
            preview_names += f" +{remaining_count}"
        return f"Lote Raw ({len(cleaned_names)} arquivos: {preview_names})"

    @staticmethod
    def _build_raw_batch_hash(raw_sources) -> str:
        digest = hashlib.sha256()
        for source in raw_sources:
            digest.update(str(source.get("file_name") or "").encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(source.get("file_hash") or "").encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def combine_raw_sources(self, raw_sources) -> dict:
        source_list = [source for source in (raw_sources or []) if source]
        if not source_list:
            raise RuntimeError("Nenhum source Raw foi informado para combinacao.")

        if len(source_list) == 1:
            return dict(source_list[0])

        batch_ingested_at = datetime.now().astimezone().isoformat(timespec="seconds")
        batch_raw_schema = source_list[0].get("raw_schema")
        merged_records = []
        merged_sheets = []
        merged_source_files = []
        requested_columns = set()
        multi_source = len(source_list) > 1

        for source_index, source in enumerate(source_list, start=1):
            source_file_name = str(source.get("file_name") or f"arquivo_{source_index}").strip() or f"arquivo_{source_index}"
            source_file_hash = source.get("file_hash")
            source_ingested_at = source.get("ingested_at") or batch_ingested_at
            source_raw_schema = source.get("raw_schema")
            requested_columns.update(self._get_raw_source_system_columns(source))

            source_file_entries = list(source.get("source_files") or [])
            if not source_file_entries:
                source_file_entry = {
                    "file_name": source_file_name,
                    "file_hash": source_file_hash,
                    "ingested_at": source_ingested_at,
                }
                if source.get("json_data_path"):
                    source_file_entry["json_data_path"] = source.get("json_data_path")
                source_file_entries.append(source_file_entry)
            merged_source_files.extend(dict(entry) for entry in source_file_entries)

            sheet_key_map = {}
            source_sheets = list(source.get("sheets") or [])
            pseudo_sheet_key = None
            if source_sheets:
                for sheet in source_sheets:
                    original_sheet_key = str(sheet.get("sheet_key") or "").strip() or f"S{len(merged_sheets) + 1}"
                    merged_sheet_key = f"F{source_index}-{original_sheet_key}" if multi_source else original_sheet_key
                    sheet_key_map[original_sheet_key] = merged_sheet_key
                    merged_sheet = dict(sheet)
                    merged_sheet["sheet_key"] = merged_sheet_key
                    if multi_source:
                        original_sheet_name = str(merged_sheet.get("sheet_name") or original_sheet_key).strip() or original_sheet_key
                        merged_sheet["sheet_name"] = f"{source_file_name} / {original_sheet_name}"
                        merged_sheet["source_file_name"] = source_file_name
                    merged_sheets.append(merged_sheet)
            elif source.get("source_type") != "json":
                pseudo_sheet_key = f"F{source_index}"
                merged_sheets.append({
                    "sheet_name": source_file_name,
                    "sheet_index": source_index,
                    "sheet_key": pseudo_sheet_key,
                    "record_count": len(source.get("records") or []),
                    "source_file_name": source_file_name,
                })

            for record in source.get("records", []):
                merged_record = dict(record)
                if "record_number" in merged_record:
                    merged_record["record_number"] = len(merged_records) + 1

                source_label = str(merged_record.get("source_label") or "").strip()
                if multi_source and source_label:
                    merged_record["source_label"] = f"{source_file_name} - {source_label}"

                original_record_sheet_key = str(merged_record.get("sheet_key") or "").strip()
                if original_record_sheet_key and original_record_sheet_key in sheet_key_map:
                    merged_record["sheet_key"] = sheet_key_map[original_record_sheet_key]
                elif pseudo_sheet_key and not merged_record.get("sheet_key"):
                    merged_record["sheet_key"] = pseudo_sheet_key

                if pseudo_sheet_key and not merged_record.get("sheet_name"):
                    merged_record["sheet_name"] = source_file_name

                if source_file_hash and not merged_record.get("raw_hash"):
                    merged_record["raw_hash"] = source_file_hash
                if source_ingested_at and not merged_record.get("raw_ingested_at"):
                    merged_record["raw_ingested_at"] = source_ingested_at
                if source_raw_schema is not None and not merged_record.get("raw_schema"):
                    merged_record["raw_schema"] = source_raw_schema
                merged_record["source_file_name"] = merged_record.get("source_file_name") or source_file_name
                merged_records.append(merged_record)

        combined_source = {
            "file_name": self._build_raw_batch_label(
                [source.get("file_name") or f"arquivo_{index}" for index, source in enumerate(source_list, start=1)]
            ),
            "file_hash": self._build_raw_batch_hash(source_list),
            "ingested_at": batch_ingested_at,
            "raw_schema": batch_raw_schema,
            "source_type": source_list[0].get("source_type", "text"),
            "raw_system_columns": [column for column in self.RAW_COLUMN_ORDER if column in requested_columns],
            "source_count": len(merged_source_files),
            "source_files": merged_source_files,
            "records": merged_records,
        }
        if merged_sheets:
            combined_source["sheets"] = merged_sheets
        if merged_records and any("record_number" in record for record in merged_records):
            combined_source["review_candidates"] = self.build_raw_review_candidates(merged_records)
            combined_source["preview_count"] = len(merged_records)
            combined_source["total_records"] = len(merged_records)
        return combined_source

    @staticmethod
    def _is_filled_raw_value(value) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip() != ""
        return True

    @staticmethod
    def _count_raw_words(value) -> int:
        if not isinstance(value, str):
            return 0
        return len(value.strip().split())

    @staticmethod
    def _is_numeric_like_raw_string(value: str) -> bool:
        normalized = value.strip().replace("\u00a0", " ")
        if not normalized:
            return False

        compact = normalized.replace(" ", "")
        numeric_patterns = (
            r"[+-]?\d+(?:[.,]\d+)?%?",
            r"[+-]?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?%?",
            r"[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?",
        )
        return any(re.fullmatch(pattern, compact) for pattern in numeric_patterns)

    @staticmethod
    def _is_date_like_raw_string(value: str) -> bool:
        normalized = value.strip()
        if not normalized:
            return False

        date_patterns = (
            r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?",
            r"\d{1,2}\.\d{1,2}\.\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?)?",
            r"\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?",
            (
                r"(?i)(?:jan|fev|mar|abr|mai|jun|jul|ago|set|sep|out|oct|nov|dez|"
                r"feb|apr|aug|may|jun|jul|oct|dec)[a-z]*[-/\s]+\d{2,4}"
            ),
            (
                r"(?i)\d{1,2}[-/\s]+(?:jan|fev|mar|abr|mai|jun|jul|ago|set|sep|out|oct|nov|dez|"
                r"feb|apr|aug|may|jun|jul|oct|dec)[a-z]*(?:[-/\s]+\d{2,4})?"
            ),
        )
        return any(re.fullmatch(pattern, normalized) for pattern in date_patterns)

    def _is_textual_header_value(self, value) -> bool:
        if not isinstance(value, str):
            return False

        normalized = value.strip()
        if not normalized:
            return False
        if self._is_numeric_like_raw_string(normalized):
            return False
        if self._is_date_like_raw_string(normalized):
            return False

        return any(char.isalpha() for char in normalized)

    @staticmethod
    def _format_raw_record_preview(values) -> str:
        return " | ".join("(null)" if value is None else str(value) for value in values)

    @staticmethod
    def _parse_raw_id_value(raw_id_value):
        text = str(raw_id_value or "").strip()
        if not text:
            return None
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        return text

    @staticmethod
    def _build_imported_raw_source_label(record_number: int, raw_tab: str | None, raw_id) -> str:
        if isinstance(raw_id, int):
            raw_id_label = f"raw #{raw_id}"
        elif raw_id is not None:
            raw_id_label = f"raw ids {raw_id}"
        else:
            raw_id_label = None

        if raw_tab and raw_id_label:
            return f"aba {raw_tab}, {raw_id_label}"
        if raw_id_label:
            return raw_id_label
        return f"linha importada {record_number}"

    def _build_raw_review_record(self, records, values, source_label: str, **metadata) -> dict:
        record = {
            "record_number": len(records) + 1,
            "source_label": source_label,
            "values": values,
        }
        record.update(metadata)
        return record

    def _is_header_candidate(self, values) -> bool:
        total_fields = len(values)
        if total_fields == 0:
            return False
        if not all(self._is_filled_raw_value(value) for value in values):
            return False

        textual_count = sum(1 for value in values if self._is_textual_header_value(value))
        return (textual_count / total_fields) >= 0.7

    def _is_noise_candidate(self, values) -> bool:
        return any(self._count_raw_words(value) >= 3 for value in values)

    def build_raw_review_candidates(self, records) -> dict:
        return {
            "header_candidates": [
                record for record in records
                if self._is_header_candidate(record.get("values", []))
            ],
            "noise_candidates": [
                record for record in records
                if self._is_noise_candidate(record.get("values", []))
            ],
        }

    def _build_raw_header_keys(self, header_records, column_count: int) -> list[str]:
        seen_keys = {}
        keys = []

        for column_index in range(column_count):
            parts = []
            for record in header_records:
                values = record.get("values", [])
                value = values[column_index] if column_index < len(values) else None
                if self._is_blank_raw_value(value):
                    continue

                text = value.strip() if isinstance(value, str) else str(value)
                if text:
                    parts.append(text)

            base_key = " | ".join(parts).strip() or f"column_{column_index + 1}"
            seen_count = seen_keys.get(base_key, 0)
            seen_keys[base_key] = seen_count + 1
            unique_key = base_key if seen_count == 0 else f"{base_key}_{seen_count + 1}"
            keys.append(unique_key)

        return keys

    def _map_raw_values_with_header(self, values, header_keys: list[str]) -> dict:
        mapped = {}
        for column_index, key in enumerate(header_keys):
            mapped[key] = values[column_index] if column_index < len(values) else None
        return mapped

    @staticmethod
    def _normalize_raw_header_values(values, column_count: int) -> tuple:
        normalized = []
        for column_index in range(column_count):
            value = values[column_index] if column_index < len(values) else None
            if value is None:
                normalized.append("")
            else:
                normalized.append(str(value).strip())
        return tuple(normalized)

    def _validate_raw_sheet_headers(self, raw_source: dict, header_records) -> list[dict]:
        sheet_infos = list(raw_source.get("sheets") or [])
        if not sheet_infos:
            raise RuntimeError("Nao foi possivel identificar as abas do Excel para validar os headers.")

        headers_by_sheet = {
            sheet["sheet_key"]: []
            for sheet in sheet_infos
        }
        for record in header_records:
            sheet_key = record.get("sheet_key")
            if sheet_key in headers_by_sheet:
                headers_by_sheet[sheet_key].append(record)

        missing_sheets = [
            f"{sheet['sheet_key']} ({sheet['sheet_name']})"
            for sheet in sheet_infos
            if not headers_by_sheet.get(sheet["sheet_key"])
        ]
        repeated_sheets = [
            f"{sheet_key} ({len(records)} headers)"
            for sheet_key, records in headers_by_sheet.items()
            if len(records) > 1
        ]
        if missing_sheets or repeated_sheets:
            details = []
            if missing_sheets:
                details.append("sem header: " + ", ".join(missing_sheets))
            if repeated_sheets:
                details.append("com mais de um header: " + ", ".join(repeated_sheets))
            raise RuntimeError(
                "Selecione exatamente 1 header para cada aba importada (" + "; ".join(details) + ")."
            )

        ordered_headers = [
            headers_by_sheet[sheet["sheet_key"]][0]
            for sheet in sheet_infos
        ]
        column_count = max(
            [len(record.get("values", [])) for record in ordered_headers],
            default=0,
        )
        if column_count == 0:
            raise RuntimeError("Os headers selecionados nao possuem colunas.")

        reference_header = self._normalize_raw_header_values(
            ordered_headers[0].get("values", []),
            column_count,
        )
        different_sheets = []
        for sheet, record in zip(sheet_infos[1:], ordered_headers[1:]):
            header = self._normalize_raw_header_values(record.get("values", []), column_count)
            if header != reference_header:
                different_sheets.append(f"{sheet['sheet_key']} ({sheet['sheet_name']})")

        if different_sheets:
            raise RuntimeError(
                "Os headers selecionados nao sao iguais entre as abas. "
                f"Divergencias em: {', '.join(different_sheets)}."
            )

        return ordered_headers

    def _build_raw_import_notes(self, header_records, noise_records) -> str | None:
        sections = []

        if header_records:
            header_rows = ", ".join(
                f"#{record['record_number']} ({record['source_label']})"
                for record in header_records
            )
            sections.append(f"Header rows used: {header_rows}")

        if noise_records:
            noise_lines = ["Noise rows ignored:"]
            for record in noise_records:
                noise_lines.append(
                    f"- #{record['record_number']} ({record['source_label']}): "
                    f"{self._format_raw_record_preview(record.get('values', []))}"
                )
            sections.append("\n".join(noise_lines))

        if not sections:
            return None

        return "\n\n".join(sections)

    def prepare_raw_source_for_import(
        self,
        raw_source: dict,
        header_row_numbers=None,
        noise_row_numbers=None,
        require_header_per_sheet: bool = False,
    ) -> dict:
        records = list(raw_source.get("records", []))
        row_numbers = {record["record_number"]: record for record in records}
        header_row_numbers = sorted(set(header_row_numbers or []))
        noise_row_numbers = sorted(set(noise_row_numbers or []))

        missing_rows = sorted(
            set(header_row_numbers).union(noise_row_numbers) - set(row_numbers)
        )
        if missing_rows:
            joined = ", ".join(str(item) for item in missing_rows)
            raise RuntimeError(f"Linhas informadas para header/noise nao existem: {joined}.")

        header_records = [row_numbers[number] for number in header_row_numbers]
        noise_records = [row_numbers[number] for number in noise_row_numbers]
        header_records_for_key_build = header_records
        if require_header_per_sheet:
            ordered_sheet_headers = self._validate_raw_sheet_headers(raw_source, header_records)
            header_records_for_key_build = ordered_sheet_headers[:1]

        ignored_rows = set(header_row_numbers).union(noise_row_numbers)
        data_records = [
            record for record in records
            if record["record_number"] not in ignored_rows
        ]

        column_count = max(
            [len(record.get("values", [])) for record in header_records_for_key_build + data_records],
            default=0,
        )
        header_keys = (
            self._build_raw_header_keys(header_records_for_key_build, column_count)
            if header_records_for_key_build
            else []
        )

        final_records = []
        for record in data_records:
            values = list(record.get("values", []))
            final_record = dict(record)
            final_record["raw_tab"] = self._get_raw_record_tab(record)
            final_record["raw_id"] = self._get_raw_record_id(record)
            if header_keys:
                final_record["values"] = self._map_raw_values_with_header(values, header_keys)
            else:
                final_record["values"] = values
            final_records.append(final_record)

        return {
            "raw_source": {
                "file_name": raw_source["file_name"],
                "file_hash": raw_source["file_hash"],
                "ingested_at": raw_source["ingested_at"],
                "raw_schema": raw_source.get("raw_schema"),
                "source_type": raw_source.get("source_type", "text"),
                "raw_system_columns": self._get_raw_source_system_columns(raw_source),
                "source_count": raw_source.get("source_count", 1),
                "source_files": list(raw_source.get("source_files") or []),
                "records": final_records,
            },
            "version_notes": self._build_raw_import_notes(header_records, noise_records),
            "header_row_numbers": header_row_numbers,
            "noise_row_numbers": noise_row_numbers,
        }

    @staticmethod
    def _detect_text_delimiter(lines):
        sample = "\n".join(line for line in lines[:10] if line.strip())
        if sample:
            try:
                return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                pass

        scores = {candidate: sum(line.count(candidate) for line in lines[:20]) for candidate in [",", ";", "\t", "|"]}
        delimiter = max(scores, key=scores.get)
        return delimiter if scores[delimiter] > 0 else None

    @staticmethod
    def _chunked(items, chunk_size: int):
        for start in range(0, len(items), chunk_size):
            yield items[start:start + chunk_size]

    @staticmethod
    def _excel_engine_for_path(path: Path) -> str:
        return "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"

    @staticmethod
    def _silence_excel_style_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Workbook contains no default style.*",
            category=UserWarning,
        )

    @staticmethod
    def _sheet_key(index: int) -> str:
        letters = []
        while index > 0:
            index, remainder = divmod(index - 1, 26)
            letters.append(chr(ord("A") + remainder))
        return "".join(reversed(letters))

    @classmethod
    def is_excel_file(cls, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in cls.EXCEL_EXTENSIONS

    def get_excel_sheet_names(self, file_path: str, cancel_event=None) -> list[str]:
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"Arquivo nao encontrado: {file_path}")
        if path.suffix.lower() not in self.EXCEL_EXTENSIONS:
            return []

        self._raise_if_cancelled(cancel_event)
        self._silence_excel_style_warnings()
        with pd.ExcelFile(path, engine=self._excel_engine_for_path(path)) as workbook:
            return list(workbook.sheet_names)

    @staticmethod
    def _notify_progress(progress_callback, stage_key: str, progress: float, message: str):
        if progress_callback:
            progress_callback(stage_key, progress, message)

    def _load_text_raw_records(
        self,
        file_bytes: bytes,
        progress_callback=None,
        cancel_event=None,
        include_review_metadata: bool = False,
    ):
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = file_bytes.decode("utf-8", errors="replace")

        lines = text.splitlines()
        delimiter = self._detect_text_delimiter(lines)
        records = []
        total_lines = len(lines)
        report_step = max(total_lines // 20, 1) if total_lines else 1

        self._notify_progress(
            progress_callback,
            "source",
            0,
            f"Arquivo texto identificado. {total_lines} linhas encontradas.",
        )

        if delimiter:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            for values in reader:
                self._raise_if_cancelled(cancel_event)
                line_number = max(int(reader.line_num or 0), 1)
                normalized_values = [self._normalize_raw_value(value) for value in values]
                if self._should_skip_blank_raw_record(normalized_values):
                    continue

                if include_review_metadata:
                    records.append(
                        self._build_raw_review_record(
                            records,
                            normalized_values,
                            source_label=f"linha {line_number}",
                            raw_tab=None,
                            raw_id=line_number,
                            source_row_number=line_number,
                        )
                    )
                else:
                    records.append({
                        "raw_tab": None,
                        "raw_id": line_number,
                        "values": normalized_values,
                    })

                if line_number == total_lines or line_number % report_step == 0:
                    progress = 100 * line_number / max(total_lines, 1)
                    self._notify_progress(
                        progress_callback,
                        "source",
                        progress,
                        f"Lendo arquivo texto: linha {line_number} de {total_lines}.",
                    )
        else:
            for line_number, raw_line in enumerate(lines, start=1):
                self._raise_if_cancelled(cancel_event)
                normalized_values = [self._normalize_raw_value(raw_line)]
                if self._should_skip_blank_raw_record(normalized_values):
                    continue

                if include_review_metadata:
                    records.append(
                        self._build_raw_review_record(
                            records,
                            normalized_values,
                            source_label=f"linha {line_number}",
                            raw_tab=None,
                            raw_id=line_number,
                            source_row_number=line_number,
                        )
                    )
                else:
                    records.append({
                        "raw_tab": None,
                        "raw_id": line_number,
                        "values": normalized_values,
                    })

                if line_number == total_lines or line_number % report_step == 0:
                    progress = 100 * line_number / max(total_lines, 1)
                    self._notify_progress(
                        progress_callback,
                        "source",
                        progress,
                        f"Lendo arquivo texto: linha {line_number} de {total_lines}.",
                    )

        return self._normalize_raw_record_widths(records)

    def _load_excel_raw_records(
        self,
        file_path: str,
        progress_callback=None,
        cancel_event=None,
        include_review_metadata: bool = False,
        selected_sheet_names=None,
    ):
        path = Path(file_path)
        records = []
        sheet_infos = []

        self._silence_excel_style_warnings()
        with pd.ExcelFile(path, engine=self._excel_engine_for_path(path)) as workbook:
            workbook_sheet_names = list(workbook.sheet_names)
            selected_names = list(selected_sheet_names or workbook_sheet_names)
            missing_sheets = [
                sheet_name
                for sheet_name in selected_names
                if sheet_name not in workbook_sheet_names
            ]
            if missing_sheets:
                joined = ", ".join(missing_sheets)
                raise RuntimeError(f"Abas selecionadas nao encontradas no arquivo: {joined}.")

            total_sheets = len(selected_names)
            sheet_indexes = {
                sheet_name: index
                for index, sheet_name in enumerate(workbook_sheet_names, start=1)
            }

            for selected_index, sheet_name in enumerate(selected_names, start=1):
                self._raise_if_cancelled(cancel_event)
                sheet_index = sheet_indexes[sheet_name]
                sheet_key = self._sheet_key(sheet_index)
                sheet_info = {
                    "sheet_name": sheet_name,
                    "sheet_index": sheet_index,
                    "sheet_key": sheet_key,
                    "record_count": 0,
                }
                sheet_infos.append(sheet_info)
                self._notify_progress(
                    progress_callback,
                    "source",
                    100 * (selected_index - 1) / max(total_sheets, 1),
                    f"Lendo aba {sheet_key} ({selected_index} de {total_sheets}): {sheet_name}.",
                )
                frame = pd.read_excel(
                    workbook,
                    sheet_name=sheet_name,
                    header=None,
                    dtype=object,
                    keep_default_na=False,
                )

                if frame.empty:
                    continue

                total_rows = len(frame.index)
                report_step = max(total_rows // 20, 1) if total_rows else 1
                for row_number, row in enumerate(frame.itertuples(index=False, name=None), start=1):
                    self._raise_if_cancelled(cancel_event)
                    normalized_values = [self._normalize_raw_value(value) for value in row]
                    if self._should_skip_blank_raw_record(normalized_values):
                        continue

                    if include_review_metadata:
                        records.append(
                            self._build_raw_review_record(
                                records,
                                normalized_values,
                                source_label=f"aba {sheet_key} - {sheet_name}, linha {row_number}",
                                sheet_name=sheet_name,
                                sheet_index=sheet_index,
                                sheet_key=sheet_key,
                                source_row_number=row_number,
                                raw_tab=sheet_name,
                                raw_id=row_number,
                            )
                        )
                    else:
                        records.append({
                            "raw_tab": sheet_name,
                            "raw_id": row_number,
                            "values": normalized_values,
                        })
                    sheet_info["record_count"] += 1

                    if row_number == total_rows or row_number % report_step == 0:
                        sheet_progress = row_number / max(total_rows, 1)
                        overall_progress = 100 * ((selected_index - 1) + sheet_progress) / max(total_sheets, 1)
                        self._notify_progress(
                            progress_callback,
                            "source",
                            overall_progress,
                            f"Aba {sheet_key} - {sheet_name}: linha {row_number} de {total_rows}.",
                        )

        return records, sheet_infos

    def _load_raw_source(
        self,
        file_path: str,
        raw_schema: str | None = None,
        progress_callback=None,
        cancel_event=None,
        include_review_metadata: bool = False,
        selected_sheet_names=None,
    ):
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"Arquivo nÃ£o encontrado: {file_path}")

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "source", 0, "Lendo arquivo bruto do disco...")
        file_bytes = path.read_bytes()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        extension = path.suffix.lower()
        source_type = "text"
        sheet_infos = []

        if extension in {".csv", ".txt"}:
            records = self._load_text_raw_records(
                file_bytes,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                include_review_metadata=include_review_metadata,
            )
        elif extension in self.EXCEL_EXTENSIONS:
            source_type = "excel"
            records, sheet_infos = self._load_excel_raw_records(
                file_path,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                include_review_metadata=include_review_metadata,
                selected_sheet_names=selected_sheet_names,
            )
        else:
            raise RuntimeError("Formato nÃ£o suportado. Use csv, txt, xls, xlsx ou xlsm.")

        self._notify_progress(
            progress_callback,
            "source",
            100,
            f"Arquivo processado com {len(records)} registros prontos para importacao.",
        )
        ingested_at = datetime.now().astimezone().isoformat(timespec="seconds")

        return {
            "file_name": path.name,
            "file_hash": file_hash,
            "ingested_at": ingested_at,
            "raw_schema": raw_schema,
            "source_type": source_type,
            "source_count": 1,
            "source_files": [
                {
                    "file_name": path.name,
                    "file_hash": file_hash,
                    "ingested_at": ingested_at,
                }
            ],
            "sheets": sheet_infos,
            "records": records,
        }

    @staticmethod
    def _decode_json_bytes(file_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return file_bytes.decode("utf-8", errors="replace")

    @classmethod
    def _sample_json_discovery_indices(cls, total_count: int) -> list[int]:
        if total_count <= cls.JSON_DISCOVERY_SAMPLE_LIMIT:
            return list(range(total_count))

        head_count = min(cls.JSON_DISCOVERY_HEAD_SAMPLES, cls.JSON_DISCOVERY_SAMPLE_LIMIT, total_count)
        remaining_slots = max(cls.JSON_DISCOVERY_SAMPLE_LIMIT - head_count, 0)
        sampled_indices = set(range(head_count))

        if remaining_slots > 0 and total_count > head_count:
            tail_span = total_count - head_count
            for slot in range(remaining_slots):
                offset = int(((slot + 1) * tail_span) / (remaining_slots + 1))
                sampled_indices.add(min(head_count + offset, total_count - 1))

        sampled_indices.add(total_count - 1)
        return sorted(sampled_indices)

    @staticmethod
    def _json_structure_signature(value):
        if isinstance(value, dict):
            return ("object", tuple(sorted(str(key) for key in value.keys())))
        if isinstance(value, list):
            return ("array", len(value))
        return ("primitive", type(value).__name__)

    @staticmethod
    def _format_json_candidate_path(path_segments) -> str:
        if not path_segments:
            return "$"

        result = "$"
        for segment in path_segments:
            if segment == "[]":
                result += "[]"
            else:
                result += f".{segment}"
        return result

    @classmethod
    def _score_json_array_candidate(cls, values, path_segments) -> dict | None:
        if not isinstance(values, list) or not values:
            return None

        sample_indices = cls._sample_json_discovery_indices(len(values))
        sampled_items = [values[index] for index in sample_indices]
        sampled_count = len(sampled_items)
        dict_items = [item for item in sampled_items if isinstance(item, dict)]
        list_items = [item for item in sampled_items if isinstance(item, list)]
        primitive_count = sampled_count - len(dict_items) - len(list_items)

        key_union = set()
        common_keys = None
        if dict_items:
            for item in dict_items:
                item_keys = {str(key) for key in item.keys()}
                key_union.update(item_keys)
                common_keys = item_keys if common_keys is None else common_keys.intersection(item_keys)

        dominant_ratio = max(len(dict_items), len(list_items), primitive_count) / max(sampled_count, 1)
        leaf_name = ""
        for segment in reversed(path_segments):
            if segment != "[]":
                leaf_name = str(segment)
                break

        score = len(values) * 4
        score += 450 if len(values) > 1 else -250

        if dict_items:
            score += 3000
            score += int((len(dict_items) / sampled_count) * 700)
            score += len(common_keys or set()) * 30
            score += len(key_union) * 8
        elif list_items:
            score += 1800
            score += int((len(list_items) / sampled_count) * 400)
        else:
            score += 250
            score += int((primitive_count / sampled_count) * 120)

        normalized_leaf = leaf_name.strip().lower()
        if normalized_leaf in {"data", "items", "records", "results", "rows", "features", "observations", "samples"}:
            score += 220
        if normalized_leaf in {"metadata", "meta", "info", "header", "headers", "config", "attributes"}:
            score -= 420
        if dominant_ratio < 0.6:
            score -= 900

        non_array_depth = len([segment for segment in path_segments if segment != "[]"])
        score -= max(non_array_depth - 6, 0) * 5

        return {
            "items": values,
            "path_segments": list(path_segments),
            "path_label": cls._format_json_candidate_path(path_segments),
            "score": score,
            "length": len(values),
            "dict_count": len(dict_items),
            "list_count": len(list_items),
        }

    @classmethod
    def _collect_json_array_candidates(cls, value, path_segments=None, candidates=None):
        if path_segments is None:
            path_segments = []
        if candidates is None:
            candidates = []

        if isinstance(value, dict):
            for child_key, child_value in value.items():
                cls._collect_json_array_candidates(
                    child_value,
                    path_segments + [str(child_key)],
                    candidates,
                )
            return candidates

        if not isinstance(value, list):
            return candidates

        candidate = cls._score_json_array_candidate(value, path_segments)
        if candidate is not None:
            candidates.append(candidate)

        seen_signatures = set()
        for index in cls._sample_json_discovery_indices(len(value)):
            item = value[index]
            signature = cls._json_structure_signature(item)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            cls._collect_json_array_candidates(item, path_segments + ["[]"], candidates)

        return candidates

    @classmethod
    def _select_json_records_candidate(cls, json_data):
        candidates = cls._collect_json_array_candidates(json_data)
        if not candidates:
            return None

        return max(
            candidates,
            key=lambda candidate: (
                candidate["score"],
                candidate["dict_count"],
                candidate["list_count"],
                candidate["length"],
                -len(candidate["path_segments"]),
            ),
        )

    def _normalize_json_cell_value(self, value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return self._normalize_raw_value(value)

    def _flatten_json_object_fields(self, value, bucket: dict, prefix: str = ""):
        if isinstance(value, dict):
            if not value and prefix:
                bucket[prefix] = None
                return

            for child_key, child_value in value.items():
                child_key_text = str(child_key).strip() or "field"
                full_key = child_key_text if not prefix else f"{prefix}.{child_key_text}"
                if isinstance(child_value, dict):
                    self._flatten_json_object_fields(child_value, bucket, prefix=full_key)
                elif isinstance(child_value, list):
                    bucket[full_key] = self._normalize_json_cell_value(child_value)
                else:
                    bucket[full_key] = self._normalize_raw_value(child_value)
            return

        bucket[prefix or "value"] = self._normalize_json_cell_value(value)

    def _flatten_json_record_values(self, item) -> dict:
        if isinstance(item, dict):
            flattened = {}
            self._flatten_json_object_fields(item, flattened)
            return flattened or {"value": None}

        if isinstance(item, list):
            row_values = {}
            for index, value in enumerate(item, start=1):
                row_values[f"column_{index}"] = self._normalize_json_cell_value(value)
            return row_values or {"value": None}

        return {"value": self._normalize_json_cell_value(item)}

    def prepare_json_raw_source_for_import(
        self,
        file_path: str,
        raw_schema: str | None = None,
        progress_callback=None,
        cancel_event=None,
    ) -> dict:
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"Arquivo nao encontrado: {file_path}")

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "source", 0, "Lendo arquivo JSON do disco...")
        file_bytes = path.read_bytes()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "source", 12, "Decodificando conteudo JSON...")
        json_text = self._decode_json_bytes(file_bytes)
        try:
            json_data = json.loads(json_text)
        except Exception as exc:
            raise RuntimeError(f"Falha ao interpretar o JSON selecionado: {exc}") from exc

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "source", 24, "Identificando o bloco repetitivo principal...")
        candidate = self._select_json_records_candidate(json_data)
        if candidate is None or not candidate.get("items"):
            raise RuntimeError(
                "Nao foi encontrado um bloco repetitivo de dados no JSON. "
                "O arquivo parece conter apenas metadados ou estruturas sem repeticao."
            )

        items = list(candidate["items"])
        total_items = len(items)
        records = []
        report_step = max(total_items // 20, 1) if total_items else 1

        for index, item in enumerate(items, start=1):
            self._raise_if_cancelled(cancel_event)
            row_values = self._flatten_json_record_values(item)
            if self._should_skip_blank_raw_record(list(row_values.values())):
                continue
            records.append({"values": row_values})

            if index == total_items or index % report_step == 0:
                progress = 24 + (70 * index / max(total_items, 1))
                self._notify_progress(
                    progress_callback,
                    "source",
                    progress,
                    f"Convertendo JSON em linhas raw: registro {index} de {total_items}.",
                )

        if not records:
            raise RuntimeError(
                "O bloco repetitivo encontrado no JSON nao gerou linhas com dados utilizaveis para importar."
            )

        self._notify_progress(
            progress_callback,
            "source",
            100,
            f"JSON convertido em {len(records)} registro(s) a partir de {candidate['path_label']}.",
        )

        version_notes = (
            "JSON raw import path: "
            f"{candidate['path_label']} ({len(records)} registro(s) convertidos)."
        )
        ingested_at = datetime.now().astimezone().isoformat(timespec="seconds")

        return {
            "raw_source": {
                "file_name": path.name,
                "file_hash": file_hash,
                "ingested_at": ingested_at,
                "raw_schema": raw_schema,
                "source_type": "json",
                "raw_system_columns": list(self.RAW_BASE_COLUMNS),
                "json_data_path": candidate["path_label"],
                "source_count": 1,
                "source_files": [
                    {
                        "file_name": path.name,
                        "file_hash": file_hash,
                        "ingested_at": ingested_at,
                        "json_data_path": candidate["path_label"],
                    }
                ],
                "records": records,
            },
            "version_notes": version_notes,
        }

    def load_raw_source_for_review(
        self,
        file_path: str,
        raw_schema: str | None = None,
        progress_callback=None,
        cancel_event=None,
        selected_sheet_names=None,
    ) -> dict:
        raw_source = self._load_raw_source(
            file_path,
            raw_schema=raw_schema,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            include_review_metadata=True,
            selected_sheet_names=selected_sheet_names,
        )
        raw_source["review_candidates"] = self.build_raw_review_candidates(raw_source["records"])
        raw_source["preview_count"] = len(raw_source["records"])
        raw_source["total_records"] = len(raw_source["records"])
        return raw_source

    def _get_present_raw_columns(
        self,
        database_name: str,
        full_table_name: str,
        cancel_event=None,
    ) -> set[str]:
        return {
            str(column.get("name") or "").strip()
            for column in self.get_table_column_definitions(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        }

    def load_imported_raw_source_for_review(
        self,
        database_name: str,
        full_table_name: str,
        file_name: str,
        file_hash: str | None = None,
        ingested_at: str | None = None,
        progress_callback=None,
        cancel_event=None,
    ) -> dict:
        schema_name, table_name = split_table_name(full_table_name)
        present_columns = self._get_present_raw_columns(database_name, full_table_name, cancel_event=cancel_event)
        raw_tab_sql = "COALESCE(raw_tab, '')" if "raw_tab" in present_columns else "''"
        raw_id_sql = "COALESCE(raw_id::text, '')" if "raw_id" in present_columns else "''"
        sql = f"""
WITH numbered AS (
    SELECT
        row_number() OVER (ORDER BY ctid) AS record_number,
        count(*) OVER () AS total_count,
        COALESCE(raw_hash, '') AS raw_hash,
        COALESCE(raw_ingested_at::text, '') AS raw_ingested_at,
        {raw_tab_sql} AS raw_tab,
        {raw_id_sql} AS raw_id,
        COALESCE(raw::text, '') AS raw_text
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
)
SELECT
    record_number,
    total_count,
    raw_hash,
    raw_ingested_at,
    raw_tab,
    raw_id,
    raw_text
FROM numbered
WHERE record_number <= {self.RAW_REVIEW_PREVIEW_LIMIT}
ORDER BY record_number;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        self._notify_progress(
            progress_callback,
            "review",
            0,
            "Lendo registros Raw importados para analise...",
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)

        records = []
        total_rows = len(rows)
        report_step = max(total_rows // 20, 1) if total_rows else 1
        detected_hash = file_hash
        detected_ingested_at = ingested_at
        total_count = 0

        for index, row in enumerate(rows, start=1):
            self._raise_if_cancelled(cancel_event)
            total_count = int(row[1]) if len(row) > 1 and row[1].strip() else total_count
            current_hash = row[2].strip() if len(row) > 2 else ""
            current_ingested_at = row[3].strip() if len(row) > 3 else ""
            raw_tab = row[4].strip() if len(row) > 4 else ""
            raw_id_value = row[5].strip() if len(row) > 5 else ""
            raw_payload = row[6] if len(row) > 6 else ""
            raw_id = self._parse_raw_id_value(raw_id_value)

            if not detected_hash and current_hash:
                detected_hash = current_hash
            if not detected_ingested_at and current_ingested_at:
                detected_ingested_at = current_ingested_at

            payload = json.loads(raw_payload) if raw_payload else {}
            values = payload.get("values", [])
            if isinstance(values, dict):
                values = list(values.values())
            elif not isinstance(values, list):
                values = [values]

            records.append({
                "record_number": index,
                "source_label": self._build_imported_raw_source_label(index, raw_tab, raw_id),
                "raw_tab": raw_tab or None,
                "raw_id": raw_id,
                "source_row_number": raw_id,
                "values": values,
            })

            if index == total_rows or index % report_step == 0:
                progress = 60 * index / max(total_rows, 1)
                self._notify_progress(
                    progress_callback,
                    "review",
                    progress,
                    f"Lendo Raw importado: registro {index} de {total_rows}.",
                )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "review",
            70,
            "Detectando candidatos automaticos de header e noise...",
        )
        review_candidates = self.build_raw_review_candidates(records)
        self._notify_progress(
            progress_callback,
            "review",
            100,
            "Analise pronta para revisao do usuario.",
        )

        return {
            "file_name": file_name,
            "file_hash": detected_hash or "",
            "ingested_at": detected_ingested_at or "",
            "records": records,
            "preview_count": len(records),
            "total_records": total_count or len(records),
            "review_candidates": review_candidates,
        }

    def get_imported_raw_records_by_numbers(
        self,
        database_name: str,
        full_table_name: str,
        row_numbers,
        cancel_event=None,
    ) -> list[dict]:
        selected_numbers = sorted(set(int(number) for number in row_numbers or []))
        if not selected_numbers:
            return []

        schema_name, table_name = split_table_name(full_table_name)
        present_columns = self._get_present_raw_columns(database_name, full_table_name, cancel_event=cancel_event)
        raw_tab_sql = "COALESCE(raw_tab, '')" if "raw_tab" in present_columns else "''"
        raw_id_sql = "COALESCE(raw_id::text, '')" if "raw_id" in present_columns else "''"
        numbers_sql = ", ".join(str(number) for number in selected_numbers)
        sql = f"""
WITH numbered AS (
    SELECT
        row_number() OVER (ORDER BY ctid) AS record_number,
        {raw_tab_sql} AS raw_tab,
        {raw_id_sql} AS raw_id,
        COALESCE(raw::text, '') AS raw_text
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
)
SELECT record_number, raw_tab, raw_id, raw_text
FROM numbered
WHERE record_number = ANY(ARRAY[{numbers_sql}]::integer[])
ORDER BY record_number;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)

        results = []
        for row in rows:
            record_number = int(row[0])
            raw_tab = row[1].strip() if len(row) > 1 else ""
            raw_id_value = row[2].strip() if len(row) > 2 else ""
            raw_payload = row[3] if len(row) > 3 else ""
            raw_id = self._parse_raw_id_value(raw_id_value)
            payload = json.loads(raw_payload) if raw_payload else {}
            values = payload.get("values", [])
            if isinstance(values, dict):
                values = list(values.values())
            elif not isinstance(values, list):
                values = [values]

            results.append({
                "record_number": record_number,
                "source_label": self._build_imported_raw_source_label(record_number, raw_tab, raw_id),
                "raw_tab": raw_tab or None,
                "raw_id": raw_id,
                "source_row_number": raw_id,
                "values": values,
            })

        return results

    def finalize_imported_raw_review(
        self,
        database_name: str,
        full_table_name: str,
        raw_source: dict,
        header_row_numbers=None,
        noise_row_numbers=None,
        progress_callback=None,
        cancel_event=None,
    ) -> dict:
        header_row_numbers = sorted(set(int(number) for number in (header_row_numbers or [])))
        noise_row_numbers = sorted(set(int(number) for number in (noise_row_numbers or [])))
        excluded_rows = sorted(set(header_row_numbers).union(noise_row_numbers))

        if not excluded_rows:
            return {
                "raw_source": {
                    "file_name": raw_source["file_name"],
                    "file_hash": raw_source["file_hash"],
                    "ingested_at": raw_source["ingested_at"],
                    "raw_schema": raw_source.get("raw_schema"),
                    "source_count": raw_source.get("source_count", 1),
                    "source_files": list(raw_source.get("source_files") or []),
                    "row_count": self._raw_source_row_count(raw_source),
                },
                "version_notes": None,
            }

        self._notify_progress(
            progress_callback,
            "review",
            5,
            "Carregando linhas selecionadas para consolidar header e noise...",
        )
        selected_records = self.get_imported_raw_records_by_numbers(
            database_name,
            full_table_name,
            excluded_rows,
            cancel_event=cancel_event,
        )
        selected_map = {record["record_number"]: record for record in selected_records}
        missing_rows = [number for number in excluded_rows if number not in selected_map]
        if missing_rows:
            joined = ", ".join(str(item) for item in missing_rows)
            raise RuntimeError(f"As linhas selecionadas nao existem mais na tabela: {joined}.")

        header_records = [selected_map[number] for number in header_row_numbers]
        noise_records = [selected_map[number] for number in noise_row_numbers]
        version_notes = self._build_raw_import_notes(header_records, noise_records)
        row_count = max(self._raw_source_row_count(raw_source) - len(excluded_rows), 0)

        schema_name, table_name = split_table_name(full_table_name)
        self.get_table_row_count(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        statements = ["BEGIN;"]

        excluded_sql = ", ".join(str(number) for number in excluded_rows)
        statements.append(
            f"""
WITH numbered AS (
    SELECT ctid, row_number() OVER (ORDER BY ctid) AS record_number
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
)
DELETE FROM {sql_ident(schema_name)}.{sql_ident(table_name)} AS target
USING numbered
WHERE target.ctid = numbered.ctid
  AND numbered.record_number = ANY(ARRAY[{excluded_sql}]::integer[]);
""".strip()
        )

        if header_records:
            self._notify_progress(
                progress_callback,
                "review",
                30,
                "Gerando chaves a partir do header selecionado...",
            )
            column_count = max(
                [len(record.get("values", [])) for record in header_records],
                default=0,
            )
            header_keys = self._build_raw_header_keys(header_records, column_count)
            if header_keys:
                values_sql = ", ".join(
                    f"({index}, {sql_literal(key)})"
                    for index, key in enumerate(header_keys, start=1)
                )
                statements.append(
                    f"""
UPDATE {sql_ident(schema_name)}.{sql_ident(table_name)} AS target
SET raw = jsonb_build_object(
    'values',
    (
        SELECT jsonb_object_agg(keys.key, COALESCE((target.raw->'values') -> (keys.idx - 1), 'null'::jsonb))
        FROM (VALUES {values_sql}) AS keys(idx, key)
    )
);
""".strip()
                )

        statements.append(
            self._build_table_row_count_delta_sql(
                schema_name,
                table_name,
                -len(excluded_rows),
            )
        )
        statements.append("COMMIT;")
        sql = "\n".join(statements) + "\n"

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "review",
            55,
            "Aplicando refinamentos finais do Raw diretamente no banco...",
        )
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        self._notify_progress(
            progress_callback,
            "review",
            100,
            "Refinamentos finais aplicados com sucesso.",
        )

        return {
            "raw_source": {
                "file_name": raw_source["file_name"],
                "file_hash": raw_source["file_hash"],
                "ingested_at": raw_source["ingested_at"],
                "raw_schema": raw_source.get("raw_schema"),
                "source_count": raw_source.get("source_count", 1),
                "source_files": list(raw_source.get("source_files") or []),
                "row_count": row_count,
            },
            "version_notes": version_notes,
        }

    def get_table_column_definitions(
        self,
        database_name: str,
        full_table_name: str,
        cancel_event=None,
        trace_context: dict | None = None,
    ):
        started_at = time.perf_counter()
        self._trace_table_open(trace_context, f"get_table_column_definitions(): iniciando para {full_table_name}")
        schema_name, table_name = split_table_name(full_table_name)

        sql = f"""
SELECT
    a.attname,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted_type,
    a.attnotnull,
    COALESCE(pg_get_expr(ad.adbin, ad.adrelid), '')
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
WHERE n.nspname = {sql_literal(schema_name)}
  AND c.relname = {sql_literal(table_name)}
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(
            command,
            cancel_event=cancel_event,
            trace_context=trace_context,
            trace_label=f"get_table_column_definitions[{full_table_name}]",
        )
        parse_started_at = time.perf_counter()
        _headers, rows = self.parse_csv_output(output)
        self._trace_table_open(
            trace_context,
            f"get_table_column_definitions(): parse do CSV em {(time.perf_counter() - parse_started_at) * 1000:.1f} ms; rows={len(rows)}",
        )

        columns = []
        for row in rows:
            values = self._normalize_csv_row(
                row,
                4,
                trace_context=trace_context,
                trace_label=f"get_table_column_definitions[{full_table_name}]",
            )
            columns.append({
                "name": values[0],
                "type": values[1],
                "not_null": values[2].strip().lower() in {"t", "true", "1"},
                "default": values[3].strip(),
            })
        self._trace_table_open(
            trace_context,
            f"get_table_column_definitions(): conclu?do em {(time.perf_counter() - started_at) * 1000:.1f} ms; columns={len(columns)}",
        )
        return columns

    @staticmethod
    def _decode_referential_action(action_code: str) -> str:
        normalized_code = str(action_code or "").strip().lower()
        mapping = {
            "a": "NO ACTION",
            "r": "RESTRICT",
            "c": "CASCADE",
            "n": "SET NULL",
            "d": "SET DEFAULT",
        }
        return mapping.get(normalized_code, normalized_code.upper())

    @staticmethod
    def _build_table_constraint_name(table_name: str, column_name: str, suffix: str) -> str:
        raw_name = f"{table_name}_{column_name}_{suffix}"
        normalized_name = re.sub(r"[^a-z0-9_]+", "_", raw_name.lower())
        normalized_name = re.sub(r"_+", "_", normalized_name).strip("_") or suffix
        return normalized_name[:63]

    @staticmethod
    def _build_constraint_progress_application_name(
        operation_kind: str,
        schema_name: str,
        table_name: str,
        column_name: str,
    ) -> str:
        parts = [
            "pgdm",
            slugify(operation_kind) or "constraint",
            slugify(schema_name) or "schema",
            slugify(table_name) or "table",
            slugify(column_name) or "column",
            str(int(time.time() * 1000)),
        ]
        return "_".join(parts)[:63]

    @staticmethod
    def _safe_int_from_sql(value) -> int:
        normalized = str(value or "").strip()
        if not normalized:
            return 0
        try:
            return int(normalized)
        except Exception:
            return 0

    def _fetch_create_index_progress_snapshot(self, database_name: str, application_name: str, cancel_event=None) -> dict | None:
        sql = f"""
SELECT
    COALESCE(activity.pid::text, ''),
    COALESCE(activity.state, ''),
    COALESCE(activity.wait_event_type, ''),
    COALESCE(activity.wait_event, ''),
    COALESCE(progress.phase, ''),
    COALESCE(progress.blocks_done::text, ''),
    COALESCE(progress.blocks_total::text, ''),
    COALESCE(progress.tuples_done::text, ''),
    COALESCE(progress.tuples_total::text, ''),
    COALESCE(progress.partitions_done::text, ''),
    COALESCE(progress.partitions_total::text, ''),
    COALESCE(progress.lockers_done::text, ''),
    COALESCE(progress.lockers_total::text, '')
FROM pg_stat_activity AS activity
LEFT JOIN pg_stat_progress_create_index AS progress ON progress.pid = activity.pid
WHERE activity.application_name = {sql_literal(application_name)}
ORDER BY activity.backend_start DESC
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        try:
            output = self.run_remote_command(command, cancel_event=cancel_event)
        except Exception:
            return None
        _headers, rows = self.parse_csv_output(output)
        if not rows:
            return None
        values = self._normalize_csv_row(
            rows[0],
            13,
            trace_label=f"create_index_progress[{application_name}]",
        )
        return {
            "pid": str(values[0] or "").strip(),
            "state": str(values[1] or "").strip(),
            "wait_event_type": str(values[2] or "").strip(),
            "wait_event": str(values[3] or "").strip(),
            "phase": str(values[4] or "").strip(),
            "blocks_done": self._safe_int_from_sql(values[5]),
            "blocks_total": self._safe_int_from_sql(values[6]),
            "tuples_done": self._safe_int_from_sql(values[7]),
            "tuples_total": self._safe_int_from_sql(values[8]),
            "partitions_done": self._safe_int_from_sql(values[9]),
            "partitions_total": self._safe_int_from_sql(values[10]),
            "lockers_done": self._safe_int_from_sql(values[11]),
            "lockers_total": self._safe_int_from_sql(values[12]),
        }

    def _build_create_index_progress_status(self, snapshot: dict | None, default_message: str) -> tuple[float, str]:
        if not snapshot:
            return 8.0, default_message

        wait_event_type = str(snapshot.get("wait_event_type") or "").strip().lower()
        wait_event = str(snapshot.get("wait_event") or "").strip()
        phase = str(snapshot.get("phase") or "").strip()
        if phase:
            progress_pairs = [
                (snapshot.get("blocks_done", 0), snapshot.get("blocks_total", 0), "blocos"),
                (snapshot.get("tuples_done", 0), snapshot.get("tuples_total", 0), "tuplas"),
                (snapshot.get("partitions_done", 0), snapshot.get("partitions_total", 0), "particoes"),
                (snapshot.get("lockers_done", 0), snapshot.get("lockers_total", 0), "locks"),
            ]
            detail_text = "progresso interno indisponivel"
            ratio = None
            for done_value, total_value, label in progress_pairs:
                if int(total_value or 0) > 0:
                    ratio = min(max(float(done_value) / float(total_value), 0.0), 1.0)
                    detail_text = f"{int(done_value):,}/{int(total_value):,} {label}"
                    break
            progress = 18.0 if ratio is None else 18.0 + (ratio * 74.0)
            return progress, f"Criando indice da primary key: {phase} ({detail_text})."

        if wait_event_type == "lock":
            wait_suffix = f" ({wait_event})" if wait_event else ""
            return 6.0, f"Aguardando lock na tabela para iniciar a primary key{wait_suffix}."

        return 12.0, default_message

    def get_table_column_constraint_metadata(
        self,
        database_name: str,
        full_table_name: str,
        cancel_event=None,
        trace_context: dict | None = None,
    ) -> dict:
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT
    con.contype,
    con.conname,
    COALESCE(array_length(con.conkey, 1), 0) AS key_count,
    COALESCE(src.ord, 0) AS ordinal_position,
    COALESCE(src_att.attname, '') AS column_name,
    COALESCE(ref_ns.nspname, '') AS ref_schema,
    COALESCE(ref_cls.relname, '') AS ref_table,
    COALESCE(ref_att.attname, '') AS ref_column,
    COALESCE(con.confupdtype::text, '') AS update_action,
    COALESCE(con.confdeltype::text, '') AS delete_action
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
LEFT JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS src(attnum, ord) ON TRUE
LEFT JOIN pg_attribute src_att ON src_att.attrelid = con.conrelid AND src_att.attnum = src.attnum
LEFT JOIN pg_class ref_cls ON ref_cls.oid = con.confrelid
LEFT JOIN pg_namespace ref_ns ON ref_ns.oid = ref_cls.relnamespace
LEFT JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS ref(attnum, ord) ON ref.ord = src.ord
LEFT JOIN pg_attribute ref_att ON ref_att.attrelid = con.confrelid AND ref_att.attnum = ref.attnum
WHERE ns.nspname = {sql_literal(schema_name)}
  AND cls.relname = {sql_literal(table_name)}
  AND con.contype IN ('p', 'f')
ORDER BY con.contype, con.conname, src.ord;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(
            command,
            cancel_event=cancel_event,
            trace_context=trace_context,
            trace_label=f"get_table_column_constraint_metadata[{full_table_name}]",
        )
        _headers, rows = self.parse_csv_output(output)
        if not rows:
            return {}

        grouped_constraints = {}
        for row in rows:
            values = self._normalize_csv_row(
                row,
                10,
                trace_context=trace_context,
                trace_label=f"get_table_column_constraint_metadata[{full_table_name}]",
            )
            constraint_type = str(values[0] or "").strip().lower()
            constraint_name = str(values[1] or "").strip()
            key_count = int(str(values[2] or "0").strip() or "0")
            column_name = str(values[4] or "").strip()
            if not constraint_type or not constraint_name or not column_name:
                continue

            grouped_constraint = grouped_constraints.setdefault(
                constraint_name,
                {
                    "constraint_type": constraint_type,
                    "constraint_name": constraint_name,
                    "key_count": key_count,
                    "columns": [],
                    "references": [],
                    "on_update": self._decode_referential_action(values[8]),
                    "on_delete": self._decode_referential_action(values[9]),
                },
            )
            grouped_constraint["columns"].append(column_name)
            if constraint_type == "f":
                reference_table_name = ""
                if values[5] and values[6]:
                    reference_table_name = f"{values[5]}.{values[6]}"
                grouped_constraint["references"].append(
                    {
                        "column_name": column_name,
                        "referenced_table_name": reference_table_name,
                        "referenced_column_name": str(values[7] or "").strip(),
                    }
                )

        metadata = {}
        for grouped_constraint in grouped_constraints.values():
            constraint_type = grouped_constraint["constraint_type"]
            if constraint_type == "p":
                primary_key_columns = list(grouped_constraint["columns"])
                for column_name in primary_key_columns:
                    column_metadata = metadata.setdefault(
                        column_name,
                        {
                            "is_primary_key": False,
                            "primary_key_name": "",
                            "primary_key_columns": [],
                            "is_foreign_key": False,
                            "foreign_keys": [],
                        },
                    )
                    column_metadata["is_primary_key"] = True
                    column_metadata["primary_key_name"] = grouped_constraint["constraint_name"]
                    column_metadata["primary_key_columns"] = primary_key_columns
            elif constraint_type == "f":
                for reference in grouped_constraint["references"]:
                    column_name = reference["column_name"]
                    column_metadata = metadata.setdefault(
                        column_name,
                        {
                            "is_primary_key": False,
                            "primary_key_name": "",
                            "primary_key_columns": [],
                            "is_foreign_key": False,
                            "foreign_keys": [],
                        },
                    )
                    column_metadata["is_foreign_key"] = True
                    column_metadata["foreign_keys"].append(
                        {
                            "constraint_name": grouped_constraint["constraint_name"],
                            "key_count": grouped_constraint["key_count"],
                            "referenced_table_name": reference["referenced_table_name"],
                            "referenced_column_name": reference["referenced_column_name"],
                            "on_update": grouped_constraint["on_update"],
                            "on_delete": grouped_constraint["on_delete"],
                        }
                    )

        return metadata

    def list_foreign_key_reference_options(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        cancel_event=None,
    ) -> list[dict]:
        column_definitions = self.get_table_column_definitions(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        source_definition = next(
            (item for item in column_definitions if str(item.get("name") or "").strip() == column_name),
            None,
        )
        if not source_definition:
            raise RuntimeError(f"A coluna {column_name} nao existe em {full_table_name}.")

        source_data_type = self._resolve_dictionary_usage_column_type(
            source_definition.get("type"),
            source_definition.get("default"),
        ) or self._normalize_data_type_name(source_definition.get("type"))
        normalized_source_type = self._normalize_data_type_name(source_data_type)
        excluded_schemas = [
            "'pg_catalog'",
            "'information_schema'",
            sql_literal(CONTROL_SCHEMA),
            sql_literal(LEGACY_CONTROL_SCHEMA),
        ]
        sql = f"""
SELECT
    ns.nspname || '.' || cls.relname AS full_table_name,
    att.attname AS column_name,
    pg_catalog.format_type(att.atttypid, att.atttypmod) AS formatted_type,
    con.contype,
    con.conname
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS src(attnum, ord) ON TRUE
JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = src.attnum
WHERE con.contype IN ('p', 'u')
  AND COALESCE(array_length(con.conkey, 1), 0) = 1
  AND ns.nspname NOT IN ({", ".join(excluded_schemas)})
ORDER BY
    ns.nspname,
    cls.relname,
    CASE WHEN con.contype = 'p' THEN 0 ELSE 1 END,
    att.attname;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        reference_options = []
        for row in rows:
            values = self._normalize_csv_row(
                row,
                5,
                trace_label=f"list_foreign_key_reference_options[{full_table_name}.{column_name}]",
            )
            target_type = self._normalize_data_type_name(values[2])
            if target_type != normalized_source_type:
                continue
            reference_options.append(
                {
                    "full_table_name": str(values[0] or "").strip(),
                    "column_name": str(values[1] or "").strip(),
                    "data_type": str(values[2] or "").strip(),
                    "constraint_type": "primary key" if str(values[3] or "").strip().lower() == "p" else "unique",
                    "constraint_name": str(values[4] or "").strip(),
                }
            )

        return reference_options

    def add_primary_key_constraint(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        cancel_event=None,
        progress_callback=None,
    ) -> dict:
        schema_name, table_name = split_table_name(full_table_name)
        self._notify_progress(progress_callback, "prepare", 5, "Validando estrutura da tabela para criar a primary key...")
        existing_columns = {
            str(item.get("name") or "").strip()
            for item in self.get_table_column_definitions(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        }
        if column_name not in existing_columns:
            raise RuntimeError(f"A coluna {column_name} nao existe em {full_table_name}.")

        constraint_metadata = self.get_table_column_constraint_metadata(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        existing_primary_key_columns = sorted(
            column
            for column, metadata in constraint_metadata.items()
            if metadata.get("is_primary_key")
        )
        if existing_primary_key_columns:
            if column_name in existing_primary_key_columns:
                raise RuntimeError(f"A coluna {column_name} ja faz parte da primary key da tabela.")
            raise RuntimeError(
                "A tabela ja possui primary key: " + ", ".join(existing_primary_key_columns) + "."
            )

        self._notify_progress(progress_callback, "prepare", 100, "Estrutura validada. Iniciando criacao da primary key...")
        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        constraint_name = self._build_table_constraint_name(table_name, column_name, "pk")
        application_name = self._build_constraint_progress_application_name("pk", schema_name, table_name, column_name)
        sql = "\n".join(
            [
                "BEGIN;",
                "SET LOCAL lock_timeout = '0';",
                "SET LOCAL statement_timeout = '0';",
                (
                    f"ALTER TABLE {qualified_table} "
                    f"ADD CONSTRAINT {sql_ident(constraint_name)} PRIMARY KEY ({sql_ident(column_name)});"
                ),
                "COMMIT;",
            ]
        ) + "\n"
        command = (
            f"export PGAPPNAME={shlex.quote(application_name)}; "
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )

        self._notify_progress(
            progress_callback,
            "execute",
            0,
            "Solicitando lock e iniciando a criacao da primary key. Em tabelas grandes isso pode levar tempo.",
        )
        worker_error = {}
        finished = threading.Event()

        def run_primary_key_command():
            try:
                self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
            except Exception as exc:
                worker_error["error"] = exc
            finally:
                finished.set()

        worker = threading.Thread(target=run_primary_key_command, daemon=True)
        worker.start()
        last_progress = None
        last_message = None
        try:
            while not finished.wait(1.0):
                self._raise_if_cancelled(cancel_event)
                snapshot = self._fetch_create_index_progress_snapshot(
                    database_name,
                    application_name,
                    cancel_event=cancel_event,
                )
                progress_value, message = self._build_create_index_progress_status(
                    snapshot,
                    "Aguardando inicio da criacao da primary key...",
                )
                if progress_value != last_progress or message != last_message:
                    self._notify_progress(progress_callback, "execute", progress_value, message)
                    last_progress = progress_value
                    last_message = message
        finally:
            worker.join(timeout=1.0)

        if worker_error.get("error") is not None:
            raise worker_error["error"]

        self._notify_progress(progress_callback, "execute", 100, "Primary key criada no banco com sucesso.")
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
            "constraint_name": constraint_name,
            "sql_recipe": sql,
        }

    def add_foreign_key_constraint(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        referenced_table_name: str,
        referenced_column_name: str,
        cancel_event=None,
        progress_callback=None,
    ) -> dict:
        schema_name, table_name = split_table_name(full_table_name)
        self._notify_progress(progress_callback, "prepare", 5, "Validando estrutura da tabela para criar a foreign key...")
        source_columns = {
            str(item.get("name") or "").strip(): item
            for item in self.get_table_column_definitions(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        }
        if column_name not in source_columns:
            raise RuntimeError(f"A coluna {column_name} nao existe em {full_table_name}.")

        constraint_metadata = self.get_table_column_constraint_metadata(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        existing_foreign_keys = list((constraint_metadata.get(column_name) or {}).get("foreign_keys") or [])
        if existing_foreign_keys:
            current_links = [
                (
                    f"{item['referenced_table_name']}.{item['referenced_column_name']}"
                    if item.get("referenced_table_name") and item.get("referenced_column_name")
                    else item.get("constraint_name", "(sem referencia)")
                )
                for item in existing_foreign_keys
            ]
            raise RuntimeError(
                f"A coluna {column_name} ja possui foreign key: " + ", ".join(current_links) + "."
            )

        reference_options = self.list_foreign_key_reference_options(
            database_name,
            full_table_name,
            column_name,
            cancel_event=cancel_event,
        )
        selected_reference = next(
            (
                option
                for option in reference_options
                if option["full_table_name"] == referenced_table_name
                and option["column_name"] == referenced_column_name
            ),
            None,
        )
        if not selected_reference:
            raise RuntimeError(
                "A referencia informada nao esta disponivel para a coluna selecionada. "
                "Escolha uma tabela/coluna compativel."
            )

        reference_schema_name, reference_table_only_name = split_table_name(referenced_table_name)
        source_type = self._normalize_data_type_name(
            self._resolve_dictionary_usage_column_type(
                source_columns[column_name].get("type"),
                source_columns[column_name].get("default"),
            )
        )
        target_type = self._normalize_data_type_name(selected_reference.get("data_type"))
        if source_type != target_type:
            raise RuntimeError(
                f"Tipos incompativeis para foreign key: {column_name} ({source_type}) -> "
                f"{referenced_table_name}.{referenced_column_name} ({target_type})."
            )

        self._notify_progress(progress_callback, "prepare", 100, "Estrutura validada. Iniciando criacao da foreign key...")
        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        qualified_reference_table = f"{sql_ident(reference_schema_name)}.{sql_ident(reference_table_only_name)}"
        constraint_name = self._build_table_constraint_name(table_name, column_name, "fkey")
        sql = "\n".join(
            [
                "BEGIN;",
                "SET LOCAL lock_timeout = '0';",
                "SET LOCAL statement_timeout = '0';",
                (
                    f"ALTER TABLE {qualified_table} "
                    f"ADD CONSTRAINT {sql_ident(constraint_name)} "
                    f"FOREIGN KEY ({sql_ident(column_name)}) "
                    f"REFERENCES {qualified_reference_table} ({sql_ident(referenced_column_name)}) "
                    "ON UPDATE CASCADE ON DELETE CASCADE;"
                ),
                "COMMIT;",
            ]
        ) + "\n"
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self._notify_progress(progress_callback, "execute", 10, "Aplicando foreign key com CASCADE no banco...")
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        self._notify_progress(progress_callback, "execute", 100, "Foreign key criada no banco com sucesso.")
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
            "constraint_name": constraint_name,
            "referenced_table_name": referenced_table_name,
            "referenced_column_name": referenced_column_name,
            "sql_recipe": sql,
        }

    def _get_raw_id_column_definition(self, database_name: str, full_table_name: str, cancel_event=None):
        for column in self.get_table_column_definitions(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        ):
            if column["name"] == "raw_id":
                return column
        return None

    def _get_existing_raw_id_high_watermark(self, database_name: str, full_table_name: str, cancel_event=None) -> int:
        if not self.table_exists(database_name, full_table_name, cancel_event=cancel_event):
            return 0

        raw_id_column = self._get_raw_id_column_definition(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        if not raw_id_column:
            return 0

        schema_name, table_name = split_table_name(full_table_name)
        raw_id_type = str(raw_id_column.get("type") or "").strip().lower()
        if raw_id_type == "jsonb":
            sql = f"""
WITH raw_id_values AS (
    SELECT {sql_ident('raw_id')}::text AS raw_id_text
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
    WHERE {sql_ident('raw_id')} IS NOT NULL
      AND jsonb_typeof({sql_ident('raw_id')}) <> 'array'
    UNION ALL
    SELECT elements.value
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
    CROSS JOIN LATERAL jsonb_array_elements_text({sql_ident('raw_id')}) AS elements(value)
    WHERE {sql_ident('raw_id')} IS NOT NULL
      AND jsonb_typeof({sql_ident('raw_id')}) = 'array'
)
SELECT COALESCE(MAX(raw_id_text::bigint), 0)
FROM raw_id_values
WHERE raw_id_text ~ '^[0-9]+$';
"""
        else:
            sql = f"""
SELECT COALESCE(MAX({sql_ident('raw_id')}::bigint), 0)
FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
WHERE {sql_ident('raw_id')} IS NOT NULL;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        return int(output or "0")

    def reserve_table_raw_ids(self, database_name: str, full_table_name: str, count: int, cancel_event=None) -> int:
        self._ensure_control_metadata(cancel_event=cancel_event)
        if count <= 0:
            return 0

        schema_name, table_name = split_table_name(full_table_name)
        existing_high_watermark = self._get_existing_raw_id_high_watermark(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        floor_value = max(existing_high_watermark + 1, 1)
        sql = f"""
BEGIN;
LOCK TABLE {CONTROL_TABLE_RAW_ID_COUNTERS} IN EXCLUSIVE MODE;
WITH current_floor AS (
    SELECT GREATEST(
        COALESCE(
            (
                SELECT next_raw_id
                FROM {CONTROL_TABLE_RAW_ID_COUNTERS}
                WHERE database_name = {sql_literal(database_name)}
                  AND schema_name = {sql_literal(schema_name)}
                  AND table_name = {sql_literal(table_name)}
            ),
            1
        ),
        {floor_value}
    ) AS next_start
),
upsert AS (
    INSERT INTO {CONTROL_TABLE_RAW_ID_COUNTERS} (
        database_name,
        schema_name,
        table_name,
        next_raw_id,
        updated_at
    )
    SELECT
        {sql_literal(database_name)},
        {sql_literal(schema_name)},
        {sql_literal(table_name)},
        next_start + {count},
        now()
    FROM current_floor
    ON CONFLICT (database_name, schema_name, table_name)
    DO UPDATE SET
        next_raw_id = EXCLUDED.next_raw_id,
        updated_at = now()
    RETURNING 1
)
SELECT next_start
FROM current_floor;
COMMIT;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -qAt -v ON_ERROR_STOP=1"
        )
        output = self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event).strip()
        first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
        return int(first_line or "0")

    def backfill_missing_raw_ids(self, database_name: str, full_table_name: str, cancel_event=None) -> int:
        if not self.table_exists(database_name, full_table_name, cancel_event=cancel_event):
            return 0

        raw_id_column = self._get_raw_id_column_definition(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        if not raw_id_column:
            return 0

        schema_name, table_name = split_table_name(full_table_name)
        count_sql = f"""
SELECT COUNT(*)
FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
WHERE {sql_ident('raw_id')} IS NULL;
"""
        count_command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -c {shlex.quote(count_sql)}"
        )
        missing_count = int(self.run_remote_command(count_command, cancel_event=cancel_event).strip() or "0")
        if missing_count <= 0:
            return 0

        reserved_start = self.reserve_table_raw_ids(
            database_name,
            full_table_name,
            missing_count,
            cancel_event=cancel_event,
        )
        raw_id_type = str(raw_id_column.get("type") or "").strip().lower()
        raw_id_expression = (
            f"to_jsonb({reserved_start} + missing.row_offset - 1)"
            if raw_id_type == "jsonb"
            else f"{reserved_start} + missing.row_offset - 1"
        )
        update_sql = f"""
WITH missing AS (
    SELECT ctid, row_number() OVER (ORDER BY ctid) AS row_offset
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
    WHERE {sql_ident('raw_id')} IS NULL
)
UPDATE {sql_ident(schema_name)}.{sql_ident(table_name)} AS target
SET {sql_ident('raw_id')} = {raw_id_expression}
FROM missing
WHERE target.ctid = missing.ctid;
"""
        update_command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1 -c {shlex.quote(update_sql)}"
        )
        self.run_remote_command(update_command, cancel_event=cancel_event)
        return missing_count

    def drop_table_column(self, database_name: str, full_table_name: str, column_name: str, cancel_event=None):
        if "raw" in str(column_name).lower():
            raise RuntimeError("Colunas raw nao podem ser excluidas por este fluxo.")

        schema_name, table_name = split_table_name(full_table_name)
        sql = (
            f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
            f"DROP COLUMN {sql_ident(column_name)};"
        )
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(command, cancel_event=cancel_event)
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
            "sql_recipe": sql,
        }

    def create_table_columns(self, database_name: str, full_table_name: str, columns, cancel_event=None):
        if not columns:
            raise RuntimeError("Nenhuma coluna informada para criacao.")

        normalized_columns = []
        seen_names = set()
        for column in columns:
            column_name = str((column or {}).get("column_name") or "").strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", column_name):
                raise RuntimeError(f"Nome de coluna invalido: {column_name or '(vazio)'}.")
            if len(column_name) > 58:
                raise RuntimeError(f"Nome de coluna longo demais: {column_name}.")
            if column_name in seen_names:
                raise RuntimeError(f"A coluna {column_name} foi informada mais de uma vez.")

            postgres_type = self.CREATE_TABLE_COLUMN_TYPE_MAP.get(
                str((column or {}).get("column_type") or "").strip().lower()
            )
            if not postgres_type:
                raise RuntimeError(
                    f"Tipo de coluna nao suportado: {str((column or {}).get('column_type') or '').strip()}"
                )

            seen_names.add(column_name)
            normalized_columns.append(
                {
                    "column_name": column_name,
                    "column_type": postgres_type,
                }
            )

        existing_columns = {
            item["name"]
            for item in self.get_table_column_definitions(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        }
        conflicting_columns = [
            column["column_name"]
            for column in normalized_columns
            if column["column_name"] in existing_columns
        ]
        if conflicting_columns:
            raise RuntimeError(
                "As seguintes colunas ja existem: " + ", ".join(conflicting_columns) + "."
            )

        schema_name, table_name = split_table_name(full_table_name)
        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        sql_lines = [
            "BEGIN;",
            "SET LOCAL lock_timeout = '10s';",
            "SET LOCAL statement_timeout = '30min';",
        ]
        sql_lines.extend(
            f"ALTER TABLE {qualified_table} ADD COLUMN {sql_ident(column['column_name'])} {column['column_type']};"
            for column in normalized_columns
        )
        sql_lines.append("COMMIT;")
        sql = "\n".join(sql_lines) + "\n"

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "columns": normalized_columns,
            "sql_recipe": sql,
        }

    def group_raw_rows(
        self,
        database_name: str,
        full_table_name: str,
        key_header: str,
        equal_headers=None,
        exclusive_headers=None,
        cancel_event=None,
        progress_callback=None,
    ):
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 0, "Validando configuracao do agrupamento...")
        key_header = str(key_header or "").strip()
        if not key_header:
            raise RuntimeError("Header de chave nao informado.")

        equal_headers = [str(item or "").strip() for item in (equal_headers or []) if str(item or "").strip()]
        exclusive_headers = [
            str(item or "").strip() for item in (exclusive_headers or []) if str(item or "").strip()
        ]
        selected_headers = [key_header, *equal_headers, *exclusive_headers]
        if len(selected_headers) != len(set(selected_headers)):
            raise RuntimeError("Os headers selecionados nao podem se repetir.")

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 20, "Conferindo estrutura raw da tabela...")
        column_names = {
            item["name"]
            for item in self.get_table_column_definitions(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        }
        required_raw_columns = {"raw_hash", "raw_ingested_at", "raw_tab", "raw_id", "raw"}
        allowed_raw_columns = required_raw_columns | {"raw_schema"}
        missing_raw_columns = sorted(required_raw_columns - column_names)
        if missing_raw_columns:
            raise RuntimeError(
                "A tabela nao possui todas as colunas raw necessarias: "
                + ", ".join(missing_raw_columns)
                + "."
            )

        extra_columns = sorted(column_names - allowed_raw_columns)
        if extra_columns:
            raise RuntimeError(
                "O agrupamento raw so pode ser executado em tabelas com as colunas raw padrao. "
                "Colunas extras encontradas: "
                + ", ".join(extra_columns)
                + "."
            )
        has_raw_schema = "raw_schema" in column_names

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 45, "Carregando headers disponiveis no raw...")
        available_headers = set(
            self.get_raw_value_keys(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        )
        missing_headers = [header for header in selected_headers if header not in available_headers]
        if missing_headers:
            raise RuntimeError(
                "Os headers selecionados nao existem no raw: " + ", ".join(missing_headers) + "."
            )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 70, "Contando linhas atuais da tabela...")
        source_row_count = self.get_table_row_count(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        if source_row_count <= 0:
            raise RuntimeError("Nao ha linhas para agrupar.")

        self._notify_progress(progress_callback, "prepare", 100, "Configuracao validada. Montando SQL otimizado...")
        schema_name, table_name = split_table_name(full_table_name)
        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        row_count_set_sql = self._build_table_row_count_set_sql(
            schema_name,
            table_name,
            "SELECT COUNT(*) FROM pgdm_raw_group_stage",
        )
        raw_values_sql = (
            f"CASE "
            f"WHEN jsonb_typeof({sql_ident('raw')} -> 'values') = 'object' THEN {sql_ident('raw')} -> 'values' "
            f"WHEN jsonb_typeof({sql_ident('raw')} -> 'Values') = 'object' THEN {sql_ident('raw')} -> 'Values' "
            f"ELSE NULL "
            f"END"
        )
        key_header_sql = sql_literal(key_header)
        group_mode_pairs = [f"{sql_literal(key_header)}, 'key'"]
        group_mode_pairs.extend(f"{sql_literal(header_name)}, 'unique'" for header_name in equal_headers)
        group_mode_pairs.extend(f"{sql_literal(header_name)}, 'exclusive'" for header_name in exclusive_headers)
        group_modes_sql = f"jsonb_build_object({', '.join(group_mode_pairs)})"

        equal_header_list_sql = ", ".join(sql_literal(header_name) for header_name in equal_headers)
        equal_stage_sql = ""
        equal_join_sql = ""
        equal_values_sql = "'{}'::jsonb"
        if equal_headers:
            equal_stage_sql = f"""
CREATE TEMP TABLE pgdm_raw_group_equal_values ON COMMIT DROP AS
WITH per_distinct AS (
    SELECT
        source.group_key_text,
        kv.key AS field_name,
        kv.value AS field_value,
        MIN(source.row_order) AS first_order
    FROM pgdm_raw_group_prepared AS source
    CROSS JOIN LATERAL jsonb_each(source.values_obj) AS kv(key, value)
    WHERE kv.key = ANY(ARRAY[{equal_header_list_sql}]::text[])
    GROUP BY source.group_key_text, kv.key, kv.value
),
per_field AS (
    SELECT
        group_key_text,
        field_name,
        CASE
            WHEN COUNT(*) = 1 THEN (jsonb_agg(field_value ORDER BY first_order) -> 0)
            ELSE jsonb_agg(field_value ORDER BY first_order)
        END AS field_value
    FROM per_distinct
    GROUP BY group_key_text, field_name
)
SELECT
    group_key_text,
    jsonb_object_agg(field_name, field_value) AS equal_values_obj
FROM per_field
GROUP BY group_key_text;
""".strip()
            equal_join_sql = """
LEFT JOIN pgdm_raw_group_equal_values AS equal_values
    ON equal_values.group_key_text = first_rows.group_key_text
""".strip()
            equal_values_sql = "COALESCE(equal_values.equal_values_obj, '{}'::jsonb)"

        exclusive_header_list_sql = ", ".join(sql_literal(header_name) for header_name in exclusive_headers)
        exclusive_stage_sql = ""
        exclusive_join_sql = ""
        exclusive_values_sql = "'{}'::jsonb"
        if exclusive_headers:
            exclusive_stage_sql = f"""
CREATE TEMP TABLE pgdm_raw_group_exclusive_values ON COMMIT DROP AS
WITH per_field AS (
    SELECT
        source.group_key_text,
        kv.key AS field_name,
        jsonb_agg(kv.value ORDER BY source.row_order) AS field_value
    FROM pgdm_raw_group_prepared AS source
    CROSS JOIN LATERAL jsonb_each(source.values_obj) AS kv(key, value)
    WHERE kv.key = ANY(ARRAY[{exclusive_header_list_sql}]::text[])
    GROUP BY source.group_key_text, kv.key
)
SELECT
    group_key_text,
    jsonb_object_agg(field_name, field_value) AS exclusive_values_obj
FROM per_field
GROUP BY group_key_text;
""".strip()
            exclusive_join_sql = """
LEFT JOIN pgdm_raw_group_exclusive_values AS exclusive_values
    ON exclusive_values.group_key_text = first_rows.group_key_text
""".strip()
            exclusive_values_sql = "COALESCE(exclusive_values.exclusive_values_obj, '{}'::jsonb)"

        raw_schema_source_sql = (
            sql_ident('raw_schema')
            if has_raw_schema
            else f"NULL::text AS {sql_ident('raw_schema')}"
        )
        raw_schema_insert_column_sql = f"    {sql_ident('raw_schema')},\n" if has_raw_schema else ""
        raw_schema_insert_value_sql = f"    {sql_ident('raw_schema')},\n" if has_raw_schema else ""

        sql = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';

CREATE TEMP TABLE pgdm_raw_group_prepared ON COMMIT DROP AS
SELECT
    row_number() OVER (ORDER BY ctid) AS row_order,
    {sql_ident('raw_hash')},
    {sql_ident('raw_ingested_at')},
    {raw_schema_source_sql},
    {sql_ident('raw_tab')},
    to_jsonb({sql_ident('raw_id')}) AS raw_id_json,
    {raw_values_sql} AS values_obj,
    ({raw_values_sql}) ->> {key_header_sql} AS group_key_text
FROM {qualified_table};

DO $pgdm_raw_group_validate$
DECLARE
    invalid_payload_count bigint;
    missing_key_count bigint;
BEGIN
    SELECT COUNT(*) INTO invalid_payload_count
    FROM pgdm_raw_group_prepared
    WHERE values_obj IS NULL;

    IF invalid_payload_count > 0 THEN
        RAISE EXCEPTION 'O agrupamento raw exige objetos em raw.values para todas as linhas.';
    END IF;

    SELECT COUNT(*) INTO missing_key_count
    FROM pgdm_raw_group_prepared
    WHERE NULLIF(btrim(COALESCE(group_key_text, '')), '') IS NULL;

    IF missing_key_count > 0 THEN
        RAISE EXCEPTION USING MESSAGE = 'A chave '
            || {key_header_sql}
            || ' esta ausente ou vazia em '
            || missing_key_count::text
            || ' linha(s).';
    END IF;
END
$pgdm_raw_group_validate$;

CREATE TEMP TABLE pgdm_raw_group_first_rows ON COMMIT DROP AS
SELECT DISTINCT ON (group_key_text)
    group_key_text,
    row_order AS first_row_order,
    {sql_ident('raw_hash')},
    {sql_ident('raw_ingested_at')},
    {sql_ident('raw_schema')},
    {sql_ident('raw_tab')},
    values_obj AS first_values_obj
FROM pgdm_raw_group_prepared
ORDER BY group_key_text, row_order;

CREATE TEMP TABLE pgdm_raw_group_meta ON COMMIT DROP AS
SELECT
    source.group_key_text,
    COALESCE(
        jsonb_agg(raw_ids.value ORDER BY source.row_order, raw_ids.ordinality)
            FILTER (WHERE raw_ids.value IS NOT NULL),
        '[]'::jsonb
    ) AS raw_id_list
FROM pgdm_raw_group_prepared AS source
LEFT JOIN LATERAL jsonb_array_elements(
    CASE
        WHEN source.raw_id_json IS NULL THEN '[]'::jsonb
        WHEN jsonb_typeof(source.raw_id_json) = 'array' THEN source.raw_id_json
        ELSE jsonb_build_array(source.raw_id_json)
    END
) WITH ORDINALITY AS raw_ids(value, ordinality) ON true
GROUP BY source.group_key_text;

DO $pgdm_raw_group_raw_id_type$
DECLARE
    current_type text;
BEGIN
    SELECT pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)
    INTO current_type
    FROM pg_attribute AS attribute
    JOIN pg_class AS class ON class.oid = attribute.attrelid
    JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
    WHERE namespace.nspname = {sql_literal(schema_name)}
      AND class.relname = {sql_literal(table_name)}
      AND attribute.attname = {sql_literal('raw_id')}
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped;

    IF current_type IS DISTINCT FROM 'jsonb' THEN
        EXECUTE 'ALTER TABLE {qualified_table} ALTER COLUMN {sql_ident('raw_id')} TYPE jsonb USING to_jsonb({sql_ident('raw_id')})';
    END IF;
END
$pgdm_raw_group_raw_id_type$;

{equal_stage_sql}

{exclusive_stage_sql}

CREATE TEMP TABLE pgdm_raw_group_stage ON COMMIT DROP AS
SELECT
    first_rows.first_row_order,
    first_rows.{sql_ident('raw_hash')},
    first_rows.{sql_ident('raw_ingested_at')},
    first_rows.{sql_ident('raw_schema')},
    first_rows.{sql_ident('raw_tab')},
    group_meta.raw_id_list AS {sql_ident('raw_id')},
    jsonb_build_object(
        'group_by',
        {key_header_sql},
        'group_modes',
        {group_modes_sql},
        'values',
        COALESCE(first_rows.first_values_obj, '{{}}'::jsonb) || {equal_values_sql} || {exclusive_values_sql}
    ) AS {sql_ident('raw')}
FROM pgdm_raw_group_first_rows AS first_rows
JOIN pgdm_raw_group_meta AS group_meta
    ON group_meta.group_key_text = first_rows.group_key_text
{equal_join_sql}
{exclusive_join_sql}
ORDER BY first_rows.first_row_order;

DELETE FROM {qualified_table};

INSERT INTO {qualified_table} (
    {sql_ident('raw_hash')},
    {sql_ident('raw_ingested_at')},
{raw_schema_insert_column_sql}    {sql_ident('raw_tab')},
    {sql_ident('raw_id')},
    {sql_ident('raw')}
)
SELECT
    {sql_ident('raw_hash')},
    {sql_ident('raw_ingested_at')},
{raw_schema_insert_value_sql}    {sql_ident('raw_tab')},
    {sql_ident('raw_id')},
    {sql_ident('raw')}
FROM pgdm_raw_group_stage
ORDER BY first_row_order;

{row_count_set_sql}

COMMIT;
""".strip() + "\n"

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "group", 5, "Agrupando registros raw em lote no banco...")
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        self._notify_progress(progress_callback, "group", 100, "Raw agrupado e reescrito com sucesso.")
        group_row_count = self.get_table_row_count(
            database_name,
            full_table_name,
        )
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "source_row_count": source_row_count,
            "group_row_count": group_row_count,
            "sql_recipe": sql,
        }

    def group_raw_columns(
        self,
        database_name: str,
        full_table_name: str,
        column_groups=None,
        cancel_event=None,
        progress_callback=None,
    ):
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 0, "Validando configuracao do agrupamento de colunas...")

        normalized_groups = []
        used_headers = set()
        for group_index, group_item in enumerate(column_groups or [], start=1):
            master_header = str((group_item or {}).get("master_header") or "").strip()
            if not master_header:
                raise RuntimeError(f"O agrupamento {group_index} nao possui coluna mestre.")

            source_headers = [
                str(item or "").strip()
                for item in ((group_item or {}).get("source_headers") or [])
                if str(item or "").strip()
            ]
            if not source_headers:
                raise RuntimeError(f"O agrupamento {group_index} precisa ter ao menos uma coluna recebida.")

            local_headers = [master_header, *source_headers]
            if len(local_headers) != len(set(local_headers)):
                raise RuntimeError(
                    f"O agrupamento {group_index} contem headers repetidos. "
                    "Um header nao pode aparecer duas vezes no mesmo grupo."
                )

            repeated_headers = [header for header in local_headers if header in used_headers]
            if repeated_headers:
                raise RuntimeError(
                    "Um mesmo header nao pode participar de mais de um agrupamento de colunas. "
                    "Conflitos: "
                    + ", ".join(sorted(set(repeated_headers)))
                    + "."
                )

            used_headers.update(local_headers)
            normalized_groups.append(
                {
                    "master_header": master_header,
                    "source_headers": source_headers,
                }
            )

        if not normalized_groups:
            raise RuntimeError("Adicione ao menos um agrupamento de colunas.")

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 25, "Conferindo estrutura raw da tabela...")
        column_names = {
            item["name"]
            for item in self.get_table_column_definitions(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        }
        if "raw" not in column_names:
            raise RuntimeError("A tabela selecionada nao possui a coluna raw.")

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 50, "Carregando headers disponiveis no raw...")
        available_headers = set(
            self.get_raw_value_keys(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        )
        missing_headers = [
            header
            for group_item in normalized_groups
            for header in [group_item["master_header"], *group_item["source_headers"]]
            if header not in available_headers
        ]
        if missing_headers:
            raise RuntimeError(
                "Os headers selecionados nao existem no raw: "
                + ", ".join(sorted(set(missing_headers)))
                + "."
            )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 75, "Contando linhas atuais da tabela...")
        row_count = self.get_table_row_count(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        if row_count <= 0:
            raise RuntimeError("Nao ha linhas para agrupar colunas.")

        self._notify_progress(progress_callback, "prepare", 100, "Configuracao validada. Montando SQL...")
        schema_name, table_name = split_table_name(full_table_name)
        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        instructions_payload = json.dumps(normalized_groups, ensure_ascii=False)
        instructions_sql = f"{sql_literal(instructions_payload)}::jsonb"
        raw_values_sql = (
            f"CASE "
            f"WHEN jsonb_typeof({sql_ident('raw')} -> 'values') = 'object' THEN {sql_ident('raw')} -> 'values' "
            f"WHEN jsonb_typeof({sql_ident('raw')} -> 'Values') = 'object' THEN {sql_ident('raw')} -> 'Values' "
            f"ELSE NULL "
            f"END"
        )
        comment_lines = ["-- Agrupamento de colunas raw"]
        for group_item in normalized_groups:
            comment_lines.append(
                "-- Mestre: "
                + group_item["master_header"]
                + " <= "
                + ", ".join(group_item["source_headers"])
            )
        comments_sql = "\n".join(comment_lines)

        sql = f"""
{comments_sql}
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';

CREATE OR REPLACE FUNCTION pg_temp.pgdm_raw_group_columns_transform(input_values jsonb, instructions jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $pgdm_raw_group_columns$
DECLARE
    working_values jsonb := COALESCE(input_values, '{{}}'::jsonb);
    instruction jsonb;
    master_key text;
    source_key text;
    current_value jsonb;
    merged_values jsonb;
    has_any_value boolean;
BEGIN
    FOR instruction IN
        SELECT value
        FROM jsonb_array_elements(COALESCE(instructions, '[]'::jsonb))
    LOOP
        master_key := NULLIF(btrim(COALESCE(instruction ->> 'master_header', '')), '');
        IF master_key IS NULL THEN
            CONTINUE;
        END IF;

        merged_values := '[]'::jsonb;
        has_any_value := false;

        IF working_values ? master_key THEN
            current_value := working_values -> master_key;
            has_any_value := true;
            IF jsonb_typeof(current_value) = 'array' THEN
                merged_values := merged_values || current_value;
            ELSE
                merged_values := merged_values || jsonb_build_array(current_value);
            END IF;
        END IF;

        FOR source_key IN
            SELECT NULLIF(btrim(value), '')
            FROM jsonb_array_elements_text(COALESCE(instruction -> 'source_headers', '[]'::jsonb))
        LOOP
            IF source_key IS NULL THEN
                CONTINUE;
            END IF;

            IF working_values ? source_key THEN
                current_value := working_values -> source_key;
                has_any_value := true;
                IF jsonb_typeof(current_value) = 'array' THEN
                    merged_values := merged_values || current_value;
                ELSE
                    merged_values := merged_values || jsonb_build_array(current_value);
                END IF;
                working_values := working_values - source_key;
            END IF;
        END LOOP;

        IF has_any_value THEN
            working_values := jsonb_set(working_values, ARRAY[master_key], merged_values, true);
        END IF;
    END LOOP;

    RETURN working_values;
END
$pgdm_raw_group_columns$;

DO $pgdm_raw_group_columns_validate$
DECLARE
    invalid_payload_count bigint;
BEGIN
    SELECT COUNT(*) INTO invalid_payload_count
    FROM {qualified_table}
    WHERE {raw_values_sql} IS NULL;

    IF invalid_payload_count > 0 THEN
        RAISE EXCEPTION 'O agrupamento de colunas exige objetos em raw.values para todas as linhas.';
    END IF;
END
$pgdm_raw_group_columns_validate$;

UPDATE {qualified_table}
SET {sql_ident('raw')} = CASE
    WHEN jsonb_typeof({sql_ident('raw')} -> 'values') = 'object' THEN
        jsonb_set(
            COALESCE({sql_ident('raw')}, '{{}}'::jsonb),
            '{{values}}',
            pg_temp.pgdm_raw_group_columns_transform({sql_ident('raw')} -> 'values', {instructions_sql}),
            true
        )
    WHEN jsonb_typeof({sql_ident('raw')} -> 'Values') = 'object' THEN
        jsonb_set(
            COALESCE({sql_ident('raw')}, '{{}}'::jsonb),
            '{{Values}}',
            pg_temp.pgdm_raw_group_columns_transform({sql_ident('raw')} -> 'Values', {instructions_sql}),
            true
        )
    ELSE
        jsonb_set(
            COALESCE({sql_ident('raw')}, '{{}}'::jsonb),
            '{{values}}',
            pg_temp.pgdm_raw_group_columns_transform(COALESCE({raw_values_sql}, '{{}}'::jsonb), {instructions_sql}),
            true
        )
END;

COMMIT;
""".strip() + "\n"

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "group", 5, "Agrupando colunas raw em lote no banco...")
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        self._notify_progress(progress_callback, "group", 100, "Colunas raw agrupadas e reescritas com sucesso.")
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "row_count": row_count,
            "group_count": len(normalized_groups),
            "sql_recipe": sql,
        }

    def validate_table_column(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        validation_config: dict,
        cancel_event=None,
    ):
        if "raw" in str(column_name or "").lower():
            raise RuntimeError("Colunas raw nao podem ser validadas por este fluxo.")

        schema_name, table_name = split_table_name(full_table_name)
        columns_info = self._get_table_columns_info(
            database_name,
            schema_name,
            table_name,
            cancel_event=cancel_event,
        )
        columns_by_name = {column["column_name"]: column for column in columns_info}
        source_info = columns_by_name.get(column_name)
        if not source_info:
            raise RuntimeError(f"Coluna nao encontrada: {column_name}")

        rule = str((validation_config or {}).get("rule") or "").strip()
        if rule in self.QUALITY_NUMERIC_RULES and not source_info.get("is_numeric"):
            raise RuntimeError("Essa regra de validacao exige uma coluna numerica.")

        flag_column_name = self._build_quality_flag_column_name(column_name)
        flag_info = columns_by_name.get(flag_column_name)
        if flag_info and str(flag_info.get("data_type") or "").lower() != "boolean":
            raise RuntimeError(
                f"A coluna {flag_column_name} ja existe e nao e booleana. "
                "Renomeie ou exclua essa coluna antes de criar a flag."
            )

        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        flag_column = sql_ident(flag_column_name)
        expression_sql, stats_cte = self._build_quality_flag_expression(
            qualified_table=qualified_table,
            column_name=column_name,
            validation_config=validation_config or {},
        )
        description = str((validation_config or {}).get("description") or rule).strip()
        sql_comment = description.replace("\r", " ").replace("\n", " ")[:500]
        update_sql = (
            f"{stats_cte}\n"
            f"UPDATE {qualified_table} AS target\n"
            f"SET {flag_column} = {expression_sql}"
        )
        if stats_cte:
            update_sql += "\nFROM qa_stats"
        update_sql += ";"

        sql = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
-- PGDM QA: {sql_comment}
ALTER TABLE {qualified_table} ADD COLUMN IF NOT EXISTS {flag_column} boolean;
{update_sql}
COMMIT;
""".strip() + "\n"

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
            "flag_column_name": flag_column_name,
            "rule": rule,
            "description": description,
            "sql_recipe": sql,
        }

    @staticmethod
    def _build_quality_flag_column_name(column_name: str) -> str:
        source = str(column_name or "").strip()
        if not source:
            raise RuntimeError("Nome de coluna invalido para validacao.")
        flag_name = f"{source}_flag"
        if len(flag_name) <= 63:
            return flag_name
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:6]
        prefix = source[:51].rstrip("_") or "qa"
        return f"{prefix}_{digest}_flag"

    @classmethod
    def _build_quality_flag_expression(
        cls,
        qualified_table: str,
        column_name: str,
        validation_config: dict,
    ) -> tuple[str, str]:
        rule = str(validation_config.get("rule") or "").strip()
        params = validation_config.get("params") or {}
        ignore_blank = bool(validation_config.get("ignore_blank")) and rule not in cls.QUALITY_BLANK_RULES
        value_expr = f"target.{sql_ident(column_name)}"
        source_expr = sql_ident(column_name)
        blank_expr = f"({value_expr} IS NULL OR NULLIF(btrim({value_expr}::text), '') IS NULL)"
        text_expr = f"{value_expr}::text"
        length_expr = f"char_length({text_expr})"
        numeric_expr = f"{value_expr}::numeric"
        stats_cte = ""
        # Keep the backslash outside f-string replacement fields for Python 3.10/3.11 compatibility.
        escape_literal = sql_literal("\\")

        if rule == "not_blank":
            valid_expr = f"NOT {blank_expr}"
        elif rule == "is_blank":
            valid_expr = blank_expr
        elif rule == "contains":
            text_value = cls._qa_required_text(params, "text")
            like_value = sql_literal(cls._qa_escape_like_text(text_value))
            valid_expr = (
                f"{value_expr} IS NOT NULL "
                f"AND {text_expr} ILIKE '%' || {like_value} || '%' ESCAPE {escape_literal}"
            )
        elif rule == "not_contains":
            text_value = cls._qa_required_text(params, "text")
            like_value = sql_literal(cls._qa_escape_like_text(text_value))
            valid_expr = (
                f"{value_expr} IS NOT NULL "
                f"AND {text_expr} NOT ILIKE '%' || {like_value} || '%' ESCAPE {escape_literal}"
            )
        elif rule in {"length_lt", "length_lte", "length_gt", "length_gte"}:
            limit = cls._qa_required_integer(params, "limit")
            operator = {
                "length_lt": "<",
                "length_lte": "<=",
                "length_gt": ">",
                "length_gte": ">=",
            }[rule]
            valid_expr = f"{value_expr} IS NOT NULL AND {length_expr} {operator} {limit}"
        elif rule == "length_between":
            lower = cls._qa_required_integer(params, "lower")
            upper = cls._qa_required_integer(params, "upper")
            if lower > upper:
                raise RuntimeError("O minimo nao pode ser maior que o maximo.")
            valid_expr = f"{value_expr} IS NOT NULL AND {length_expr} BETWEEN {lower} AND {upper}"
        elif rule in {"numeric_gt", "numeric_gte", "numeric_lt", "numeric_lte"}:
            numeric_value = cls._qa_required_decimal(params, "value")
            operator = {
                "numeric_gt": ">",
                "numeric_gte": ">=",
                "numeric_lt": "<",
                "numeric_lte": "<=",
            }[rule]
            valid_expr = f"{value_expr} IS NOT NULL AND {numeric_expr} {operator} {numeric_value}"
        elif rule == "numeric_between":
            lower = cls._qa_required_decimal(params, "lower")
            upper = cls._qa_required_decimal(params, "upper")
            if float(lower) > float(upper):
                raise RuntimeError("O minimo nao pode ser maior que o maximo.")
            valid_expr = f"{value_expr} IS NOT NULL AND {numeric_expr} BETWEEN {lower} AND {upper}"
        elif rule in {
            "numeric_mean_stddev_within",
            "numeric_max_mean_stddev",
            "numeric_min_mean_stddev",
        }:
            stddevs = cls._qa_required_decimal(params, "stddevs")
            if float(stddevs) <= 0:
                raise RuntimeError("Use N desvios maior que zero.")
            stats_cte = f"""WITH qa_stats AS MATERIALIZED (
    SELECT
        avg({source_expr}::numeric) AS mean_value,
        COALESCE(stddev_samp({source_expr}::numeric), 0) AS stddev_value
    FROM {qualified_table}
    WHERE {source_expr} IS NOT NULL
)"""
            upper_bound = f"(qa_stats.mean_value + ({stddevs} * qa_stats.stddev_value))"
            lower_bound = f"(qa_stats.mean_value - ({stddevs} * qa_stats.stddev_value))"
            if rule == "numeric_mean_stddev_within":
                valid_expr = (
                    f"{value_expr} IS NOT NULL "
                    f"AND qa_stats.mean_value IS NOT NULL "
                    f"AND {numeric_expr} BETWEEN {lower_bound} AND {upper_bound}"
                )
            elif rule == "numeric_max_mean_stddev":
                valid_expr = (
                    f"{value_expr} IS NOT NULL "
                    f"AND qa_stats.mean_value IS NOT NULL "
                    f"AND {numeric_expr} <= {upper_bound}"
                )
            else:
                valid_expr = (
                    f"{value_expr} IS NOT NULL "
                    f"AND qa_stats.mean_value IS NOT NULL "
                    f"AND {numeric_expr} >= {lower_bound}"
                )
        else:
            raise RuntimeError(f"Regra de validacao nao suportada: {rule}")

        invalid_expr = f"COALESCE(NOT ({valid_expr}), true)"
        if ignore_blank:
            invalid_expr = f"CASE WHEN {blank_expr} THEN false ELSE {invalid_expr} END"
        return invalid_expr, stats_cte

    @staticmethod
    def _qa_required_text(params: dict, key: str) -> str:
        value = str((params or {}).get(key) or "").strip()
        if not value:
            raise RuntimeError("Informe o texto da regra de validacao.")
        return value

    @staticmethod
    def _qa_escape_like_text(value: str) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    @staticmethod
    def _qa_required_integer(params: dict, key: str) -> int:
        value = str((params or {}).get(key) or "").strip()
        if not re.fullmatch(r"\d+", value):
            raise RuntimeError("Informe um numero inteiro valido para a regra de validacao.")
        return int(value)

    @staticmethod
    def _qa_required_decimal(params: dict, key: str) -> str:
        value = str((params or {}).get(key) or "").strip().replace(",", ".")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
            raise RuntimeError("Informe um numero valido para a regra de validacao.")
        return value

    def get_raw_value_keys(self, database_name: str, full_table_name: str, cancel_event=None) -> list[str]:
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
WITH raw_values AS (
    SELECT
        CASE
            WHEN jsonb_typeof({sql_ident('raw')} -> 'values') = 'object' THEN {sql_ident('raw')} -> 'values'
            WHEN jsonb_typeof({sql_ident('raw')} -> 'Values') = 'object' THEN {sql_ident('raw')} -> 'Values'
            ELSE '{{}}'::jsonb
        END AS payload
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
    WHERE {sql_ident('raw')} IS NOT NULL
)
SELECT DISTINCT key
FROM raw_values
CROSS JOIN LATERAL jsonb_object_keys(payload) AS keys(key)
ORDER BY key;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        return [row[0] for row in rows if row]

    def get_raw_value_keys_with_multiple_values(
        self,
        database_name: str,
        full_table_name: str,
        cancel_event=None,
    ) -> list[str]:
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
WITH raw_values AS (
    SELECT
        CASE
            WHEN jsonb_typeof({sql_ident('raw')} -> 'values') = 'object' THEN {sql_ident('raw')} -> 'values'
            WHEN jsonb_typeof({sql_ident('raw')} -> 'Values') = 'object' THEN {sql_ident('raw')} -> 'Values'
            ELSE '{{}}'::jsonb
        END AS payload
    FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
    WHERE {sql_ident('raw')} IS NOT NULL
),
key_values AS (
    SELECT kv.key, kv.value
    FROM raw_values
    CROSS JOIN LATERAL jsonb_each(payload) AS kv(key, value)
)
SELECT DISTINCT key
FROM key_values
WHERE jsonb_typeof(value) = 'array'
ORDER BY key;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        return [row[0] for row in rows if row]

    @classmethod
    def _build_raw_values_object_sql(cls, table_reference: str) -> str:
        raw_reference = f"{table_reference}.{sql_ident('raw')}" if table_reference else sql_ident("raw")
        return (
            "CASE "
            f"WHEN jsonb_typeof({raw_reference} -> 'values') = 'object' THEN {raw_reference} -> 'values' "
            f"WHEN jsonb_typeof({raw_reference} -> 'Values') = 'object' THEN {raw_reference} -> 'Values' "
            "ELSE '{}'::jsonb "
            "END"
        )

    @classmethod
    def _build_raw_key_json_sql(cls, table_reference: str, raw_key: str) -> str:
        return f"({cls._build_raw_values_object_sql(table_reference)}) -> {sql_literal(raw_key)}"

    @classmethod
    def _build_raw_key_text_sql(cls, table_reference: str, raw_key: str, first_array_item: bool = False) -> str:
        value_sql = cls._build_raw_key_json_sql(table_reference, raw_key)
        if first_array_item:
            return (
                "CASE "
                f"WHEN {value_sql} IS NULL THEN NULL "
                f"WHEN jsonb_typeof({value_sql}) = 'array' THEN ({value_sql} ->> 0) "
                f"ELSE ({value_sql} #>> '{{}}') "
                "END"
            )
        return f"({value_sql} #>> '{{}}')"

    @classmethod
    def _normalize_expand_source(cls, source: dict, role_name: str) -> dict:
        if not isinstance(source, dict):
            raise RuntimeError(f"Configuracao invalida para {role_name}.")

        source_kind = str(source.get("source_kind") or "").strip().lower()
        field_name = str(source.get("name") or "").strip()
        if source_kind not in {"column", "raw"}:
            raise RuntimeError(f"Origem invalida para {role_name}.")
        if not field_name:
            raise RuntimeError(f"Campo nao informado para {role_name}.")
        if source_kind == "column" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field_name):
            raise RuntimeError(f"Nome de coluna invalido para {role_name}.")
        return {
            "source_kind": source_kind,
            "name": field_name,
            "label": source.get("label") or f"{source_kind}:{field_name}",
            "type": str(source.get("type") or "").strip(),
        }

    @staticmethod
    def _normalize_data_type_label(data_type: str) -> str:
        value = str(data_type or "").strip().lower()
        value = re.sub(r"\s*\([^)]*\)", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    @staticmethod
    def _normalize_data_type_name(data_type: str) -> str:
        value = PostgresAdminService._normalize_data_type_label(data_type)
        aliases = {
            "bool": "boolean",
            "character varying": "varchar",
            "character": "varchar",
            "text": "varchar",
            "varchar": "varchar",
            "decimal": "numeric",
            "float4": "real",
            "float8": "double precision",
            "int2": "smallint",
            "int4": "integer",
            "int8": "bigint",
            "serial": "integer",
            "bigserial": "bigint",
            "json": "jsonb",
            "time without time zone": "time",
            "time with time zone": "time",
            "timestamp without time zone": "timestamp",
            "timestamp": "timestamp",
            "timestamp with time zone": "timestamptz",
            "timestampz": "timestamptz",
            "timestamptz": "timestamptz",
            "double precision": "double precision",
        }
        return aliases.get(value, value)

    @classmethod
    def resolve_supported_expand_column_type(cls, data_type: str) -> str | None:
        normalized_type = cls._normalize_data_type_name(data_type)
        return normalized_type if normalized_type in cls.RAW_EXPAND_TYPE_MAP else None

    @classmethod
    def resolve_supported_data_dictionary_type(cls, data_type: str) -> str | None:
        exact_type = cls._normalize_data_type_label(data_type)
        if exact_type in cls.SERIAL_BASE_TYPE_MAP:
            return exact_type

        normalized_type = cls._normalize_data_type_name(exact_type)
        return normalized_type if normalized_type in cls.DATA_DICTIONARY_TYPE_MAP else None

    @classmethod
    def _resolve_data_dictionary_postgres_type(cls, target_type: str) -> str:
        normalized_target_type = cls._normalize_dictionary_data_type(target_type)
        return cls.DATA_DICTIONARY_TYPE_MAP[normalized_target_type]

    @staticmethod
    def _looks_like_serial_default(default_expression: str) -> bool:
        return bool(re.match(r"^nextval\(", str(default_expression or "").strip(), flags=re.IGNORECASE))

    @classmethod
    def _resolve_dictionary_usage_column_type(cls, actual_type: str, default_expression: str = "") -> str:
        normalized_actual_type = cls._normalize_data_type_name(actual_type)
        if cls._looks_like_serial_default(default_expression):
            if normalized_actual_type == "integer":
                return "serial"
            if normalized_actual_type == "bigint":
                return "bigserial"

        return cls.resolve_supported_data_dictionary_type(actual_type) or normalized_actual_type

    @staticmethod
    def _build_data_dictionary_sequence_name(table_name: str, column_name: str) -> str:
        raw_name = f"{table_name}_{column_name}_seq"
        normalized_name = re.sub(r"[^a-z0-9_]+", "_", raw_name.lower())
        normalized_name = re.sub(r"_+", "_", normalized_name).strip("_") or "column_seq"
        return normalized_name[:63]

    @classmethod
    def _build_expand_source_text_sql(cls, table_reference: str, source_kind: str, field_name: str) -> str:
        if source_kind == "column":
            return f"{table_reference}.{sql_ident(field_name)}::text"
        return cls._build_raw_key_text_sql(table_reference, field_name)

    @classmethod
    def _build_expand_source_json_sql(cls, table_reference: str, source_kind: str, field_name: str) -> str:
        if source_kind == "column":
            return f"to_jsonb({table_reference}.{sql_ident(field_name)})"
        return cls._build_raw_key_json_sql(table_reference, field_name)

    def raw_value_key_has_multiple_values(
        self,
        database_name: str,
        full_table_name: str,
        raw_key: str,
        cancel_event=None,
    ) -> bool:
        schema_name, table_name = split_table_name(full_table_name)
        value_sql = self._build_raw_key_json_sql("", raw_key)
        sql = f"""
SELECT COALESCE(bool_or(jsonb_typeof({value_sql}) = 'array'), false)
FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
WHERE {sql_ident('raw')} IS NOT NULL;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        return str(output or "").strip().lower() in {"t", "true", "1", "yes"}

    def expand_raw_value_key(
        self,
        database_name: str,
        full_table_name: str,
        raw_key: str,
        column_name: str,
        column_type: str,
        false_values=None,
        true_values=None,
        date_formats=None,
        first_array_item: bool = False,
        cancel_event=None,
        progress_callback=None,
    ):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(column_name or "")):
            raise RuntimeError("Nome de coluna invalido para expansao.")
        if len(column_name) > 58:
            raise RuntimeError("Nome de coluna longo demais para expansao.")

        postgres_type = self.RAW_EXPAND_TYPE_MAP.get(str(column_type or "").strip().lower())
        if not postgres_type:
            raise RuntimeError(f"Tipo de coluna nao suportado: {column_type}")

        schema_name, table_name = split_table_name(full_table_name)
        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        value_column = sql_ident(column_name)
        fail_column_name = f"{column_name}_fail"
        fail_column = sql_ident(fail_column_name)
        raw_key_literal = sql_literal(raw_key)
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 10, "Preparando SQL de expansao do raw...")

        raw_value_sql = self._build_raw_key_text_sql("", raw_key, first_array_item=first_array_item)

        if postgres_type in {"varchar"}:
            update_sql = self._build_raw_text_expand_sql(
                qualified_table,
                value_column,
                fail_column,
                raw_value_sql,
                postgres_type,
            )
        elif postgres_type == "boolean":
            update_sql = self._build_raw_boolean_expand_sql(
                qualified_table,
                value_column,
                fail_column,
                raw_value_sql,
                false_values=false_values,
                true_values=true_values,
            )
        elif postgres_type in {"date", "timestamp", "timestamptz", "time"} and date_formats:
            update_sql = self._build_raw_datetime_expand_sql(
                qualified_table,
                value_column,
                fail_column,
                raw_value_sql,
                postgres_type,
                date_formats,
            )
        else:
            update_sql = self._build_raw_safe_cast_expand_sql(
                qualified_table,
                value_column,
                fail_column,
                raw_value_sql,
                postgres_type,
            )

        sql = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
ALTER TABLE {qualified_table} ADD COLUMN IF NOT EXISTS {value_column} {postgres_type};
ALTER TABLE {qualified_table} ADD COLUMN IF NOT EXISTS {fail_column} boolean NOT NULL DEFAULT false;
{update_sql}
DO $pgdm_raw_expand_cleanup$
DECLARE
    fail_count bigint;
BEGIN
    SELECT COUNT(*) INTO fail_count
    FROM {qualified_table}
    WHERE {fail_column} IS TRUE;

    IF fail_count = 0 THEN
        ALTER TABLE {qualified_table} DROP COLUMN IF EXISTS {fail_column};
    END IF;
END
$pgdm_raw_expand_cleanup$;
COMMIT;
""".strip() + "\n"

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self._notify_progress(progress_callback, "alter", 35, "Criando colunas de destino no banco...")
        self._notify_progress(progress_callback, "convert", 45, "Convertendo valores do raw em lote...")
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        self._notify_progress(progress_callback, "convert", 100, "Valores convertidos com sucesso.")
        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "raw_key": raw_key,
            "column_name": column_name,
            "column_type": postgres_type,
            "fail_column_name": fail_column_name,
            "sql_recipe": sql,
        }

    @classmethod
    def _build_expand_child_conversion_parts(
        cls,
        postgres_type: str,
        false_values=None,
        true_values=None,
        date_formats=None,
        custom_sql: str | None = None,
        helper_suffix: str = "",
        raw_text_ref: str = "raw_text",
        normalized_text_ref: str = "normalized_text",
        converted_value_ref: str = "converted_value",
        converted_raw_text_ref: str | None = None,
    ) -> dict:
        helper_suffix = str(helper_suffix or "")
        converted_raw_text_ref = converted_raw_text_ref or raw_text_ref
        custom_sql = str(custom_sql or "").strip().rstrip(";").strip()
        if custom_sql:
            return {
                "setup_sql": f"""
CREATE OR REPLACE FUNCTION pg_temp.pgdm_raw_expand_child_custom_value{helper_suffix}(input_text text, normalized_text text)
RETURNS {postgres_type}
LANGUAGE plpgsql
AS $pgdm_raw_expand_child_custom{helper_suffix}$
BEGIN
    IF input_text IS NULL THEN
        RETURN NULL;
    END IF;

    RETURN {custom_sql};
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END
$pgdm_raw_expand_child_custom{helper_suffix}$;
""".strip(),
                "converted_expr": (
                    f"pg_temp.pgdm_raw_expand_child_custom_value{helper_suffix}("
                    f"{converted_raw_text_ref}, {normalized_text_ref})"
                ),
                "json_expr": f"to_jsonb({converted_value_ref})",
                "fail_expr": f"({raw_text_ref} IS NOT NULL AND {converted_value_ref} IS NULL)",
            }

        if postgres_type == "varchar":
            return {
                "setup_sql": "",
                "converted_expr": converted_raw_text_ref,
                "json_expr": f"to_jsonb({converted_value_ref})",
                "fail_expr": "false",
            }

        if postgres_type == "boolean":
            false_array = cls._text_array_literal(false_values)
            true_array = cls._text_array_literal(true_values)
            return {
                "setup_sql": "",
                "converted_expr": (
                    f"CASE "
                    f"WHEN lower({converted_raw_text_ref}) = ANY({true_array}) THEN true "
                    f"WHEN lower({converted_raw_text_ref}) = ANY({false_array}) THEN false "
                    f"ELSE NULL "
                    f"END"
                ),
                "json_expr": f"to_jsonb({converted_value_ref})",
                "fail_expr": (
                    f"({raw_text_ref} IS NOT NULL "
                    f"AND {normalized_text_ref} <> ALL({true_array}) "
                    f"AND {normalized_text_ref} <> ALL({false_array}))"
                ),
            }

        if postgres_type in {"date", "timestamp", "timestamptz", "time"} and date_formats:
            format_specs = cls._normalize_raw_datetime_formats(date_formats)
            if not format_specs:
                raise RuntimeError("Informe ao menos um formato de data/hora para expansao.")

            postgres_formats = [item["format"] for item in format_specs]
            postgres_patterns = [item["pattern"] for item in format_specs]
            format_array = "ARRAY[" + ", ".join(sql_literal(item) for item in postgres_formats) + "]::text[]"
            pattern_array = "ARRAY[" + ", ".join(sql_literal(item) for item in postgres_patterns) + "]::text[]"
            if postgres_type == "date":
                parse_expression = "to_timestamp(input_text, formats[format_index])::date"
            elif postgres_type == "timestamp":
                parse_expression = "to_timestamp(input_text, formats[format_index])::timestamp"
            elif postgres_type == "timestamptz":
                parse_expression = "to_timestamp(input_text, formats[format_index])"
            elif postgres_type == "time":
                parse_expression = "to_timestamp(input_text, formats[format_index])::time"
            else:
                raise RuntimeError(f"Tipo temporal nao suportado: {postgres_type}")

            return {
                "setup_sql": f"""
CREATE OR REPLACE FUNCTION pg_temp.pgdm_raw_expand_child_parse_datetime{helper_suffix}(input_text text, formats text[], patterns text[])
RETURNS {postgres_type}
LANGUAGE plpgsql
AS $pgdm_raw_expand_child_datetime{helper_suffix}$
DECLARE
    format_index integer;
BEGIN
    IF input_text IS NULL THEN
        RETURN NULL;
    END IF;

    FOR format_index IN 1..COALESCE(array_length(formats, 1), 0) LOOP
        IF patterns[format_index] IS NOT NULL AND input_text !~* patterns[format_index] THEN
            CONTINUE;
        END IF;

        BEGIN
            RETURN {parse_expression};
        EXCEPTION WHEN OTHERS THEN
        END;
    END LOOP;

    RETURN NULL;
END
$pgdm_raw_expand_child_datetime{helper_suffix}$;
""".strip(),
                "converted_expr": (
                    f"pg_temp.pgdm_raw_expand_child_parse_datetime{helper_suffix}("
                    f"{converted_raw_text_ref}, {format_array}, {pattern_array})"
                ),
                "json_expr": f"to_jsonb({converted_value_ref})",
                "fail_expr": f"({raw_text_ref} IS NOT NULL AND {converted_value_ref} IS NULL)",
            }

        return {
            "setup_sql": f"""
CREATE OR REPLACE FUNCTION pg_temp.pgdm_raw_expand_child_cast_value{helper_suffix}(input_text text)
RETURNS {postgres_type}
LANGUAGE plpgsql
AS $pgdm_raw_expand_child_cast{helper_suffix}$
BEGIN
    IF input_text IS NULL THEN
        RETURN NULL;
    END IF;

    RETURN input_text::{postgres_type};
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END
$pgdm_raw_expand_child_cast{helper_suffix}$;
""".strip(),
            "converted_expr": f"pg_temp.pgdm_raw_expand_child_cast_value{helper_suffix}({converted_raw_text_ref})",
            "json_expr": f"to_jsonb({converted_value_ref})",
            "fail_expr": f"({raw_text_ref} IS NOT NULL AND {converted_value_ref} IS NULL)",
        }

    def _build_child_expand_sql_recipe(
        self,
        parent_full_table_name: str,
        raw_key: str,
        child_full_table_name: str,
        parent_key_source: dict,
        child_key_source: dict,
        child_value_source: dict,
        column_name: str,
        postgres_type: str,
        false_values=None,
        true_values=None,
        date_formats=None,
        custom_sql: str | None = None,
    ) -> str:
        parent_schema_name, parent_table_name = split_table_name(parent_full_table_name)
        child_schema_name, child_table_name = split_table_name(child_full_table_name)
        qualified_parent_table = f"{sql_ident(parent_schema_name)}.{sql_ident(parent_table_name)}"
        qualified_child_table = f"{sql_ident(child_schema_name)}.{sql_ident(child_table_name)}"
        value_column = sql_ident(column_name)
        fail_column_name = f"{column_name}_fail"
        fail_column = sql_ident(fail_column_name)
        child_key_column = sql_ident(child_key_source["name"])
        child_value_column = sql_ident(child_value_source["name"])
        parent_key_json_sql = self._build_expand_source_json_sql(
            "parent",
            parent_key_source["source_kind"],
            parent_key_source["name"],
        )
        parent_key_text_sql = self._build_expand_source_text_sql(
            "parent",
            parent_key_source["source_kind"],
            parent_key_source["name"],
        )
        parent_value_json_sql = self._build_raw_key_json_sql("parent", raw_key)
        conversion_parts = self._build_expand_child_conversion_parts(
            postgres_type,
            false_values=false_values,
            true_values=true_values,
            date_formats=date_formats,
            custom_sql=custom_sql,
        )
        setup_sql = conversion_parts["setup_sql"]
        if setup_sql:
            setup_sql += "\n"

        comment_lines = [
            "-- Expansao Raw via tabela filha",
            f"-- Raw selecionado: {raw_key}",
            f"-- Tabela filha de destino: {child_full_table_name}",
            f"-- Chave da tabela atual: {parent_key_source['label']}",
            f"-- Chave na tabela filha: {child_key_source['label']}",
            f"-- Coluna de destino na tabela filha: {child_value_source['label']}",
        ]
        comments_sql = "\n".join(comment_lines)

        return f"""
{comments_sql}
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
ALTER TABLE {qualified_parent_table} ADD COLUMN IF NOT EXISTS {value_column} jsonb;
ALTER TABLE {qualified_parent_table} ADD COLUMN IF NOT EXISTS {fail_column} boolean NOT NULL DEFAULT false;
DO $pgdm_raw_expand_validate$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM {qualified_parent_table} AS parent
        WHERE {parent_key_json_sql} IS NOT NULL
          AND jsonb_typeof({parent_key_json_sql}) IN ('array', 'object')
    ) THEN
        RAISE EXCEPTION 'A chave selecionada na tabela atual precisa ser escalar.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM {qualified_parent_table} AS parent
        WHERE {parent_value_json_sql} IS NOT NULL
          AND jsonb_typeof({parent_value_json_sql}) = 'object'
    ) THEN
        RAISE EXCEPTION 'O header selecionado para tabela filha precisa ser escalar ou lista de valores.';
    END IF;
END
$pgdm_raw_expand_validate$;
{setup_sql}WITH parent_source AS MATERIALIZED (
    SELECT
        parent.ctid AS row_id,
        NULLIF(btrim({parent_key_text_sql}), '') AS parent_key_text,
        {parent_value_json_sql} AS parent_value_json,
        row_number() OVER (ORDER BY parent.ctid) AS parent_order
    FROM {qualified_parent_table} AS parent
),
expanded AS MATERIALIZED (
    SELECT
        parent_source.row_id,
        parent_source.parent_key_text,
        parent_source.parent_order,
        element.ordinality AS item_order,
        element.value AS item_json,
        NULLIF(btrim(element.value #>> '{{}}'), '') AS raw_text
    FROM parent_source
    LEFT JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(parent_source.parent_value_json) = 'array' THEN parent_source.parent_value_json
            WHEN parent_source.parent_value_json IS NULL THEN '[]'::jsonb
            ELSE jsonb_build_array(parent_source.parent_value_json)
        END
    ) WITH ORDINALITY AS element(value, ordinality) ON true
),
converted AS MATERIALIZED (
    SELECT
        expanded.row_id,
        expanded.parent_key_text,
        expanded.parent_order,
        expanded.item_order,
        expanded.raw_text,
        lower(expanded.raw_text) AS normalized_text,
        {conversion_parts["converted_expr"]} AS converted_value
    FROM expanded
    WHERE expanded.item_json IS NULL
       OR jsonb_typeof(expanded.item_json) NOT IN ('array', 'object')
),
child_rows AS MATERIALIZED (
    SELECT
        converted.parent_key_text::{child_key_source["type"]} AS child_key_value,
        converted.converted_value AS child_value,
        converted.parent_order,
        converted.item_order
    FROM converted
    WHERE converted.parent_key_text IS NOT NULL
      AND converted.raw_text IS NOT NULL
),
inserted_child AS (
    INSERT INTO {qualified_child_table} ({child_key_column}, {child_value_column})
    SELECT
        child_rows.child_key_value,
        child_rows.child_value
    FROM child_rows
    ORDER BY child_rows.parent_order, child_rows.item_order
    RETURNING 1
),
parent_payload AS MATERIALIZED (
    SELECT
        converted.row_id,
        COALESCE(
            jsonb_agg(
                COALESCE({conversion_parts["json_expr"]}, 'null'::jsonb)
                ORDER BY converted.item_order
            ) FILTER (WHERE converted.raw_text IS NOT NULL),
            '[]'::jsonb
        ) AS list_value,
        COALESCE(bool_or({conversion_parts["fail_expr"]}), false) AS has_fail
    FROM converted
    GROUP BY converted.row_id
),
source AS MATERIALIZED (
    SELECT
        parent.ctid AS row_id,
        parent_payload.list_value,
        COALESCE(parent_payload.has_fail, false) AS has_fail
    FROM {qualified_parent_table} AS parent
    LEFT JOIN parent_payload
      ON parent.ctid = parent_payload.row_id
)
UPDATE {qualified_parent_table} AS target
SET
    {value_column} = source.list_value,
    {fail_column} = source.has_fail
FROM source
WHERE target.ctid = source.row_id;
DO $pgdm_raw_expand_cleanup$
DECLARE
    fail_count bigint;
BEGIN
    SELECT COUNT(*) INTO fail_count
    FROM {qualified_parent_table}
    WHERE {fail_column} IS TRUE;

    IF fail_count = 0 THEN
        ALTER TABLE {qualified_parent_table} DROP COLUMN IF EXISTS {fail_column};
    END IF;
END
$pgdm_raw_expand_cleanup$;
COMMIT;
""".strip() + "\n"

    def _build_child_expand_version_sql_recipes(
        self,
        parent_full_table_name: str,
        raw_key: str,
        child_full_table_name: str,
        parent_key_source: dict,
        child_key_source: dict,
        child_value_source: dict,
        column_name: str,
        postgres_type: str,
        false_values=None,
        true_values=None,
        date_formats=None,
        custom_sql: str | None = None,
    ) -> dict:
        parent_schema_name, parent_table_name = split_table_name(parent_full_table_name)
        child_schema_name, child_table_name = split_table_name(child_full_table_name)
        qualified_parent_table = f"{sql_ident(parent_schema_name)}.{sql_ident(parent_table_name)}"
        qualified_child_table = f"{sql_ident(child_schema_name)}.{sql_ident(child_table_name)}"
        value_column = sql_ident(column_name)
        fail_column_name = f"{column_name}_fail"
        fail_column = sql_ident(fail_column_name)
        child_key_column = sql_ident(child_key_source["name"])
        child_value_column = sql_ident(child_value_source["name"])
        parent_key_json_sql = self._build_expand_source_json_sql(
            "parent",
            parent_key_source["source_kind"],
            parent_key_source["name"],
        )
        parent_key_text_sql = self._build_expand_source_text_sql(
            "parent",
            parent_key_source["source_kind"],
            parent_key_source["name"],
        )
        parent_value_json_sql = self._build_raw_key_json_sql("parent", raw_key)
        conversion_parts = self._build_expand_child_conversion_parts(
            postgres_type,
            false_values=false_values,
            true_values=true_values,
            date_formats=date_formats,
            custom_sql=custom_sql,
        )
        setup_sql = conversion_parts["setup_sql"]
        if setup_sql:
            setup_sql += "\n"

        common_comment_lines = [
            f"-- Raw selecionado: {raw_key}",
            f"-- Tabela filha de destino: {child_full_table_name}",
            f"-- Chave da tabela atual: {parent_key_source['label']}",
            f"-- Chave na tabela filha: {child_key_source['label']}",
            f"-- Coluna de destino na tabela filha: {child_value_source['label']}",
        ]
        parent_comments_sql = "\n".join(
            ["-- Expansao Raw via tabela filha (tabela mae)", *common_comment_lines]
        )
        child_comments_sql = "\n".join(
            ["-- Expansao Raw via tabela filha (tabela filha)", *common_comment_lines]
        )
        validation_sql = f"""
DO $pgdm_raw_expand_validate$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM {qualified_parent_table} AS parent
        WHERE {parent_key_json_sql} IS NOT NULL
          AND jsonb_typeof({parent_key_json_sql}) IN ('array', 'object')
    ) THEN
        RAISE EXCEPTION 'A chave selecionada na tabela atual precisa ser escalar.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM {qualified_parent_table} AS parent
        WHERE {parent_value_json_sql} IS NOT NULL
          AND jsonb_typeof({parent_value_json_sql}) = 'object'
    ) THEN
        RAISE EXCEPTION 'O header selecionado para tabela filha precisa ser escalar ou lista de valores.';
    END IF;
END
$pgdm_raw_expand_validate$;
""".strip()
        common_cte_sql = f"""
{setup_sql}WITH parent_source AS MATERIALIZED (
    SELECT
        parent.ctid AS row_id,
        NULLIF(btrim({parent_key_text_sql}), '') AS parent_key_text,
        {parent_value_json_sql} AS parent_value_json,
        row_number() OVER (ORDER BY parent.ctid) AS parent_order
    FROM {qualified_parent_table} AS parent
),
expanded AS MATERIALIZED (
    SELECT
        parent_source.row_id,
        parent_source.parent_key_text,
        parent_source.parent_order,
        element.ordinality AS item_order,
        element.value AS item_json,
        NULLIF(btrim(element.value #>> '{{}}'), '') AS raw_text
    FROM parent_source
    LEFT JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(parent_source.parent_value_json) = 'array' THEN parent_source.parent_value_json
            WHEN parent_source.parent_value_json IS NULL THEN '[]'::jsonb
            ELSE jsonb_build_array(parent_source.parent_value_json)
        END
    ) WITH ORDINALITY AS element(value, ordinality) ON true
),
converted AS MATERIALIZED (
    SELECT
        expanded.row_id,
        expanded.parent_key_text,
        expanded.parent_order,
        expanded.item_order,
        expanded.raw_text,
        lower(expanded.raw_text) AS normalized_text,
        {conversion_parts["converted_expr"]} AS converted_value
    FROM expanded
    WHERE expanded.item_json IS NULL
       OR jsonb_typeof(expanded.item_json) NOT IN ('array', 'object')
)""".strip()

        parent_sql = f"""
{parent_comments_sql}
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
ALTER TABLE {qualified_parent_table} ADD COLUMN IF NOT EXISTS {value_column} jsonb;
ALTER TABLE {qualified_parent_table} ADD COLUMN IF NOT EXISTS {fail_column} boolean NOT NULL DEFAULT false;
{validation_sql}
{common_cte_sql},
parent_payload AS MATERIALIZED (
    SELECT
        converted.row_id,
        COALESCE(
            jsonb_agg(
                COALESCE({conversion_parts["json_expr"]}, 'null'::jsonb)
                ORDER BY converted.item_order
            ) FILTER (WHERE converted.raw_text IS NOT NULL),
            '[]'::jsonb
        ) AS list_value,
        COALESCE(bool_or({conversion_parts["fail_expr"]}), false) AS has_fail
    FROM converted
    GROUP BY converted.row_id
),
source AS MATERIALIZED (
    SELECT
        parent.ctid AS row_id,
        parent_payload.list_value,
        COALESCE(parent_payload.has_fail, false) AS has_fail
    FROM {qualified_parent_table} AS parent
    LEFT JOIN parent_payload
      ON parent.ctid = parent_payload.row_id
)
UPDATE {qualified_parent_table} AS target
SET
    {value_column} = source.list_value,
    {fail_column} = source.has_fail
FROM source
WHERE target.ctid = source.row_id;
DO $pgdm_raw_expand_cleanup$
DECLARE
    fail_count bigint;
BEGIN
    SELECT COUNT(*) INTO fail_count
    FROM {qualified_parent_table}
    WHERE {fail_column} IS TRUE;

    IF fail_count = 0 THEN
        ALTER TABLE {qualified_parent_table} DROP COLUMN IF EXISTS {fail_column};
    END IF;
END
$pgdm_raw_expand_cleanup$;
COMMIT;
""".strip() + "\n"

        child_sql = f"""
{child_comments_sql}
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
{validation_sql}
{common_cte_sql},
child_rows AS MATERIALIZED (
    SELECT
        converted.parent_key_text::{child_key_source["type"]} AS child_key_value,
        converted.converted_value AS child_value,
        converted.parent_order,
        converted.item_order
    FROM converted
    WHERE converted.parent_key_text IS NOT NULL
      AND converted.raw_text IS NOT NULL
)
INSERT INTO {qualified_child_table} ({child_key_column}, {child_value_column})
SELECT
    child_rows.child_key_value,
    child_rows.child_value
FROM child_rows
ORDER BY child_rows.parent_order, child_rows.item_order;
COMMIT;
""".strip() + "\n"

        return {
            "parent_sql": parent_sql,
            "child_sql": child_sql,
        }

    def _build_child_expand_multi_sql_bundle(
        self,
        parent_full_table_name: str,
        child_full_table_name: str,
        parent_key_source: dict,
        child_key_source: dict,
        value_mappings: list[dict],
    ) -> dict:
        parent_schema_name, parent_table_name = split_table_name(parent_full_table_name)
        child_schema_name, child_table_name = split_table_name(child_full_table_name)
        qualified_parent_table = f"{sql_ident(parent_schema_name)}.{sql_ident(parent_table_name)}"
        qualified_child_table = f"{sql_ident(child_schema_name)}.{sql_ident(child_table_name)}"
        child_key_column = sql_ident(child_key_source["name"])
        parent_key_json_sql = self._build_expand_source_json_sql(
            "parent",
            parent_key_source["source_kind"],
            parent_key_source["name"],
        )
        parent_key_text_sql = self._build_expand_source_text_sql(
            "parent",
            parent_key_source["source_kind"],
            parent_key_source["name"],
        )

        mapping_specs = []
        for index, mapping in enumerate(value_mappings, start=1):
            raw_key = str(mapping["raw_key"]).strip()
            child_value_source = mapping["child_value_source"]
            postgres_type = mapping["postgres_type"]
            column_name = mapping["column_name"]
            conversion_parts = self._build_expand_child_conversion_parts(
                postgres_type,
                false_values=mapping.get("false_values"),
                true_values=mapping.get("true_values"),
                date_formats=mapping.get("date_formats"),
                custom_sql=mapping.get("conversion_sql"),
                helper_suffix=f"_{index}",
                raw_text_ref=f"raw_text_{index}",
                normalized_text_ref=f"normalized_text_{index}",
                converted_value_ref=f"converted_value_{index}",
                converted_raw_text_ref=f"converted_source.raw_text_{index}",
            )
            mapping_specs.append(
                {
                    "index": index,
                    "raw_key": raw_key,
                    "raw_key_literal": sql_literal(raw_key),
                    "parent_value_json_sql": self._build_raw_key_json_sql("parent", raw_key),
                    "value_json_name": f"value_json_{index}",
                    "item_json_name": f"item_json_{index}",
                    "raw_text_name": f"raw_text_{index}",
                    "normalized_text_name": f"normalized_text_{index}",
                    "converted_value_name": f"converted_value_{index}",
                    "list_value_name": f"list_value_{index}",
                    "has_fail_name": f"has_fail_{index}",
                    "child_value_alias": f"child_value_{index}",
                    "postgres_type": postgres_type,
                    "value_column": sql_ident(column_name),
                    "fail_column": sql_ident(f"{column_name}_fail"),
                    "child_value_column": sql_ident(child_value_source["name"]),
                    "child_value_label": child_value_source["label"],
                    "conversion_parts": conversion_parts,
                }
            )

        setup_sql_parts = [
            spec["conversion_parts"]["setup_sql"]
            for spec in mapping_specs
            if spec["conversion_parts"]["setup_sql"]
        ]
        setup_sql = ("\n".join(setup_sql_parts) + "\n") if setup_sql_parts else ""

        common_comment_lines = [
            f"-- Tabela filha de destino: {child_full_table_name}",
            f"-- Chave da tabela atual: {parent_key_source['label']}",
            f"-- Chave na tabela filha: {child_key_source['label']}",
        ]
        common_comment_lines.extend(
            f"-- Raw {spec['raw_key']}: destino={spec['child_value_label']} | tipo={spec['postgres_type']}"
            for spec in mapping_specs
        )
        execution_comments_sql = "\n".join(
            ["-- Expansao Raw via tabela filha (multiplos valores)", *common_comment_lines]
        )
        parent_comments_sql = "\n".join(
            ["-- Expansao Raw via tabela filha (tabela mae, multiplos valores)", *common_comment_lines]
        )
        child_comments_sql = "\n".join(
            ["-- Expansao Raw via tabela filha (tabela filha, multiplos valores)", *common_comment_lines]
        )

        validation_lines = [
            "DO $pgdm_raw_expand_validate$",
            "BEGIN",
            "    IF EXISTS (",
            "        SELECT 1",
            f"        FROM {qualified_parent_table} AS parent",
            f"        WHERE {parent_key_json_sql} IS NOT NULL",
            f"          AND jsonb_typeof({parent_key_json_sql}) IN ('array', 'object')",
            "    ) THEN",
            "        RAISE EXCEPTION 'A chave selecionada na tabela atual precisa ser escalar.';",
            "    END IF;",
            "",
        ]
        for spec in mapping_specs:
            validation_lines.extend(
                [
                    "    IF EXISTS (",
                    "        SELECT 1",
                    f"        FROM {qualified_parent_table} AS parent",
                    f"        WHERE {spec['parent_value_json_sql']} IS NOT NULL",
                    f"          AND jsonb_typeof({spec['parent_value_json_sql']}) = 'object'",
                    "    ) THEN",
                    "        RAISE EXCEPTION 'O header selecionado para tabela filha precisa ser escalar ou lista de valores: "
                    + spec["raw_key"].replace("'", "''")
                    + ".';",
                    "    END IF;",
                    "",
                ]
            )
        validation_lines.extend(["END", "$pgdm_raw_expand_validate$;"])
        validation_sql = "\n".join(validation_lines)

        parent_source_fields = [
            "        parent.ctid AS row_id",
            f"        NULLIF(btrim({parent_key_text_sql}), '') AS parent_key_text",
        ]
        parent_source_fields.extend(
            f"        {spec['parent_value_json_sql']} AS {spec['value_json_name']}"
            for spec in mapping_specs
        )
        parent_source_fields.append("        row_number() OVER (ORDER BY parent.ctid) AS parent_order")

        max_count_parts = ["1"]
        expanded_fields = [
            "        parent_source.row_id",
            "        parent_source.parent_key_text",
            "        parent_source.parent_order",
            "        series.item_order",
        ]
        converted_source_fields = [
            "        expanded.row_id",
            "        expanded.parent_key_text",
            "        expanded.parent_order",
            "        expanded.item_order",
        ]
        converted_fields = [
            "        converted_source.row_id",
            "        converted_source.parent_key_text",
            "        converted_source.parent_order",
            "        converted_source.item_order",
        ]
        child_rows_fields = [
            f"        converted.parent_key_text::{child_key_source['type']} AS child_key_value",
            "        converted.parent_order",
            "        converted.item_order",
        ]
        child_presence_checks = []
        parent_payload_fields = [
            "        converted.row_id",
        ]
        source_fields = [
            "        parent.ctid AS row_id",
        ]
        update_assignments = []
        cleanup_lines = [
            "DO $pgdm_raw_expand_cleanup$",
            "DECLARE",
            "    fail_count bigint;",
            "BEGIN",
        ]
        parent_alter_lines = []

        for spec in mapping_specs:
            item_json_expr = (
                f"CASE "
                f"WHEN jsonb_typeof(parent_source.{spec['value_json_name']}) = 'array' "
                f"THEN parent_source.{spec['value_json_name']} -> (series.item_order - 1) "
                f"WHEN parent_source.{spec['value_json_name']} IS NULL THEN NULL "
                f"WHEN series.item_order = 1 THEN parent_source.{spec['value_json_name']} "
                f"ELSE NULL "
                f"END"
            )
            raw_text_expr = (
                f"CASE "
                f"WHEN ({item_json_expr}) IS NULL "
                f"OR jsonb_typeof(({item_json_expr})) IN ('array', 'object') "
                f"THEN NULL "
                f"ELSE NULLIF(btrim(({item_json_expr}) #>> '{{}}'), '') "
                f"END"
            )
            cardinality_expr = (
                f"CASE "
                f"WHEN jsonb_typeof(parent_source.{spec['value_json_name']}) = 'array' "
                f"THEN jsonb_array_length(parent_source.{spec['value_json_name']}) "
                f"WHEN parent_source.{spec['value_json_name']} IS NULL THEN 0 "
                f"ELSE 1 "
                f"END"
            )
            max_count_parts.append(cardinality_expr)
            expanded_fields.append(f"        {item_json_expr} AS {spec['item_json_name']}")
            expanded_fields.append(f"        {raw_text_expr} AS {spec['raw_text_name']}")
            converted_source_fields.append(
                f"        expanded.{spec['raw_text_name']} AS {spec['raw_text_name']}"
            )
            converted_source_fields.append(
                f"        lower(expanded.{spec['raw_text_name']}) AS {spec['normalized_text_name']}"
            )
            converted_fields.append(
                f"        converted_source.{spec['raw_text_name']} AS {spec['raw_text_name']}"
            )
            converted_fields.append(
                f"        converted_source.{spec['normalized_text_name']} AS {spec['normalized_text_name']}"
            )
            converted_fields.append(
                f"        {spec['conversion_parts']['converted_expr']} AS {spec['converted_value_name']}"
            )
            child_rows_fields.append(
                f"        converted.{spec['converted_value_name']} AS {spec['child_value_alias']}"
            )
            child_presence_checks.append(f"converted.{spec['raw_text_name']} IS NOT NULL")
            parent_payload_fields.append(
                "        COALESCE("
                f"jsonb_agg(COALESCE({spec['conversion_parts']['json_expr']}, 'null'::jsonb) "
                "ORDER BY converted.item_order) "
                f"FILTER (WHERE converted.{spec['raw_text_name']} IS NOT NULL), "
                "'[]'::jsonb"
                f") AS {spec['list_value_name']}"
            )
            parent_payload_fields.append(
                f"        COALESCE(bool_or({spec['conversion_parts']['fail_expr']}), false) AS {spec['has_fail_name']}"
            )
            source_fields.append(
                f"        COALESCE(parent_payload.{spec['list_value_name']}, '[]'::jsonb) AS {spec['list_value_name']}"
            )
            source_fields.append(
                f"        COALESCE(parent_payload.{spec['has_fail_name']}, false) AS {spec['has_fail_name']}"
            )
            update_assignments.append(
                f"    {spec['value_column']} = source.{spec['list_value_name']}"
            )
            update_assignments.append(
                f"    {spec['fail_column']} = source.{spec['has_fail_name']}"
            )
            cleanup_lines.extend(
                [
                    f"    SELECT COUNT(*) INTO fail_count FROM {qualified_parent_table} WHERE {spec['fail_column']} IS TRUE;",
                    "    IF fail_count = 0 THEN",
                    f"        ALTER TABLE {qualified_parent_table} DROP COLUMN IF EXISTS {spec['fail_column']};",
                    "    END IF;",
                    "",
                ]
            )
            parent_alter_lines.append(
                f"ALTER TABLE {qualified_parent_table} ADD COLUMN IF NOT EXISTS {spec['value_column']} jsonb;"
            )
            parent_alter_lines.append(
                f"ALTER TABLE {qualified_parent_table} ADD COLUMN IF NOT EXISTS {spec['fail_column']} boolean NOT NULL DEFAULT false;"
            )

        cleanup_lines.extend(["END", "$pgdm_raw_expand_cleanup$;"])
        cleanup_sql = "\n".join(cleanup_lines)
        max_item_count_sql = "GREATEST(" + ", ".join(max_count_parts) + ")"
        parent_source_fields_sql = ",\n".join(parent_source_fields)
        expanded_fields_sql = ",\n".join(expanded_fields)
        converted_source_fields_sql = ",\n".join(converted_source_fields)
        converted_fields_sql = ",\n".join(converted_fields)

        common_cte_sql = f"""
{setup_sql}WITH parent_source AS MATERIALIZED (
    SELECT
{parent_source_fields_sql}
    FROM {qualified_parent_table} AS parent
),
expanded AS MATERIALIZED (
    SELECT
{expanded_fields_sql}
    FROM parent_source
    JOIN LATERAL generate_series(1, {max_item_count_sql}) AS series(item_order) ON true
),
converted_source AS MATERIALIZED (
    SELECT
{converted_source_fields_sql}
    FROM expanded
),
converted AS MATERIALIZED (
    SELECT
{converted_fields_sql}
    FROM converted_source
)""".strip()

        child_insert_columns_sql = ", ".join(
            [child_key_column, *[spec["child_value_column"] for spec in mapping_specs]]
        )
        child_select_columns_sql = ",\n".join(
            ["    child_rows.child_key_value", *[f"    child_rows.{spec['child_value_alias']}" for spec in mapping_specs]]
        )
        child_presence_sql = " OR ".join(child_presence_checks) if child_presence_checks else "false"
        child_rows_fields_sql = ",\n".join(child_rows_fields)

        child_rows_sql = f"""
child_rows AS MATERIALIZED (
    SELECT
{child_rows_fields_sql}
    FROM converted
    WHERE converted.parent_key_text IS NOT NULL
      AND ({child_presence_sql})
)""".strip()

        parent_payload_fields_sql = ",\n".join(parent_payload_fields)
        source_fields_sql = ",\n".join(source_fields)
        parent_payload_sql = f"""
parent_payload AS MATERIALIZED (
    SELECT
{parent_payload_fields_sql}
    FROM converted
    GROUP BY converted.row_id
),
source AS MATERIALIZED (
    SELECT
{source_fields_sql}
    FROM {qualified_parent_table} AS parent
    LEFT JOIN parent_payload
      ON parent.ctid = parent_payload.row_id
)""".strip()

        update_assignments_sql = ",\n".join(update_assignments)
        parent_update_sql = f"""
UPDATE {qualified_parent_table} AS target
SET
{update_assignments_sql}
FROM source
WHERE target.ctid = source.row_id;
{cleanup_sql}
""".strip()

        parent_alter_sql = "\n".join(parent_alter_lines)
        execution_sql = f"""
{execution_comments_sql}
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
{parent_alter_sql}
{validation_sql}
{common_cte_sql},
{child_rows_sql},
inserted_child AS (
    INSERT INTO {qualified_child_table} ({child_insert_columns_sql})
    SELECT
{child_select_columns_sql}
    FROM child_rows
    ORDER BY child_rows.parent_order, child_rows.item_order
    RETURNING 1
),
{parent_payload_sql}
{parent_update_sql}
COMMIT;
""".strip() + "\n"

        parent_sql = f"""
{parent_comments_sql}
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
{parent_alter_sql}
{validation_sql}
{common_cte_sql},
{parent_payload_sql}
{parent_update_sql}
COMMIT;
""".strip() + "\n"

        child_sql = f"""
{child_comments_sql}
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';
{validation_sql}
{common_cte_sql},
{child_rows_sql}
INSERT INTO {qualified_child_table} ({child_insert_columns_sql})
SELECT
{child_select_columns_sql}
FROM child_rows
ORDER BY child_rows.parent_order, child_rows.item_order;
COMMIT;
""".strip() + "\n"

        return {
            "execution_sql": execution_sql,
            "parent_sql": parent_sql,
            "child_sql": child_sql,
        }

    def expand_raw_value_key_from_child_table(
        self,
        database_name: str,
        parent_full_table_name: str,
        raw_key: str,
        child_full_table_name: str,
        parent_key_source: dict,
        child_key_source: dict,
        child_value_source: dict | None,
        column_name: str | None,
        column_type: str | None,
        false_values=None,
        true_values=None,
        date_formats=None,
        value_mappings=None,
        cancel_event=None,
        progress_callback=None,
    ):
        parent_key_source = self._normalize_expand_source(parent_key_source, "chave da tabela atual")
        child_key_source = self._normalize_expand_source(child_key_source, "chave da tabela filha")
        parent_schema_name, parent_table_name = split_table_name(parent_full_table_name)
        child_schema_name, child_table_name = split_table_name(child_full_table_name)
        child_key_type = str(child_key_source.get("type") or "").strip()
        if child_key_source["source_kind"] != "column":
            raise RuntimeError("A tabela filha deve usar uma coluna real para a chave.")
        if not child_key_type:
            raise RuntimeError("Nao foi possivel identificar o tipo da chave na tabela filha.")

        normalized_mappings = []
        mapping_payload = list(value_mappings or [])
        if not mapping_payload:
            mapping_payload = [
                {
                    "raw_key": raw_key,
                    "child_value_source": child_value_source,
                    "column_name": column_name,
                    "column_type": column_type,
                    "false_values": false_values,
                    "true_values": true_values,
                    "date_formats": date_formats,
                }
            ]

        seen_raw_keys = set()
        seen_target_column_names = set()
        seen_child_value_columns = set()
        for mapping_index, mapping in enumerate(mapping_payload, start=1):
            mapping_raw_key = str((mapping or {}).get("raw_key") or "").strip()
            if not mapping_raw_key:
                raise RuntimeError(f"O mapeamento {mapping_index} nao possui header raw.")

            mapping_column_name = str((mapping or {}).get("column_name") or "").strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", mapping_column_name):
                raise RuntimeError(
                    f"Nome de coluna invalido para expansao no mapeamento {mapping_index}."
                )
            if len(mapping_column_name) > 58:
                raise RuntimeError(
                    f"Nome de coluna longo demais para expansao no mapeamento {mapping_index}."
                )

            mapping_column_type = str((mapping or {}).get("column_type") or "").strip()
            normalized_mapping_type = self._normalize_data_type_name(mapping_column_type)
            if not normalized_mapping_type:
                raise RuntimeError(
                    f"Tipo de coluna nao suportado no mapeamento {mapping_index}: {mapping_column_type or '(vazio)'}"
                )
            supported_mapping_type = self.resolve_supported_expand_column_type(mapping_column_type)
            postgres_type = (
                self.RAW_EXPAND_TYPE_MAP[supported_mapping_type]
                if supported_mapping_type
                else mapping_column_type
            )

            normalized_child_value_source = self._normalize_expand_source(
                (mapping or {}).get("child_value_source"),
                f"valor {mapping_index} da tabela filha",
            )
            if normalized_child_value_source["source_kind"] != "column":
                raise RuntimeError("A tabela filha deve usar colunas reais para os valores.")

            type_conversion_mode = str((mapping or {}).get("type_conversion_mode") or "").strip().lower()
            conversion_sql = str((mapping or {}).get("conversion_sql") or "").strip()
            child_value_db_type = str(normalized_child_value_source.get("type") or "").strip()
            child_value_type = self._normalize_data_type_name(child_value_db_type)
            supported_child_type = self.resolve_supported_expand_column_type(child_value_db_type)
            selected_value_type = supported_mapping_type or normalized_mapping_type
            effective_column_type = mapping_column_type
            effective_postgres_type = postgres_type

            if child_value_type and child_value_type != selected_value_type:
                if type_conversion_mode == "custom_sql":
                    if not conversion_sql:
                        raise RuntimeError(
                            "Informe uma expressao SQL de conversao para o mapeamento com tipo diferente."
                        )
                    if supported_child_type:
                        effective_column_type = supported_child_type
                        effective_postgres_type = self.RAW_EXPAND_TYPE_MAP[supported_child_type]
                    else:
                        effective_postgres_type = child_value_db_type
                elif type_conversion_mode == "child_type":
                    if not supported_child_type:
                        raise RuntimeError(
                            "O tipo da coluna selecionada na tabela filha nao possui conversao automatica suportada."
                        )
                    effective_column_type = supported_child_type
                    effective_postgres_type = self.RAW_EXPAND_TYPE_MAP[supported_child_type]
                else:
                    raise RuntimeError(
                        "O tipo escolhido nao corresponde ao tipo da coluna selecionada na tabela filha."
                    )
            else:
                conversion_sql = ""

            if mapping_raw_key in seen_raw_keys:
                raise RuntimeError(f"O header raw {mapping_raw_key} foi informado mais de uma vez.")
            if mapping_column_name in seen_target_column_names:
                raise RuntimeError(
                    f"A coluna de destino {mapping_column_name} foi informada mais de uma vez."
                )
            child_value_name = str(normalized_child_value_source.get("name") or "").strip()
            if child_value_name in seen_child_value_columns:
                raise RuntimeError(
                    f"A coluna da tabela filha {child_value_name} foi informada mais de uma vez."
                )

            seen_raw_keys.add(mapping_raw_key)
            seen_target_column_names.add(mapping_column_name)
            seen_child_value_columns.add(child_value_name)
            normalized_mappings.append(
                {
                    "raw_key": mapping_raw_key,
                    "child_value_source": normalized_child_value_source,
                    "column_name": mapping_column_name,
                    "column_type": effective_column_type,
                    "postgres_type": effective_postgres_type,
                    "false_values": (mapping or {}).get("false_values"),
                    "true_values": (mapping or {}).get("true_values"),
                    "date_formats": (mapping or {}).get("date_formats"),
                    "type_conversion_mode": type_conversion_mode,
                    "conversion_sql": conversion_sql,
                }
            )

        multi_value_keys = set(
            self.get_raw_value_keys_with_multiple_values(
                database_name,
                parent_full_table_name,
                cancel_event=cancel_event,
            )
        )
        non_multi_headers = [
            mapping["raw_key"]
            for mapping in normalized_mappings
            if mapping["raw_key"] not in multi_value_keys
        ]
        if non_multi_headers:
            raise RuntimeError(
                "A expansao via tabela filha so aceita headers com multiplos valores. "
                "Headers invalidos: "
                + ", ".join(non_multi_headers)
                + "."
            )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            10,
            f"Preparando expansao via tabela filha {child_full_table_name}...",
        )
        self._notify_progress(
            progress_callback,
            "convert",
            20,
            "Desmembrando valores do raw agrupado para a tabela filha...",
        )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "alter",
            35,
            "Preparando insercao na tabela filha...",
        )
        primary_mapping = normalized_mappings[0]
        if len(normalized_mappings) == 1:
            version_recipes = self._build_child_expand_version_sql_recipes(
                parent_full_table_name=parent_full_table_name,
                raw_key=primary_mapping["raw_key"],
                child_full_table_name=child_full_table_name,
                parent_key_source=parent_key_source,
                child_key_source=child_key_source,
                child_value_source=primary_mapping["child_value_source"],
                column_name=primary_mapping["column_name"],
                postgres_type=primary_mapping["postgres_type"],
                false_values=primary_mapping.get("false_values"),
                true_values=primary_mapping.get("true_values"),
                date_formats=primary_mapping.get("date_formats"),
                custom_sql=primary_mapping.get("conversion_sql"),
            )
            sql = version_recipes["child_sql"]
        else:
            sql_bundle = self._build_child_expand_multi_sql_bundle(
                parent_full_table_name=parent_full_table_name,
                child_full_table_name=child_full_table_name,
                parent_key_source=parent_key_source,
                child_key_source=child_key_source,
                value_mappings=normalized_mappings,
            )
            sql = sql_bundle["child_sql"]
            version_recipes = {
                "child_sql": sql_bundle["child_sql"],
            }
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self._notify_progress(
            progress_callback,
            "convert",
            60,
            "Inserindo linhas na tabela filha...",
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        self._notify_progress(
            progress_callback,
            "convert",
            100,
            "Tabela filha preenchida com sucesso.",
        )
        return {
            "schema_name": child_schema_name,
            "table_name": child_table_name,
            "parent_schema_name": parent_schema_name,
            "parent_table_name": parent_table_name,
            "child_schema_name": child_schema_name,
            "child_table_name": child_table_name,
            "raw_key": primary_mapping["raw_key"],
            "column_name": primary_mapping["column_name"],
            "column_type": primary_mapping["column_type"],
            "mapping_count": len(normalized_mappings),
            "value_mappings": [
                {
                    "raw_key": mapping["raw_key"],
                    "column_name": mapping["column_name"],
                    "child_value_column": mapping["child_value_source"]["name"],
                }
                for mapping in normalized_mappings
            ],
            "child_sql_recipe": version_recipes["child_sql"],
            "sql_recipe": version_recipes["child_sql"],
        }

    @staticmethod
    def _build_raw_text_expand_sql(
        qualified_table: str,
        value_column: str,
        fail_column: str,
        raw_value_sql: str,
        postgres_type: str,
    ):
        return f"""
WITH source AS MATERIALIZED (
    SELECT ctid AS row_id, NULLIF(btrim({raw_value_sql}), '') AS raw_text
    FROM {qualified_table}
)
UPDATE {qualified_table} AS target
SET
    {value_column} = source.raw_text::{postgres_type},
    {fail_column} = false
FROM source
WHERE target.ctid = source.row_id;
""".strip()

    @staticmethod
    def _build_raw_safe_cast_expand_sql(
        qualified_table: str,
        value_column: str,
        fail_column: str,
        raw_value_sql: str,
        postgres_type: str,
    ):
        return f"""
CREATE OR REPLACE FUNCTION pg_temp.pgdm_raw_expand_cast_value(input_text text)
RETURNS {postgres_type}
LANGUAGE plpgsql
AS $pgdm_raw_expand_cast$
BEGIN
    IF input_text IS NULL THEN
        RETURN NULL;
    END IF;

    RETURN input_text::{postgres_type};
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END
$pgdm_raw_expand_cast$;

WITH source AS MATERIALIZED (
    SELECT ctid AS row_id, NULLIF(btrim({raw_value_sql}), '') AS raw_text
    FROM {qualified_table}
),
converted AS MATERIALIZED (
    SELECT
        row_id,
        raw_text,
        pg_temp.pgdm_raw_expand_cast_value(raw_text) AS converted_value
    FROM source
)
UPDATE {qualified_table} AS target
SET
    {value_column} = converted.converted_value,
    {fail_column} = converted.raw_text IS NOT NULL AND converted.converted_value IS NULL
FROM converted
WHERE target.ctid = converted.row_id;
""".strip()

    @classmethod
    def _build_raw_datetime_expand_sql(
        cls,
        qualified_table: str,
        value_column: str,
        fail_column: str,
        raw_value_sql: str,
        postgres_type: str,
        date_formats,
    ):
        format_specs = cls._normalize_raw_datetime_formats(date_formats)
        if not format_specs:
            raise RuntimeError("Informe ao menos um formato de data/hora para expansao.")

        postgres_formats = [item["format"] for item in format_specs]
        postgres_patterns = [item["pattern"] for item in format_specs]
        format_array = "ARRAY[" + ", ".join(sql_literal(item) for item in postgres_formats) + "]::text[]"
        pattern_array = "ARRAY[" + ", ".join(sql_literal(item) for item in postgres_patterns) + "]::text[]"
        if postgres_type == "date":
            parse_expression = "to_timestamp(input_text, formats[format_index])::date"
        elif postgres_type == "timestamp":
            parse_expression = "to_timestamp(input_text, formats[format_index])::timestamp"
        elif postgres_type == "timestamptz":
            parse_expression = "to_timestamp(input_text, formats[format_index])"
        elif postgres_type == "time":
            parse_expression = "to_timestamp(input_text, formats[format_index])::time"
        else:
            raise RuntimeError(f"Tipo temporal nao suportado: {postgres_type}")

        return f"""
CREATE OR REPLACE FUNCTION pg_temp.pgdm_raw_expand_parse_datetime(input_text text, formats text[], patterns text[])
RETURNS {postgres_type}
LANGUAGE plpgsql
AS $pgdm_raw_expand_datetime$
DECLARE
    format_index integer;
BEGIN
    IF input_text IS NULL THEN
        RETURN NULL;
    END IF;

    FOR format_index IN 1..COALESCE(array_length(formats, 1), 0) LOOP
        IF patterns[format_index] IS NOT NULL AND input_text !~* patterns[format_index] THEN
            CONTINUE;
        END IF;

        BEGIN
            RETURN {parse_expression};
        EXCEPTION WHEN OTHERS THEN
        END;
    END LOOP;

    RETURN NULL;
END
$pgdm_raw_expand_datetime$;

WITH source AS MATERIALIZED (
    SELECT ctid AS row_id, NULLIF(btrim({raw_value_sql}), '') AS raw_text
    FROM {qualified_table}
),
converted AS MATERIALIZED (
    SELECT
        row_id,
        raw_text,
        pg_temp.pgdm_raw_expand_parse_datetime(raw_text, {format_array}, {pattern_array}) AS converted_value
    FROM source
)
UPDATE {qualified_table} AS target
SET
    {value_column} = converted.converted_value,
    {fail_column} = converted.raw_text IS NOT NULL AND converted.converted_value IS NULL
FROM converted
WHERE target.ctid = converted.row_id;
""".strip()

    @classmethod
    def _normalize_raw_datetime_formats(cls, date_formats) -> list[dict[str, str]]:
        normalized = []
        seen = set()
        for raw_format in date_formats or []:
            postgres_format = cls._raw_datetime_format_to_postgres(raw_format)
            if not postgres_format or postgres_format in seen:
                continue
            seen.add(postgres_format)
            normalized.append(
                {
                    "format": postgres_format,
                    "pattern": cls._raw_datetime_format_to_regex(raw_format),
                }
            )
        return normalized

    @staticmethod
    def _raw_datetime_format_to_postgres(raw_format: str) -> str:
        source = str(raw_format or "").strip()
        upper_source = source.upper()
        parts = []
        index = 0
        previous_token = None
        tokens = ("HH24", "AAAA", "YYYY", "DD", "HH", "MI", "SS", "AA", "YY", "MM")

        while index < len(source):
            matched = None
            for token in tokens:
                if upper_source.startswith(token, index):
                    matched = token
                    break

            if not matched:
                parts.append(source[index])
                index += 1
                continue

            if matched in {"AAAA", "YYYY"}:
                postgres_token = "YYYY"
            elif matched in {"AA", "YY"}:
                postgres_token = "YY"
            elif matched == "HH":
                postgres_token = "HH24"
            elif matched == "MM":
                previous_char = source[index - 1] if index > 0 else ""
                next_index = index + len(matched)
                next_char = source[next_index] if next_index < len(source) else ""
                if previous_token in {"HH", "HH24", "MI"} or previous_char == ":" or next_char == ":":
                    postgres_token = "MI"
                else:
                    postgres_token = "MM"
            else:
                postgres_token = matched

            parts.append(postgres_token)
            previous_token = matched
            index += len(matched)

        return "".join(parts).strip()

    @staticmethod
    def _raw_datetime_format_to_regex(raw_format: str) -> str:
        source = str(raw_format or "").strip()
        upper_source = source.upper()
        parts = []
        index = 0
        tokens = ("HH24", "AAAA", "YYYY", "TZH", "TZM", "DD", "HH", "MI", "SS", "MS", "US", "AA", "YY", "MM")

        while index < len(source):
            matched = None
            for token in tokens:
                if upper_source.startswith(token, index):
                    matched = token
                    break

            if not matched:
                char = source[index]
                if char.isspace():
                    parts.append(r"[[:space:]]+")
                else:
                    parts.append(re.escape(char))
                index += 1
                continue

            if matched in {"AAAA", "YYYY"}:
                regex_part = r"[0-9]{4}"
            elif matched in {"AA", "YY"}:
                regex_part = r"[0-9]{2}"
            elif matched in {"HH", "HH24", "MI", "MM", "DD"}:
                regex_part = r"[0-9]{1,2}"
            elif matched == "SS":
                regex_part = r"[0-9]{1,2}([.,][0-9]+)?"
            elif matched in {"MS", "US"}:
                regex_part = r"[0-9]{1,6}"
            elif matched in {"TZH", "TZM"}:
                regex_part = r"[+-]?[0-9]{1,2}"
            else:
                regex_part = r".+"

            parts.append(regex_part)
            index += len(matched)

        return "^" + "".join(parts).strip() + "$"

    @staticmethod
    def _build_raw_boolean_expand_sql(
        qualified_table: str,
        value_column: str,
        fail_column: str,
        raw_value_sql: str,
        false_values,
        true_values,
    ):
        false_array = PostgresAdminService._text_array_literal(false_values)
        true_array = PostgresAdminService._text_array_literal(true_values)
        return f"""
WITH source AS MATERIALIZED (
    SELECT
        ctid AS row_id,
        NULLIF(btrim({raw_value_sql}), '') AS raw_text
    FROM {qualified_table}
),
converted AS MATERIALIZED (
    SELECT
        row_id,
        raw_text,
        lower(raw_text) AS normalized_text,
        CASE
            WHEN lower(raw_text) = ANY({true_array}) THEN true
            WHEN lower(raw_text) = ANY({false_array}) THEN false
            ELSE NULL
        END AS converted_value
    FROM source
)
UPDATE {qualified_table} AS target
SET
    {value_column} = converted.converted_value,
    {fail_column} = (
        converted.raw_text IS NOT NULL
        AND converted.normalized_text <> ALL({true_array})
        AND converted.normalized_text <> ALL({false_array})
    )
FROM converted
WHERE target.ctid = converted.row_id;
""".strip()

    @staticmethod
    def _text_array_literal(values) -> str:
        normalized = [
            str(value).strip().lower()
            for value in (values or [])
            if str(value).strip()
        ]
        if not normalized:
            return "ARRAY[]::text[]"
        return "ARRAY[" + ", ".join(sql_literal(value) for value in normalized) + "]::text[]"

    def build_create_table_recipe(self, database_name: str, full_table_name: str, cancel_event=None):
        schema_name, table_name = split_table_name(full_table_name)
        columns = self.get_table_column_definitions(database_name, full_table_name, cancel_event=cancel_event)

        if columns:
            column_sql = []
            for column in columns:
                line = f"{sql_ident(column['name'])} {column['type']}"
                if column["default"]:
                    line += f" DEFAULT {column['default']}"
                if column["not_null"]:
                    line += " NOT NULL"
                column_sql.append(f"    {line}")
            create_table_sql = (
                f"CREATE TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} (\n"
                + ",\n".join(column_sql)
                + "\n);"
            )
        else:
            create_table_sql = f"CREATE TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} ();"

        return (
            f"CREATE SCHEMA IF NOT EXISTS {sql_ident(schema_name)};\n"
            f"{create_table_sql}"
        )

    def build_raw_load_recipe(self, full_table_name: str, raw_source: dict | None = None) -> str:
        schema_name, table_name = split_table_name(full_table_name)
        comments = []
        system_columns = self._get_raw_source_system_columns(raw_source)
        if raw_source:
            appended_row_count = raw_source.get("appended_row_count", self._raw_source_row_count(raw_source))
            append_start_row = raw_source.get("append_start_row")
            append_end_row = raw_source.get("append_end_row")
            assigned_raw_id_start = raw_source.get("assigned_raw_id_start")
            assigned_raw_id_end = raw_source.get("assigned_raw_id_end")
            raw_dump_scope = str(raw_source.get("raw_dump_scope") or "").strip()
            comments.extend([
                f"-- Raw source: {raw_source['file_name']}",
                f"-- Raw hash: {raw_source['file_hash']}",
                f"-- Raw ingested at: {raw_source['ingested_at']}",
                "-- Raw load mode: append",
                f"-- Raw rows appended: {appended_row_count}",
                f"-- Raw total rows after import: {self._raw_source_row_count(raw_source)}",
            ])
            if raw_source.get("raw_schema"):
                comments.insert(3, f"-- Raw schema: {raw_source['raw_schema']}")
            if raw_dump_scope:
                comments.append(f"-- Raw dump scope: {raw_dump_scope}")
            if append_start_row is not None and append_end_row is not None:
                comments.append(f"-- Raw appended table row range: {append_start_row}-{append_end_row}")
            if assigned_raw_id_start is not None and assigned_raw_id_end is not None:
                comments.append(f"-- Raw appended raw_id range: {assigned_raw_id_start}-{assigned_raw_id_end}")

        statements = [
            f"CREATE SCHEMA IF NOT EXISTS {sql_ident(schema_name)};",
            f"CREATE TABLE IF NOT EXISTS {sql_ident(schema_name)}.{sql_ident(table_name)} ();",
        ]
        if "raw_hash" in system_columns:
            statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_hash')} text;"
            )
        if "raw_ingested_at" in system_columns:
            statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_ingested_at')} timestamptz;"
            )
        if "raw_schema" in system_columns:
            statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_schema')} text;"
            )
        if "raw_tab" in system_columns:
            statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_tab')} text;"
            )
        if "raw_id" in system_columns:
            statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_id')} bigint;"
            )
        if "raw" in system_columns:
            statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw')} jsonb;"
            )

        return "\n".join(comments + statements).strip()

    @staticmethod
    def build_restore_recipe(restored_version_code: str) -> str:
        return f"-- RESTORE VERSION {restored_version_code}"

    def _build_raw_dump_base_dir(self, database_name: str, schema_name: str, table_name: str) -> str:
        safe_path = f"{slugify(database_name)}/{slugify(schema_name)}/{slugify(table_name)}"
        return f"{self.remote_storage_root}/raw_versions/{safe_path}"

    @staticmethod
    def resolve_original_table_name(table_name: str) -> str:
        return re.sub(r"__deleted_\d+$", "", table_name)

    @staticmethod
    def _build_raw_dump_version_label(version_code: str | None) -> str:
        normalized = str(version_code or "").strip()
        if not normalized:
            return "legacy"
        try:
            return f"v{version_to_int(normalized) + 1}_{normalized}"
        except Exception:
            cleaned = slugify(normalized)
            return f"v_{cleaned}" if cleaned else "legacy"

    def _build_raw_dump_file_name(self, raw_source: dict, version_code: str | None = None) -> str:
        stamp = str(raw_source["ingested_at"]).replace(":", "-")
        hash_prefix = str(raw_source.get("file_hash") or "sem_hash")[:12] or "sem_hash"
        version_label = self._build_raw_dump_version_label(version_code)
        return f"{version_label}_{stamp}_{hash_prefix}.dump.gz"

    def _build_raw_dump_paths(
        self,
        database_name: str,
        full_table_name: str,
        raw_source: dict,
        version_code: str | None = None,
    ):
        schema_name, table_name = split_table_name(full_table_name)
        file_name = self._build_raw_dump_file_name(raw_source, version_code=version_code)
        base_dir = self._build_raw_dump_base_dir(database_name, schema_name, table_name)
        return base_dir, f"{base_dir}/{file_name}"

    def _build_raw_dump_where_sql(self, raw_source: dict, system_columns) -> str:
        assigned_raw_id_start = raw_source.get("assigned_raw_id_start")
        assigned_raw_id_end = raw_source.get("assigned_raw_id_end")
        if (
            "raw_id" in system_columns
            and assigned_raw_id_start is not None
            and assigned_raw_id_end is not None
        ):
            return (
                f"WHERE {sql_ident('raw_id')} BETWEEN "
                f"{int(assigned_raw_id_start)} AND {int(assigned_raw_id_end)}"
            )

        if "raw_hash" in system_columns and "raw_ingested_at" in system_columns:
            pair_clauses = []
            seen_pairs = set()
            for source_info in list(raw_source.get("source_files") or []):
                record_hash = str(source_info.get("file_hash") or "").strip()
                record_ingested_at = str(source_info.get("ingested_at") or "").strip()
                if not record_hash or not record_ingested_at:
                    continue
                pair_key = (record_hash, record_ingested_at)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                pair_clauses.append(
                    f"({sql_ident('raw_hash')} = {sql_literal(record_hash)} AND "
                    f"{sql_ident('raw_ingested_at')} = {sql_literal(record_ingested_at)}::timestamptz)"
                )

            if not pair_clauses:
                record_hash = str(raw_source.get("file_hash") or "").strip()
                record_ingested_at = str(raw_source.get("ingested_at") or "").strip()
                if record_hash and record_ingested_at:
                    pair_clauses.append(
                        f"({sql_ident('raw_hash')} = {sql_literal(record_hash)} AND "
                        f"{sql_ident('raw_ingested_at')} = {sql_literal(record_ingested_at)}::timestamptz)"
                    )

            if pair_clauses:
                return "WHERE " + " OR ".join(pair_clauses)

        return ""

    def move_archived_table_raw_dumps(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        archived_table_name: str,
        cancel_event=None,
    ):
        source_base_dir = self._build_raw_dump_base_dir(database_name, schema_name, table_name)
        target_base_dir = self._build_raw_dump_base_dir(database_name, schema_name, archived_table_name)
        if source_base_dir == target_base_dir:
            return

        target_parent_dir = target_base_dir.rsplit("/", 1)[0]
        move_command = (
            f"if [ -d {shlex.quote(source_base_dir)} ]; then "
            f"mkdir -p {shlex.quote(target_parent_dir)} && "
            f"mkdir -p {shlex.quote(target_base_dir)} && "
            f"find {shlex.quote(source_base_dir)} -mindepth 1 -maxdepth 1 -type f "
            f"-exec mv {{}} {shlex.quote(target_base_dir)}/ \\; && "
            f"rmdir {shlex.quote(source_base_dir)} 2>/dev/null || true; "
            "fi"
        )
        self.run_remote_command(move_command, cancel_event=cancel_event)

        sql = f"""
UPDATE {CONTROL_TABLE_VERSIONS}
SET raw_dump_path = CASE
    WHEN raw_dump_path = {sql_literal(source_base_dir)} THEN {sql_literal(target_base_dir)}
    WHEN raw_dump_path LIKE {sql_literal(source_base_dir + '/%')} THEN
        {sql_literal(target_base_dir)} || substr(raw_dump_path, {len(source_base_dir) + 1})
    ELSE raw_dump_path
END
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
  AND raw_dump_path IS NOT NULL;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(command, cancel_event=cancel_event)

    def _ensure_control_metadata(self, cancel_event=None, trace_context: dict | None = None):
        self.ensure_admin_schema(cancel_event=cancel_event, trace_context=trace_context)

    @staticmethod
    def _normalize_data_dictionary_aliases(aliases) -> str:
        normalized_items = []
        seen_items = set()
        for raw_item in str(aliases or "").split(","):
            alias = raw_item.strip()
            if not alias:
                continue
            alias_key = alias.lower()
            if alias_key in seen_items:
                continue
            seen_items.add(alias_key)
            normalized_items.append(alias)
        return ", ".join(normalized_items)

    @classmethod
    def _normalize_dictionary_data_type(cls, data_type: str) -> str:
        value = str(data_type or "").strip()
        if not value:
            return ""

        resolved_type = cls.resolve_supported_data_dictionary_type(value)
        if not resolved_type:
            raise RuntimeError(f"Tipo canonico nao suportado no data dictionary: {value}.")
        return resolved_type

    @classmethod
    def _normalize_data_dictionary_payload(cls, column_payload: dict, position: int) -> dict:
        column_name = str((column_payload or {}).get("column_name") or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", column_name):
            raise RuntimeError(f"Nome de coluna invalido no dicionario de dados ({position}).")

        dictionary = dict((column_payload or {}).get("dictionary") or {})
        standard_name = str(dictionary.get("standard_name") or "").strip()
        definition = str(dictionary.get("definition") or "").strip()
        units = str(dictionary.get("units") or "").strip()
        value_domain = str(dictionary.get("value_domain") or "").strip()
        aliases = cls._normalize_data_dictionary_aliases(dictionary.get("aliases"))
        data_type = cls._normalize_dictionary_data_type(
            dictionary.get("data_type") or (column_payload or {}).get("column_type")
        )
        reuse_existing = bool(dictionary.get("reuse_existing"))
        existing_standard_name = str(dictionary.get("existing_standard_name") or "").strip()

        if not standard_name:
            raise RuntimeError(f"Standard name ausente para a coluna {column_name}.")
        if not reuse_existing and not definition:
            raise RuntimeError(f"Definition ausente para a coluna {column_name}.")
        if not units:
            raise RuntimeError(f"Units ausentes para a coluna {column_name}.")
        if not reuse_existing and not value_domain:
            raise RuntimeError(f"Value domain ausente para a coluna {column_name}.")

        resolved_standard_name = existing_standard_name if reuse_existing and existing_standard_name else standard_name
        return {
            "column_name": column_name,
            "standard_name": standard_name,
            "definition": definition,
            "units": units,
            "value_domain": value_domain,
            "aliases": aliases,
            "data_type": data_type,
            "reuse_existing": reuse_existing,
            "resolved_standard_name": resolved_standard_name,
        }

    def find_data_dictionary_entry(self, search_term: str, cancel_event=None):
        self._ensure_control_metadata(cancel_event=cancel_event)
        normalized_term = str(search_term or "").strip()
        if not normalized_term:
            return None

        search_literal = sql_literal(normalized_term)
        sql = f"""
SELECT row_to_json(result_row)
FROM (
    SELECT
        dictionary.standard_name,
        dictionary.definition,
        dictionary.units,
        dictionary.value_domain,
        COALESCE(dictionary.aliases, '') AS aliases,
        COALESCE(dictionary.data_type, '') AS data_type,
        (
            SELECT COUNT(*)
            FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE} AS usage
            WHERE usage.standard_name = dictionary.standard_name
        ) AS usage_count
    FROM {CONTROL_TABLE_DATA_DICTIONARY} AS dictionary
    WHERE lower(dictionary.standard_name) = lower({search_literal})
       OR EXISTS (
            SELECT 1
            FROM unnest(string_to_array(COALESCE(dictionary.aliases, ''), ',')) AS alias_item
            WHERE lower(btrim(alias_item)) = lower({search_literal})
       )
    ORDER BY
        CASE
            WHEN lower(dictionary.standard_name) = lower({search_literal}) THEN 0
            ELSE 1
        END,
        dictionary.standard_name
    LIMIT 1
) AS result_row;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        if not output:
            return None
        return json.loads(output)

    def list_data_dictionary_entries(self, cancel_event=None) -> list[dict]:
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT
    dictionary.standard_name,
    dictionary.definition,
    dictionary.units,
    dictionary.value_domain,
    COALESCE(dictionary.aliases, '') AS aliases,
    COALESCE(dictionary.data_type, '') AS data_type,
    COUNT(usage.standard_name) AS usage_count
FROM {CONTROL_TABLE_DATA_DICTIONARY} AS dictionary
LEFT JOIN {CONTROL_TABLE_DATA_DICTIONARY_USAGE} AS usage
    ON usage.standard_name = dictionary.standard_name
GROUP BY
    dictionary.standard_name,
    dictionary.definition,
    dictionary.units,
    dictionary.value_domain,
    dictionary.aliases,
    dictionary.data_type
ORDER BY lower(dictionary.standard_name), dictionary.standard_name;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        headers, rows = self.parse_csv_output(output)
        if not headers:
            return []

        index_map = {header: idx for idx, header in enumerate(headers)}
        entries = []
        for row in rows:
            entries.append(
                {
                    "standard_name": row[index_map["standard_name"]],
                    "definition": row[index_map["definition"]],
                    "units": row[index_map["units"]],
                    "value_domain": row[index_map["value_domain"]],
                    "aliases": row[index_map["aliases"]],
                    "data_type": row[index_map["data_type"]],
                    "usage_count": int(row[index_map["usage_count"]] or 0),
                }
            )
        return entries

    def list_data_dictionary_usage_entries(self, cancel_event=None) -> list[dict]:
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT
    database_name,
    schema_name,
    table_name,
    column_name,
    standard_name,
    COALESCE(linked_at::text, '') AS linked_at,
    COALESCE(linked_by, '') AS linked_by
FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
ORDER BY database_name, schema_name, table_name, column_name;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        headers, rows = self.parse_csv_output(output)
        if not headers:
            return []

        index_map = {header: idx for idx, header in enumerate(headers)}
        entries = []
        for row in rows:
            entries.append(
                {
                    "database_name": row[index_map["database_name"]],
                    "schema_name": row[index_map["schema_name"]],
                    "table_name": row[index_map["table_name"]],
                    "column_name": row[index_map["column_name"]],
                    "standard_name": row[index_map["standard_name"]],
                    "linked_at": row[index_map["linked_at"]],
                    "linked_by": row[index_map["linked_by"]],
                }
            )
        return entries

    def _get_data_dictionary_usage_entry(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        column_name: str,
        cancel_event=None,
    ) -> dict | None:
        self._ensure_control_metadata(cancel_event=cancel_event)

        normalized_database = str(database_name or "").strip()
        normalized_schema = str(schema_name or "").strip()
        normalized_table = str(table_name or "").strip()
        normalized_column = str(column_name or "").strip()
        if not (normalized_database and normalized_schema and normalized_table and normalized_column):
            return None

        sql = f"""
SELECT
    database_name,
    schema_name,
    table_name,
    column_name,
    standard_name,
    COALESCE(linked_at::text, '') AS linked_at,
    COALESCE(linked_by, '') AS linked_by
FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
WHERE database_name = {sql_literal(normalized_database)}
  AND schema_name = {sql_literal(normalized_schema)}
  AND table_name = {sql_literal(normalized_table)}
  AND column_name = {sql_literal(normalized_column)}
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        headers, rows = self.parse_csv_output(output)
        if not headers or not rows:
            return None

        index_map = {header: idx for idx, header in enumerate(headers)}
        row = rows[0]
        return {
            "database_name": row[index_map["database_name"]],
            "schema_name": row[index_map["schema_name"]],
            "table_name": row[index_map["table_name"]],
            "column_name": row[index_map["column_name"]],
            "standard_name": row[index_map["standard_name"]],
            "linked_at": row[index_map["linked_at"]],
            "linked_by": row[index_map["linked_by"]],
        }

    def _get_data_dictionary_entry_by_name(self, standard_name: str, cancel_event=None) -> dict | None:
        self._ensure_control_metadata(cancel_event=cancel_event)
        normalized_name = str(standard_name or "").strip()
        if not normalized_name:
            return None

        sql = f"""
SELECT
    dictionary.standard_name,
    dictionary.definition,
    dictionary.units,
    dictionary.value_domain,
    COALESCE(dictionary.aliases, '') AS aliases,
    COALESCE(dictionary.data_type, '') AS data_type,
    (
        SELECT COUNT(*)
        FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE} AS usage
        WHERE usage.standard_name = dictionary.standard_name
    ) AS usage_count
FROM {CONTROL_TABLE_DATA_DICTIONARY} AS dictionary
WHERE lower(dictionary.standard_name) = lower({sql_literal(normalized_name)})
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        headers, rows = self.parse_csv_output(output)
        if not headers or not rows:
            return None

        index_map = {header: idx for idx, header in enumerate(headers)}
        row = rows[0]
        return {
            "standard_name": row[index_map["standard_name"]],
            "definition": row[index_map["definition"]],
            "units": row[index_map["units"]],
            "value_domain": row[index_map["value_domain"]],
            "aliases": row[index_map["aliases"]],
            "data_type": row[index_map["data_type"]],
            "usage_count": int(row[index_map["usage_count"]] or 0),
        }

    def _list_data_dictionary_usage_for_standard_name(self, standard_name: str, cancel_event=None) -> list[dict]:
        self._ensure_control_metadata(cancel_event=cancel_event)
        normalized_name = str(standard_name or "").strip()
        if not normalized_name:
            return []

        sql = f"""
SELECT
    database_name,
    schema_name,
    table_name,
    column_name,
    standard_name,
    COALESCE(linked_at::text, '') AS linked_at,
    COALESCE(linked_by, '') AS linked_by
FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
WHERE lower(standard_name) = lower({sql_literal(normalized_name)})
ORDER BY database_name, schema_name, table_name, column_name;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        headers, rows = self.parse_csv_output(output)
        if not headers:
            return []

        index_map = {header: idx for idx, header in enumerate(headers)}
        entries = []
        for row in rows:
            entries.append(
                {
                    "database_name": row[index_map["database_name"]],
                    "schema_name": row[index_map["schema_name"]],
                    "table_name": row[index_map["table_name"]],
                    "column_name": row[index_map["column_name"]],
                    "standard_name": row[index_map["standard_name"]],
                    "linked_at": row[index_map["linked_at"]],
                    "linked_by": row[index_map["linked_by"]],
                }
            )
        return entries

    def _delete_data_dictionary_usage_entries(self, usage_entries, cancel_event=None) -> int:
        entries = list(usage_entries or [])
        if not entries:
            return 0

        self._ensure_control_metadata(cancel_event=cancel_event)
        values_sql = ",\n".join(
            (
                f"({sql_literal(entry['database_name'])}, "
                f"{sql_literal(entry['schema_name'])}, "
                f"{sql_literal(entry['table_name'])}, "
                f"{sql_literal(entry['column_name'])})"
            )
            for entry in entries
        )
        sql = f"""
WITH targets (database_name, schema_name, table_name, column_name) AS (
    VALUES
    {values_sql}
),
deleted AS (
    DELETE FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE} AS usage
    USING targets
    WHERE usage.database_name = targets.database_name
      AND usage.schema_name = targets.schema_name
      AND usage.table_name = targets.table_name
      AND usage.column_name = targets.column_name
    RETURNING 1
)
SELECT COUNT(*) FROM deleted;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        return int(output or "0")

    def _collect_data_dictionary_entry_usage_state(self, standard_name: str, cancel_event=None) -> dict:
        entry = self._get_data_dictionary_entry_by_name(standard_name, cancel_event=cancel_event)
        if not entry:
            raise RuntimeError(f'Entrada "{standard_name}" nao encontrada no data dictionary.')

        usage_entries = self._list_data_dictionary_usage_for_standard_name(
            entry["standard_name"],
            cancel_event=cancel_event,
        )
        valid_entries = []
        stale_entries = []
        database_exists_cache = {}
        table_exists_cache = {}
        table_columns_cache = {}
        detected_types = []
        detected_type_keys = set()

        for usage_entry in usage_entries:
            self._raise_if_cancelled(cancel_event)

            database_name = str(usage_entry["database_name"] or "").strip()
            schema_name = str(usage_entry["schema_name"] or "").strip()
            table_name = str(usage_entry["table_name"] or "").strip()
            column_name = str(usage_entry["column_name"] or "").strip()
            full_table_name = f"{schema_name}.{table_name}"

            if database_name not in database_exists_cache:
                database_exists_cache[database_name] = self.database_exists(
                    database_name,
                    cancel_event=cancel_event,
                )
            if not database_exists_cache[database_name]:
                stale_entries.append(usage_entry)
                continue

            table_key = (database_name, full_table_name)
            if table_key not in table_exists_cache:
                table_exists_cache[table_key] = self.table_exists(
                    database_name,
                    full_table_name,
                    cancel_event=cancel_event,
                )
            if not table_exists_cache[table_key]:
                stale_entries.append(usage_entry)
                continue

            if table_key not in table_columns_cache:
                table_columns_cache[table_key] = {
                    str(item.get("name") or "").strip(): item
                    for item in self.get_table_column_definitions(
                        database_name,
                        full_table_name,
                        cancel_event=cancel_event,
                    )
                }

            column_definition = table_columns_cache[table_key].get(column_name)
            if not column_definition:
                stale_entries.append(usage_entry)
                continue

            actual_column_type = str(column_definition.get("type") or "").strip()
            actual_default_expression = str(column_definition.get("default") or "").strip()
            normalized_column_type = self._normalize_data_type_name(actual_column_type)
            supported_column_type = self._resolve_dictionary_usage_column_type(
                actual_column_type,
                actual_default_expression,
            ) or ""
            detected_label = supported_column_type or normalized_column_type or actual_column_type
            if detected_label and detected_label not in detected_type_keys:
                detected_type_keys.add(detected_label)
                detected_types.append(detected_label)

            valid_entries.append(
                {
                    **usage_entry,
                    "full_table_name": full_table_name,
                    "actual_column_type": actual_column_type,
                    "actual_default_expression": actual_default_expression,
                    "normalized_column_type": normalized_column_type,
                    "supported_column_type": supported_column_type,
                }
            )

        stored_data_type = self.resolve_supported_data_dictionary_type(entry.get("data_type")) or ""
        if not stored_data_type:
            inferred_types = {
                str(item.get("supported_column_type") or "").strip()
                for item in valid_entries
                if str(item.get("supported_column_type") or "").strip()
            }
            effective_data_type = next(iter(inferred_types)) if len(inferred_types) == 1 else ""
        else:
            effective_data_type = stored_data_type

        return {
            "entry": entry,
            "usage_entries": valid_entries,
            "stale_usage_entries": stale_entries,
            "detected_usage_types": detected_types,
            "stale_usage_count": len(stale_entries),
            "effective_data_type": effective_data_type,
        }

    @classmethod
    def _build_data_dictionary_cast_expression(cls, column_name: str, target_type: str) -> str:
        column_ident = sql_ident(column_name)
        normalized_target_type = cls._normalize_dictionary_data_type(target_type)
        if normalized_target_type == "varchar":
            return f"{column_ident}::text"
        if normalized_target_type == "jsonb":
            return f"to_jsonb({column_ident})"

        postgres_type = cls._resolve_data_dictionary_postgres_type(normalized_target_type)
        return f"({column_ident}::text)::{postgres_type}"

    @classmethod
    def _build_data_dictionary_target_column_name(cls, standard_name: str) -> str:
        normalized_name = slugify(str(standard_name or "").strip())
        normalized_name = normalized_name.replace("-", "_")
        normalized_name = re.sub(r"_+", "_", normalized_name).strip("_")
        if not normalized_name:
            normalized_name = "column_name"
        if not normalized_name[0].isalpha():
            normalized_name = f"col_{normalized_name}"
        normalized_name = normalized_name[:58].rstrip("_")
        if not normalized_name:
            normalized_name = "column_name"
        if normalized_name in cls.RAW_SYSTEM_COLUMNS:
            normalized_name = f"{normalized_name}_value"
            normalized_name = normalized_name[:58].rstrip("_") or "column_name"
        if not re.fullmatch(r"[a-z][a-z0-9_]*", normalized_name):
            raise RuntimeError(
                f"Nao foi possivel derivar um nome fisico de coluna valido a partir de {standard_name!r}."
            )
        return normalized_name

    def _count_data_dictionary_type_conversion_failures(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        target_type: str,
        cancel_event=None,
    ) -> int:
        normalized_target_type = self._normalize_dictionary_data_type(target_type)
        if normalized_target_type in {"varchar", "jsonb"}:
            return 0

        schema_name, table_name = split_table_name(full_table_name)
        postgres_type = self._resolve_data_dictionary_postgres_type(normalized_target_type)
        column_ident = sql_ident(column_name)
        sql = f"""
CREATE OR REPLACE FUNCTION pg_temp.pgdm_try_dictionary_cast(input_text text)
RETURNS {postgres_type}
LANGUAGE plpgsql
AS $function$
BEGIN
    IF input_text IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN input_text::{postgres_type};
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$function$;

SELECT COUNT(*)
FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
WHERE {column_ident} IS NOT NULL
  AND pg_temp.pgdm_try_dictionary_cast({column_ident}::text) IS NULL;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1 -At"
        )
        output = self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        output_lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
        return int(output_lines[-1] if output_lines else "0")

    def _alter_data_dictionary_linked_column_type(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        target_type: str,
        cancel_event=None,
    ):
        normalized_target_type = self._normalize_dictionary_data_type(target_type)
        schema_name, table_name = split_table_name(full_table_name)
        postgres_type = self._resolve_data_dictionary_postgres_type(normalized_target_type)
        cast_expression = self._build_data_dictionary_cast_expression(column_name, normalized_target_type)
        table_literal = sql_literal(f"{schema_name}.{table_name}")
        column_literal = sql_literal(column_name)

        if normalized_target_type in self.SERIAL_BASE_TYPE_MAP:
            sequence_name = self._build_data_dictionary_sequence_name(table_name, column_name)
            pre_alter_sql = ""
            post_alter_sql = f"""
DO $pgdm_dictionary_serial_apply$
DECLARE
    schema_name_text text := {sql_literal(schema_name)};
    table_name_text text := {sql_literal(table_name)};
    column_name_text text := {column_literal};
    target_sequence_name text := {sql_literal(sequence_name)};
    target_sequence_qualified text;
BEGIN
    target_sequence_qualified := pg_get_serial_sequence(
        format('%I.%I', schema_name_text, table_name_text),
        column_name_text
    );

    IF target_sequence_qualified IS NULL THEN
        EXECUTE format('CREATE SEQUENCE IF NOT EXISTS %I.%I', schema_name_text, target_sequence_name);
        target_sequence_qualified := format('%I.%I', schema_name_text, target_sequence_name);
    END IF;

    EXECUTE format(
        'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT nextval(%L::regclass)',
        schema_name_text,
        table_name_text,
        column_name_text,
        target_sequence_qualified
    );
    EXECUTE 'ALTER SEQUENCE ' || target_sequence_qualified || ' OWNED BY '
        || format('%I.%I.%I', schema_name_text, table_name_text, column_name_text);
    EXECUTE format(
        'SELECT setval(%L::regclass, COALESCE(MAX(%I), 0) + 1, false) FROM %I.%I',
        target_sequence_qualified,
        column_name_text,
        schema_name_text,
        table_name_text
    );
END
$pgdm_dictionary_serial_apply$;
""".strip()
        else:
            pre_alter_sql = f"""
DO $pgdm_dictionary_serial_cleanup$
DECLARE
    current_sequence text;
BEGIN
    current_sequence := pg_get_serial_sequence({table_literal}, {column_literal});
    IF current_sequence IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ALTER COLUMN %I DROP DEFAULT',
            {sql_literal(schema_name)},
            {sql_literal(table_name)},
            {column_literal}
        );
        EXECUTE 'ALTER SEQUENCE ' || current_sequence || ' OWNED BY NONE';
    END IF;
END
$pgdm_dictionary_serial_cleanup$;
""".strip()
            post_alter_sql = ""

        sql = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';

{pre_alter_sql}

ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)}
ALTER COLUMN {sql_ident(column_name)} TYPE {postgres_type}
USING {cast_expression};

{post_alter_sql}

COMMIT;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)

    def _rename_data_dictionary_linked_column(
        self,
        database_name: str,
        full_table_name: str,
        current_column_name: str,
        target_column_name: str,
        cancel_event=None,
    ):
        if current_column_name == target_column_name:
            return

        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';

ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)}
RENAME COLUMN {sql_ident(current_column_name)} TO {sql_ident(target_column_name)};

COMMIT;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)

    def get_data_dictionary_entry_edit_context(self, standard_name: str, cancel_event=None) -> dict:
        return self._collect_data_dictionary_entry_usage_state(standard_name, cancel_event=cancel_event)

    def update_data_dictionary_entry(
        self,
        existing_standard_name: str,
        dictionary_payload: dict,
        requested_by: str,
        cancel_event=None,
    ) -> dict:
        self._ensure_control_metadata(cancel_event=cancel_event)

        normalized_existing_name = str(existing_standard_name or "").strip()
        if not normalized_existing_name:
            raise RuntimeError("Standard name atual ausente para atualizar o data dictionary.")

        normalized_requested_by = str(requested_by or "").strip()
        if not normalized_requested_by:
            raise RuntimeError("Informe quem esta solicitando a atualizacao do data dictionary.")

        payload = dict(dictionary_payload or {})
        updated_standard_name = str(payload.get("standard_name") or "").strip()
        definition = str(payload.get("definition") or "").strip()
        units = str(payload.get("units") or "").strip()
        value_domain = str(payload.get("value_domain") or "").strip()
        aliases = self._normalize_data_dictionary_aliases(payload.get("aliases"))
        target_data_type = self._normalize_dictionary_data_type(payload.get("data_type"))

        if not updated_standard_name:
            raise RuntimeError("Standard name ausente para atualizar o data dictionary.")
        if not definition:
            raise RuntimeError("Definition ausente para atualizar o data dictionary.")
        if not units:
            raise RuntimeError("Units ausentes para atualizar o data dictionary.")
        if not value_domain:
            raise RuntimeError("Value domain ausente para atualizar o data dictionary.")

        state = self._collect_data_dictionary_entry_usage_state(
            normalized_existing_name,
            cancel_event=cancel_event,
        )
        current_entry = dict(state["entry"])
        valid_usage_entries = list(state["usage_entries"])
        stale_usage_entries = list(state["stale_usage_entries"])

        conflicting_entry = self._get_data_dictionary_entry_by_name(
            updated_standard_name,
            cancel_event=cancel_event,
        )
        if conflicting_entry and str(conflicting_entry.get("standard_name") or "").strip().lower() != normalized_existing_name.lower():
            raise RuntimeError(
                f'Ja existe outra entrada no data dictionary com o standard name "{updated_standard_name}".'
            )

        if stale_usage_entries:
            self._delete_data_dictionary_usage_entries(stale_usage_entries, cancel_event=cancel_event)

        effective_data_type = target_data_type or (self.resolve_supported_data_dictionary_type(current_entry.get("data_type")) or "")

        pending_type_changes = []
        for usage_entry in valid_usage_entries:
            current_supported_type = str(usage_entry.get("supported_column_type") or "").strip()
            if not effective_data_type or current_supported_type == effective_data_type:
                continue

            failed_values = self._count_data_dictionary_type_conversion_failures(
                usage_entry["database_name"],
                usage_entry["full_table_name"],
                usage_entry["column_name"],
                effective_data_type,
                cancel_event=cancel_event,
            )
            if failed_values:
                raise RuntimeError(
                    "Nao foi possivel converter automaticamente "
                    f'{usage_entry["database_name"]}.{usage_entry["full_table_name"]}.{usage_entry["column_name"]} '
                    f'para {effective_data_type}: {failed_values} valor(es) invalidos.'
                )
            pending_type_changes.append(usage_entry)

        for usage_entry in pending_type_changes:
            self._raise_if_cancelled(cancel_event)
            self._alter_data_dictionary_linked_column_type(
                usage_entry["database_name"],
                usage_entry["full_table_name"],
                usage_entry["column_name"],
                effective_data_type,
                cancel_event=cancel_event,
            )

        aliases_sql = sql_literal(aliases) if aliases else "NULL"
        data_type_sql = sql_literal(effective_data_type) if effective_data_type else "NULL"
        usage_linked_by_sql = sql_literal(normalized_requested_by)
        sql = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';

UPDATE {CONTROL_TABLE_DATA_DICTIONARY}
SET
    standard_name = {sql_literal(updated_standard_name)},
    definition = {sql_literal(definition)},
    units = {sql_literal(units)},
    value_domain = {sql_literal(value_domain)},
    aliases = {aliases_sql},
    data_type = {data_type_sql}
WHERE lower(standard_name) = lower({sql_literal(normalized_existing_name)});

UPDATE {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
SET
    linked_at = now(),
    linked_by = {usage_linked_by_sql}
WHERE lower(standard_name) = lower({sql_literal(updated_standard_name)});

COMMIT;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)

        affected_tables = []
        seen_tables = set()
        for usage_entry in valid_usage_entries:
            table_key = (usage_entry["database_name"], usage_entry["full_table_name"])
            if table_key in seen_tables:
                continue
            seen_tables.add(table_key)
            affected_tables.append(
                {
                    "database_name": usage_entry["database_name"],
                    "full_table_name": usage_entry["full_table_name"],
                }
            )

        return {
            "standard_name": updated_standard_name,
            "usage_count": len(valid_usage_entries),
            "altered_column_count": len(pending_type_changes),
            "affected_tables": affected_tables,
            "data_type": effective_data_type,
        }

    def update_data_dictionary_usage_entry(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        current_column_name: str,
        target_column_name: str,
        requested_by: str,
        cancel_event=None,
    ) -> dict:
        self._ensure_control_metadata(cancel_event=cancel_event)

        normalized_database_name = str(database_name or "").strip()
        normalized_schema_name = str(schema_name or "").strip()
        normalized_table_name = str(table_name or "").strip()
        normalized_current_column_name = str(current_column_name or "").strip()
        normalized_target_column_name = str(target_column_name or "").strip()
        normalized_requested_by = str(requested_by or "").strip()

        if not (normalized_database_name and normalized_schema_name and normalized_table_name):
            raise RuntimeError("Identificacao incompleta da entrada do data dictionary usage.")
        if not normalized_current_column_name:
            raise RuntimeError("Column name atual ausente para atualizar o data dictionary usage.")
        if not normalized_target_column_name:
            raise RuntimeError("Informe o novo column name.")
        if not normalized_requested_by:
            raise RuntimeError("Informe quem esta solicitando a atualizacao do data dictionary usage.")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", normalized_target_column_name):
            raise RuntimeError(
                "Column name invalido. Use apenas letras minusculas, numeros e underscore, "
                "iniciando por letra."
            )
        if len(normalized_target_column_name) > 58:
            raise RuntimeError(f"Column name longo demais: {normalized_target_column_name}.")

        usage_entry = self._get_data_dictionary_usage_entry(
            normalized_database_name,
            normalized_schema_name,
            normalized_table_name,
            normalized_current_column_name,
            cancel_event=cancel_event,
        )
        if not usage_entry:
            raise RuntimeError("Entrada do data dictionary usage nao encontrada.")

        full_table_name = f"{normalized_schema_name}.{normalized_table_name}"
        existing_columns = {
            str(item.get("name") or "").strip()
            for item in self.get_table_column_definitions(
                normalized_database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        }
        if normalized_current_column_name not in existing_columns:
            raise RuntimeError(
                f"A coluna {normalized_current_column_name} nao existe mais em "
                f"{normalized_database_name}.{full_table_name}."
            )
        if (
            normalized_target_column_name != normalized_current_column_name
            and normalized_target_column_name in existing_columns
        ):
            raise RuntimeError(
                f"A coluna {normalized_target_column_name} ja existe em "
                f"{normalized_database_name}.{full_table_name}."
            )

        if normalized_target_column_name != normalized_current_column_name:
            self._rename_data_dictionary_linked_column(
                normalized_database_name,
                full_table_name,
                normalized_current_column_name,
                normalized_target_column_name,
                cancel_event=cancel_event,
            )

        sql = f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30min';

UPDATE {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
SET
    column_name = {sql_literal(normalized_target_column_name)},
    linked_at = now(),
    linked_by = {sql_literal(normalized_requested_by)}
WHERE database_name = {sql_literal(normalized_database_name)}
  AND schema_name = {sql_literal(normalized_schema_name)}
  AND table_name = {sql_literal(normalized_table_name)}
  AND column_name = {sql_literal(normalized_current_column_name)};

COMMIT;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)

        return {
            "database_name": normalized_database_name,
            "schema_name": normalized_schema_name,
            "table_name": normalized_table_name,
            "full_table_name": full_table_name,
            "standard_name": usage_entry["standard_name"],
            "previous_column_name": normalized_current_column_name,
            "column_name": normalized_target_column_name,
        }

    def get_table_data_dictionary_links(
        self,
        database_name: str,
        full_table_name: str,
        cancel_event=None,
        trace_context: dict | None = None,
    ) -> dict[str, str]:
        started_at = time.perf_counter()
        self._trace_table_open(trace_context, f"get_table_data_dictionary_links(): iniciando para {full_table_name}")
        ensure_started_at = time.perf_counter()
        self._ensure_control_metadata(cancel_event=cancel_event, trace_context=trace_context)
        self._trace_table_open(
            trace_context,
            f"get_table_data_dictionary_links(): ensure_control_metadata em {(time.perf_counter() - ensure_started_at) * 1000:.1f} ms",
        )
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT
    column_name,
    standard_name
FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(
            command,
            cancel_event=cancel_event,
            trace_context=trace_context,
            trace_label=f"get_table_data_dictionary_links[{full_table_name}]",
        )
        parse_started_at = time.perf_counter()
        headers, rows = self.parse_csv_output(output)
        self._trace_table_open(
            trace_context,
            f"get_table_data_dictionary_links(): parse do CSV em {(time.perf_counter() - parse_started_at) * 1000:.1f} ms; rows={len(rows)}",
        )

        results = {}
        try:
            column_index = headers.index("column_name")
            standard_name_index = headers.index("standard_name")
        except ValueError:
            self._trace_table_open(
                trace_context,
                f"get_table_data_dictionary_links(): conclu?do em {(time.perf_counter() - started_at) * 1000:.1f} ms; links=0",
            )
            return results

        for row in rows:
            if len(row) <= max(column_index, standard_name_index):
                continue
            column_name = row[column_index]
            if not column_name:
                continue
            results[column_name] = row[standard_name_index]

        self._trace_table_open(
            trace_context,
            f"get_table_data_dictionary_links(): conclu?do em {(time.perf_counter() - started_at) * 1000:.1f} ms; links={len(results)}",
        )
        return results

    def remove_data_dictionary_usage_link(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        cancel_event=None,
    ) -> int:
        self._ensure_control_metadata(cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
WITH deleted AS (
    DELETE FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
    WHERE database_name = {sql_literal(database_name)}
      AND schema_name = {sql_literal(schema_name)}
      AND table_name = {sql_literal(table_name)}
      AND column_name = {sql_literal(column_name)}
    RETURNING 1
)
SELECT COUNT(*) FROM deleted;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        return int(output or "0")

    def delete_data_dictionary_entry(self, standard_name: str, cancel_event=None) -> dict:
        self._ensure_control_metadata(cancel_event=cancel_event)
        normalized_name = str(standard_name or "").strip()
        if not normalized_name:
            raise RuntimeError("Standard name ausente para exclusao do dicionario.")

        name_literal = sql_literal(normalized_name)
        usage_sql = f"""
SELECT COUNT(*)
FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE}
WHERE lower(standard_name) = lower({name_literal});
"""
        usage_command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(usage_sql)}"
        )
        usage_output = self.run_remote_command(usage_command, cancel_event=cancel_event).strip()
        usage_count = int(usage_output or "0")
        if usage_count:
            raise RuntimeError(
                f'A entrada "{normalized_name}" nao pode ser excluida porque ainda esta vinculada '
                f"a {usage_count} coluna(s) no data dictionary usage."
            )

        delete_sql = f"""
DELETE FROM {CONTROL_TABLE_DATA_DICTIONARY}
WHERE lower(standard_name) = lower({name_literal})
RETURNING standard_name;
"""
        delete_command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(delete_sql)}"
        )
        deleted_name = self.run_remote_command(delete_command, cancel_event=cancel_event).strip()
        if not deleted_name:
            raise RuntimeError(f'Entrada "{normalized_name}" nao encontrada no data dictionary.')

        return {
            "standard_name": deleted_name,
            "usage_count": 0,
        }

    def reconcile_data_dictionary_usage(self, cancel_event=None) -> dict:
        usage_entries = self.list_data_dictionary_usage_entries(cancel_event=cancel_event)
        total_rows = len(usage_entries)
        if not usage_entries:
            return {
                "total_rows": 0,
                "stale_rows": 0,
                "removed_rows": 0,
                "remaining_rows": 0,
            }

        stale_entries = []
        database_exists_cache = {}
        table_exists_cache = {}
        table_columns_cache = {}

        for usage_entry in usage_entries:
            self._raise_if_cancelled(cancel_event)

            database_name = str(usage_entry["database_name"] or "").strip()
            schema_name = str(usage_entry["schema_name"] or "").strip()
            table_name = str(usage_entry["table_name"] or "").strip()
            column_name = str(usage_entry["column_name"] or "").strip()
            full_table_name = f"{schema_name}.{table_name}"

            if database_name not in database_exists_cache:
                database_exists_cache[database_name] = self.database_exists(
                    database_name,
                    cancel_event=cancel_event,
                )
            if not database_exists_cache[database_name]:
                stale_entries.append(usage_entry)
                continue

            table_key = (database_name, full_table_name)
            if table_key not in table_exists_cache:
                table_exists_cache[table_key] = self.table_exists(
                    database_name,
                    full_table_name,
                    cancel_event=cancel_event,
                )
            if not table_exists_cache[table_key]:
                stale_entries.append(usage_entry)
                continue

            if table_key not in table_columns_cache:
                table_columns_cache[table_key] = {
                    str(item.get("name") or "").strip()
                    for item in self.get_table_column_definitions(
                        database_name,
                        full_table_name,
                        cancel_event=cancel_event,
                    )
                }
            if column_name not in table_columns_cache[table_key]:
                stale_entries.append(usage_entry)

        removed_rows = 0
        if stale_entries:
            values_sql = ",\n".join(
                (
                    f"({sql_literal(entry['database_name'])}, "
                    f"{sql_literal(entry['schema_name'])}, "
                    f"{sql_literal(entry['table_name'])}, "
                    f"{sql_literal(entry['column_name'])})"
                )
                for entry in stale_entries
            )
            delete_sql = f"""
WITH targets (database_name, schema_name, table_name, column_name) AS (
    VALUES
    {values_sql}
),
deleted AS (
    DELETE FROM {CONTROL_TABLE_DATA_DICTIONARY_USAGE} AS usage
    USING targets
    WHERE usage.database_name = targets.database_name
      AND usage.schema_name = targets.schema_name
      AND usage.table_name = targets.table_name
      AND usage.column_name = targets.column_name
    RETURNING 1
)
SELECT COUNT(*) FROM deleted;
"""
            delete_command = (
                f"psql -h localhost -U {shlex.quote(self.sql_username)} "
                f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(delete_sql)}"
            )
            delete_output = self.run_remote_command(delete_command, cancel_event=cancel_event).strip()
            removed_rows = int(delete_output or "0")

        return {
            "total_rows": total_rows,
            "stale_rows": len(stale_entries),
            "removed_rows": removed_rows,
            "remaining_rows": max(total_rows - removed_rows, 0),
        }

    def sync_data_dictionary_entries(
        self,
        database_name: str,
        full_table_name: str,
        columns,
        requested_by: str | None = None,
        cancel_event=None,
    ):
        normalized_columns = []
        for position, column_payload in enumerate(columns or [], start=1):
            if not (column_payload or {}).get("dictionary"):
                continue
            normalized_columns.append(
                self._normalize_data_dictionary_payload(column_payload, position)
            )

        if not normalized_columns:
            return []

        self._ensure_control_metadata(cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        linked_by_sql = "NULL" if requested_by is None else sql_literal(str(requested_by))

        sql_lines = [
            "BEGIN;",
            "SET LOCAL lock_timeout = '10s';",
            "SET LOCAL statement_timeout = '30min';",
        ]
        for column_payload in normalized_columns:
            resolved_standard_name_literal = sql_literal(column_payload["resolved_standard_name"])
            definition_literal = sql_literal(column_payload["definition"])
            units_literal = sql_literal(column_payload["units"])
            value_domain_literal = sql_literal(column_payload["value_domain"])
            data_type_sql = (
                sql_literal(column_payload["data_type"])
                if column_payload["data_type"]
                else "NULL"
            )
            aliases_sql = (
                sql_literal(column_payload["aliases"])
                if column_payload["aliases"]
                else "NULL"
            )
            sql_lines.extend(
                [
                    f"""INSERT INTO {CONTROL_TABLE_DATA_DICTIONARY} (
    standard_name,
    definition,
    units,
    value_domain,
    aliases,
    data_type
)
SELECT
    {resolved_standard_name_literal},
    {definition_literal},
    {units_literal},
    {value_domain_literal},
    {aliases_sql},
    {data_type_sql}
WHERE NOT EXISTS (
    SELECT 1
    FROM {CONTROL_TABLE_DATA_DICTIONARY}
    WHERE lower(standard_name) = lower({resolved_standard_name_literal})
);""",
                    f"""UPDATE {CONTROL_TABLE_DATA_DICTIONARY}
SET data_type = COALESCE(data_type, {data_type_sql})
WHERE lower(standard_name) = lower({resolved_standard_name_literal});""",
                    f"""INSERT INTO {CONTROL_TABLE_DATA_DICTIONARY_USAGE} (
    database_name,
    schema_name,
    table_name,
    column_name,
    standard_name,
    linked_by
)
SELECT
    {sql_literal(database_name)},
    {sql_literal(schema_name)},
    {sql_literal(table_name)},
    {sql_literal(column_payload["column_name"])},
    COALESCE(
        (
            SELECT standard_name
            FROM {CONTROL_TABLE_DATA_DICTIONARY}
            WHERE lower(standard_name) = lower({resolved_standard_name_literal})
            LIMIT 1
        ),
        {resolved_standard_name_literal}
    ),
    {linked_by_sql}
ON CONFLICT (database_name, schema_name, table_name, column_name)
DO UPDATE SET
    standard_name = EXCLUDED.standard_name,
    linked_at = now(),
    linked_by = EXCLUDED.linked_by;""",
                ]
            )
        sql_lines.append("COMMIT;")
        sql = "\n".join(sql_lines) + "\n"

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        return normalized_columns

    def _list_existing_archived_table_dirs(
        self,
        database_name: str,
        schema_name: str,
        cancel_event=None,
    ) -> set[str]:
        parent_dirs = [
            f"{self.remote_storage_root}/raw_versions/{slugify(database_name)}/{slugify(schema_name)}",
            f"{self.remote_storage_root}/deletions/table/{slugify(database_name)}/{slugify(schema_name)}",
        ]
        command = "\n".join(
            [
                "(",
                *[
                    (
                        f"if [ -d {shlex.quote(parent_dir)} ]; then "
                        f"find {shlex.quote(parent_dir)} -mindepth 1 -maxdepth 1 -type d; "
                        "fi"
                    )
                    for parent_dir in parent_dirs
                ],
                ")",
            ]
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        return {
            line.strip().rstrip("/").split("/")[-1]
            for line in output.splitlines()
            if line.strip()
        }

    def normalize_archived_raw_dump_paths(self, cancel_event=None):
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT DISTINCT
    database_name,
    schema_name,
    table_name,
    raw_dump_path
FROM {CONTROL_TABLE_VERSIONS}
WHERE table_name ~ '__deleted_[0-9]+$'
  AND raw_dump_path IS NOT NULL
  AND raw_dump_path <> ''
ORDER BY database_name, schema_name, table_name, raw_dump_path;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)

        suffix_pattern = re.compile(r"__deleted_\d+$")
        for row in rows:
            database_name = row[0]
            schema_name = row[1]
            archived_table_name = row[2]
            raw_dump_path = row[3]
            original_table_name = suffix_pattern.sub("", archived_table_name)

            source_base_dir = self._build_raw_dump_base_dir(database_name, schema_name, original_table_name)
            target_base_dir = self._build_raw_dump_base_dir(database_name, schema_name, archived_table_name)
            if not raw_dump_path.startswith(source_base_dir + "/"):
                continue

            target_dump_path = target_base_dir + raw_dump_path[len(source_base_dir):]
            target_parent_dir = target_dump_path.rsplit("/", 1)[0]
            move_command = (
                f"if [ -f {shlex.quote(raw_dump_path)} ]; then "
                f"mkdir -p {shlex.quote(target_parent_dir)} && "
                f"if [ ! -e {shlex.quote(target_dump_path)} ]; then "
                f"mv {shlex.quote(raw_dump_path)} {shlex.quote(target_dump_path)}; "
                "fi; "
                "fi"
            )
            self.run_remote_command(move_command, cancel_event=cancel_event)

            update_sql = f"""
UPDATE {CONTROL_TABLE_VERSIONS}
SET raw_dump_path = {sql_literal(target_dump_path)}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(archived_table_name)}
  AND raw_dump_path = {sql_literal(raw_dump_path)};
"""
            update_command = (
                f"psql -h localhost -U {shlex.quote(self.sql_username)} "
                f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(update_sql)}"
            )
            self.run_remote_command(update_command, cancel_event=cancel_event)

    @staticmethod
    def _build_raw_json_payload(record: dict) -> dict:
        return {
            "values": record.get("values", []),
        }

    @staticmethod
    def _get_raw_record_tab(record: dict):
        return record.get("raw_tab") or record.get("sheet_name")

    @staticmethod
    def _get_raw_record_id(record: dict):
        return record.get("raw_id") or record.get("source_row_number")

    def _build_raw_copy_script_stream(
        self,
        schema_name: str,
        table_name: str,
        file_hash: str,
        ingested_at: str,
        raw_schema: str | None,
        records,
        system_columns=None,
        raw_id_start: int | None = None,
        row_count_delta: int = 0,
        progress_callback=None,
        cancel_event=None,
    ):
        system_columns = list(system_columns or self.RAW_COLUMN_ORDER)
        spool = tempfile.SpooledTemporaryFile(
            max_size=self.RAW_IMPORT_SPOOL_MAX_MEMORY_BYTES,
            mode="w+b",
        )
        text_stream = io.TextIOWrapper(spool, encoding="utf-8", newline="")
        row_buffer = io.StringIO(newline="")
        writer = csv.writer(row_buffer, lineterminator="\n")
        total_records = len(records)
        report_step = max(total_records // 20, 1) if total_records else 1
        payload_size_bytes = 0

        text_stream.write("BEGIN;\n")
        if total_records:
            copy_column_sql = ", ".join(sql_ident(column) for column in system_columns)
            text_stream.write(
                f"\\copy {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"({copy_column_sql}) "
                "FROM STDIN WITH (FORMAT csv)\n"
            )

        for index, record in enumerate(records, start=1):
            self._raise_if_cancelled(cancel_event)
            row = []
            record_hash = record.get("raw_hash", file_hash)
            record_ingested_at = record.get("raw_ingested_at", ingested_at)
            record_schema = record.get("raw_schema", raw_schema)
            record_raw_id = raw_id_start + index - 1 if raw_id_start is not None else self._get_raw_record_id(record)
            for column_name in system_columns:
                if column_name == "raw_hash":
                    row.append(record_hash)
                elif column_name == "raw_ingested_at":
                    row.append(record_ingested_at)
                elif column_name == "raw_schema":
                    row.append(record_schema)
                elif column_name == "raw_tab":
                    row.append(self._get_raw_record_tab(record))
                elif column_name == "raw_id":
                    row.append(record_raw_id)
                elif column_name == "raw":
                    row.append(json.dumps(self._build_raw_json_payload(record), ensure_ascii=False))
            row_buffer.seek(0)
            row_buffer.truncate(0)
            writer.writerow(row)
            row_text = row_buffer.getvalue()
            text_stream.write(row_text)
            payload_size_bytes += len(row_text.encode("utf-8"))

            if progress_callback and (index == total_records or index % report_step == 0):
                progress_callback(
                    "payload",
                    100 * index / max(total_records, 1),
                    f"Montando carga de importacao: registro {index} de {total_records}.",
                )

        if total_records:
            text_stream.write("\\.\n")
        if row_count_delta:
            text_stream.write(
                self._build_table_row_count_delta_sql(
                    schema_name,
                    table_name,
                    row_count_delta,
                )
                + "\n"
            )
        text_stream.write("COMMIT;\n")
        text_stream.flush()
        text_stream.detach()
        spool.seek(0)
        return spool, payload_size_bytes

    def build_raw_version_recipe_from_source(
        self,
        database_name: str,
        full_table_name: str,
        raw_source: dict,
        progress_callback=None,
        cancel_event=None,
    ):
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "version", 35, "Montando receita SQL leve da nova versao...")
        recipe = self.build_raw_load_recipe(full_table_name, raw_source=raw_source)
        self._notify_progress(progress_callback, "version", 55, "Receita SQL leve preparada.")
        return recipe

    def create_raw_snapshot_dump(
        self,
        database_name: str,
        full_table_name: str,
        raw_source: dict,
        cancel_event=None,
        progress_callback=None,
        version_code: str | None = None,
    ):
        self._raise_if_cancelled(cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        version_label = self._build_raw_dump_version_label(version_code)
        base_dir, dump_path = self._build_raw_dump_paths(
            database_name,
            full_table_name,
            raw_source,
            version_code=version_code,
        )
        system_columns = self._get_raw_source_system_columns(raw_source)
        column_sql = ", ".join(
            f"{sql_ident(column)}::text" if column == "raw" else sql_ident(column)
            for column in system_columns
        )
        where_sql = self._build_raw_dump_where_sql(raw_source, system_columns)
        select_sql = f"SELECT {column_sql} FROM {sql_ident(schema_name)}.{sql_ident(table_name)}"
        if where_sql:
            select_sql = f"{select_sql} {where_sql}"

        # New Raw versions store only the rows introduced by that version.
        # Older histories may still point to full-table dumps; replay remains
        # compatible because restore merges rows instead of replacing them.
        copy_sql = f"\\copy ({select_sql}) TO STDOUT WITH (FORMAT csv)"
        command = (
            f"mkdir -p {shlex.quote(base_dir)} && "
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -c {shlex.quote(copy_sql)} "
            f"| gzip -c > {shlex.quote(dump_path)}"
        )
        self._notify_progress(
            progress_callback,
            "version",
            0,
            f"Gerando dump Raw delta da versao {version_label}...",
        )
        self.run_remote_command(command, cancel_event=cancel_event)
        self._notify_progress(
            progress_callback,
            "version",
            30,
            f"Dump Raw da versao {version_label} gerado em {dump_path}.",
        )
        return dump_path

    def restore_raw_snapshot_dump(
        self,
        database_name: str,
        full_table_name: str,
        dump_path: str,
        cancel_event=None,
        merge_existing: bool = False,
    ):
        self._raise_if_cancelled(cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        prepare_sql = "\n".join([
            f"CREATE SCHEMA IF NOT EXISTS {sql_ident(schema_name)};",
            f"CREATE TABLE IF NOT EXISTS {sql_ident(schema_name)}.{sql_ident(table_name)} ();",
            f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} ADD COLUMN IF NOT EXISTS {sql_ident('raw_hash')} text;",
            (
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_ingested_at')} timestamptz;"
            ),
            (
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_schema')} text;"
            ),
            f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} ADD COLUMN IF NOT EXISTS {sql_ident('raw')} jsonb;",
        ]) + "\n"
        prepare_command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X"
        )
        self.run_remote_command(prepare_command, stdin_text=prepare_sql, cancel_event=cancel_event)
        self.get_table_row_count(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )

        self._restore_raw_snapshot_dump_from_program(
            database_name,
            schema_name,
            table_name,
            dump_path,
            [
                ["raw_hash", "raw_ingested_at", "raw_schema", "raw_tab", "raw_id", "raw"],
                ["raw_hash", "raw_ingested_at", "raw_tab", "raw_id", "raw"],
                ["raw_hash", "raw_ingested_at", "raw_schema", "raw_id", "raw"],
                ["raw_hash", "raw_ingested_at", "raw_id", "raw"],
                ["raw_hash", "raw_ingested_at", "raw_schema", "raw"],
                ["raw_hash", "raw_ingested_at", "raw"],
            ],
            merge_existing=merge_existing,
            cancel_event=cancel_event,
        )

    def _restore_raw_snapshot_dump_from_program(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        dump_path: str,
        copy_column_candidates,
        merge_existing: bool = False,
        cancel_event=None,
    ):
        if not copy_column_candidates:
            raise RuntimeError("Nao foi possivel determinar o formato do dump Raw para restauracao.")

        copy_columns = list(copy_column_candidates[0])
        stage_table = "pgdm_raw_restore_stage"
        copy_column_sql = ", ".join(sql_ident(column) for column in copy_columns)
        program = f"gunzip -c {shlex.quote(dump_path)}"
        insert_sql = self._build_raw_snapshot_restore_insert_sql(
            schema_name,
            table_name,
            stage_table,
            copy_columns,
            merge_existing=merge_existing,
        )
        optional_target_columns_sql = []
        if "raw_schema" in copy_columns:
            optional_target_columns_sql.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_schema')} text;"
            )
        if "raw_tab" in copy_columns:
            optional_target_columns_sql.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_tab')} text;"
            )
        if "raw_id" in copy_columns:
            optional_target_columns_sql.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_id')} bigint;"
            )
        optional_target_sql = "\n".join(optional_target_columns_sql)
        script = f"""
BEGIN;
CREATE TEMP TABLE {sql_ident(stage_table)} (
    {sql_ident('raw_hash')} text,
    {sql_ident('raw_ingested_at')} timestamptz,
    {sql_ident('raw_schema')} text,
    {sql_ident('raw_tab')} text,
    {sql_ident('raw_id')} bigint,
    {sql_ident('raw')} jsonb
);
\\copy {sql_ident(stage_table)} ({copy_column_sql}) FROM PROGRAM {sql_literal(program)} WITH (FORMAT csv)
{optional_target_sql}
{insert_sql}
COMMIT;
""".lstrip()
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        try:
            self.run_remote_command(command, stdin_text=script, cancel_event=cancel_event)
        except RuntimeError as exc:
            message = str(exc).lower()
            if (
                "missing data for column" not in message
                and "extra data after last expected column" not in message
            ) or len(copy_column_candidates) == 1:
                raise

            self._restore_raw_snapshot_dump_from_program(
                database_name,
                schema_name,
                table_name,
                dump_path,
                copy_column_candidates[1:],
                merge_existing=merge_existing,
                cancel_event=cancel_event,
            )

    def _build_raw_snapshot_restore_insert_sql(
        self,
        schema_name: str,
        table_name: str,
        stage_table: str,
        target_columns,
        merge_existing: bool = False,
    ) -> str:
        target_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        column_sql = ", ".join(sql_ident(column) for column in target_columns)
        select_sql = ", ".join(f"source.{sql_ident(column)}" for column in target_columns)
        insert_sql = f"""
INSERT INTO {target_table} ({column_sql})
SELECT {select_sql}
FROM {sql_ident(stage_table)} AS source
""".rstrip()
        if merge_existing:
            merge_keys = [
                column
                for column in ("raw_hash", "raw_ingested_at", "raw_schema", "raw_tab", "raw_id")
                if column in target_columns
            ]
            if not merge_keys:
                merge_keys = [column for column in target_columns if column != "raw"]
            insert_sql += f"""
WHERE NOT EXISTS (
    SELECT 1
    FROM {target_table} AS existing
    WHERE {" AND ".join(
        f"existing.{sql_ident(column)} IS NOT DISTINCT FROM source.{sql_ident(column)}"
        for column in merge_keys
    )}
)
"""
        inserted_count_table = "pgdm_raw_restore_insert_count"
        counter_sql = self._build_table_row_count_delta_expression_sql(
            schema_name,
            table_name,
            f"SELECT row_count FROM {sql_ident(inserted_count_table)}",
            delta_description="linhas restauradas",
        )
        return f"""
CREATE TEMP TABLE {sql_ident(inserted_count_table)} ON COMMIT DROP AS
WITH inserted_rows AS (
{insert_sql.rstrip()}
    RETURNING 1
)
SELECT COUNT(*)::bigint AS row_count
FROM inserted_rows;

{counter_sql}
""".strip()

    def _get_table_versions_for_replay(self, database_name: str, schema_name: str, table_name: str, cancel_event=None):
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT
    versions.id::text,
    version_code,
    sql_recipe,
    COALESCE(restored_from_version, ''),
    COALESCE(raw_dump_path, ''),
    COALESCE(raw_schema, ''),
    COALESCE(operation_kind, 'standard'),
    COALESCE((
        SELECT json_agg(
            json_build_object(
                'dependency_kind', dependency.dependency_kind,
                'source_database_name', dependency.source_database_name,
                'source_schema_name', dependency.source_schema_name,
                'source_table_name', dependency.source_table_name,
                'dependency_payload', dependency.dependency_payload
            )
            ORDER BY dependency.id
        )
        FROM {CONTROL_TABLE_VERSION_DEPENDENCIES} AS dependency
        WHERE dependency.table_version_id = versions.id
    ), '[]'::json)::text
FROM {CONTROL_TABLE_VERSIONS} AS versions
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
ORDER BY version_code ASC;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        return [
            {
                "version_id": int(row[0]),
                "version_code": row[1],
                "sql_recipe": row[2],
                "restored_from_version": row[3],
                "raw_dump_path": row[4],
                "raw_schema": row[5] or None,
                "operation_kind": row[6] or "standard",
                "dependencies": json.loads(row[7] or "[]"),
            }
            for row in rows
        ]

    def _build_replay_plan(self, versions, target_version_code: str):
        memo = {}
        available_codes = {version["version_code"] for version in versions}
        if target_version_code not in available_codes:
            raise RuntimeError(f"Versao {target_version_code} nao encontrada para replay.")

        def flatten(target_code: str, stack=None):
            if target_code in memo:
                return [dict(item) for item in memo[target_code]]

            if stack is None:
                stack = set()
            if target_code in stack:
                raise RuntimeError("Ciclo detectado no historico de restauracoes.")

            stack.add(target_code)
            result = []
            for version in versions:
                if version["restored_from_version"]:
                    result = flatten(version["restored_from_version"], stack)
                else:
                    result.append(dict(version))

                if version["version_code"] == target_code:
                    memo[target_code] = [dict(item) for item in result]
                    stack.remove(target_code)
                    return [dict(item) for item in result]

            stack.remove(target_code)
            raise RuntimeError(f"Versao {target_code} nao encontrada para replay.")

        return flatten(target_version_code)

    @staticmethod
    def _canonical_raw_hash_manifest(items) -> list[dict]:
        normalized = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            raw_schema = str(item.get("raw_schema") or "").strip()
            raw_hash = item.get("raw_hash")
            if raw_hash is not None:
                raw_hash = str(raw_hash)
            try:
                row_count = int(item.get("row_count") or 0)
            except (TypeError, ValueError):
                row_count = -1
            normalized.append(
                {
                    "raw_schema": raw_schema,
                    "raw_hash": raw_hash,
                    "row_count": row_count,
                }
            )
        normalized.sort(
            key=lambda item: (
                item["raw_schema"],
                item["raw_hash"] is not None,
                item["raw_hash"] or "",
            )
        )
        return normalized

    @staticmethod
    def _canonical_source_version_references(items) -> list[dict]:
        normalized = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            version_code = str(item.get("version_code") or "").strip()
            raw_schema = str(item.get("raw_schema") or "").strip()
            if not version_code or not raw_schema:
                continue
            normalized.append(
                {
                    "version_code": version_code,
                    "raw_schema": raw_schema,
                    "raw_dump_path": str(
                        item.get("raw_dump_path") or ""
                    ).strip(),
                    "raw_hash": str(item.get("raw_hash") or "").strip(),
                }
            )

        def version_sort_key(item):
            version_code = item["version_code"]
            return (
                0 if version_code.isdigit() else 1,
                int(version_code) if version_code.isdigit() else version_code,
                item["raw_schema"],
                item["raw_dump_path"],
                item["raw_hash"],
            )

        normalized.sort(key=version_sort_key)
        return normalized

    def _capture_expansion_source_manifest(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        raw_schemas,
        cancel_event=None,
    ) -> dict:
        normalized_raw_schemas = self._dedupe_non_empty_strings(raw_schemas)
        if not normalized_raw_schemas:
            raise RuntimeError("O preflight recebeu um escopo raw_schema vazio.")
        scope_sql = ", ".join(
            sql_literal(value)
            for value in normalized_raw_schemas
        )
        source_table_sql = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        sql = f"""
WITH raw_hash_manifest AS MATERIALIZED (
    SELECT
        {sql_ident('raw_schema')}::text AS raw_schema,
        {sql_ident('raw_hash')}::text AS raw_hash,
        COUNT(*)::bigint AS row_count
    FROM {source_table_sql}
    WHERE {sql_ident('raw_schema')} IN ({scope_sql})
    GROUP BY {sql_ident('raw_schema')}, {sql_ident('raw_hash')}
)
SELECT json_build_object(
    'raw_schemas', COALESCE((
        SELECT json_agg(DISTINCT raw_schema ORDER BY raw_schema)
        FROM raw_hash_manifest
    ), '[]'::json),
    'expected_row_count', COALESCE((
        SELECT SUM(row_count)
        FROM raw_hash_manifest
    ), 0),
    'expected_distinct_raw_hash_count', (
        SELECT COUNT(*)
        FROM (
            SELECT DISTINCT raw_hash
            FROM raw_hash_manifest
        ) AS distinct_hashes
    ),
    'raw_hash_manifest', COALESCE((
        SELECT json_agg(
            json_build_object(
                'raw_schema', raw_schema,
                'raw_hash', raw_hash,
                'row_count', row_count
            )
            ORDER BY raw_schema, raw_hash NULLS FIRST
        )
        FROM raw_hash_manifest
    ), '[]'::json),
    'source_columns', COALESCE((
        SELECT json_agg(
            json_build_object(
                'name', attribute.attname,
                'type', pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)
            )
            ORDER BY attribute.attnum
        )
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation
          ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = {sql_literal(schema_name)}
          AND relation.relname = {sql_literal(table_name)}
          AND relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
    ), '[]'::json)
)::text;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -qAt -v ON_ERROR_STOP=1 "
            f"-c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(
            command,
            cancel_event=cancel_event,
        ).strip()
        if not output:
            raise RuntimeError(
                f"A origem {schema_name}.{table_name} nao retornou manifesto."
            )
        try:
            manifest = json.loads(output.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"O manifesto atual de {schema_name}.{table_name} e invalido."
            ) from exc
        if not isinstance(manifest, dict):
            raise RuntimeError(
                f"O manifesto atual de {schema_name}.{table_name} tem formato inesperado."
            )
        return manifest

    @staticmethod
    def _manifest_subset(manifest: dict, raw_schemas) -> dict:
        scope = {
            str(value).strip()
            for value in raw_schemas or []
            if str(value).strip()
        }
        items = [
            item
            for item in PostgresAdminService._canonical_raw_hash_manifest(
                (manifest or {}).get("raw_hash_manifest")
            )
            if item["raw_schema"] in scope
        ]
        present_schemas = sorted(
            {
                item["raw_schema"]
                for item in items
                if item["raw_schema"]
            }
        )
        distinct_hashes = {
            item["raw_hash"]
            for item in items
        }
        return {
            "raw_schemas": present_schemas,
            "expected_row_count": sum(item["row_count"] for item in items),
            "expected_distinct_raw_hash_count": len(distinct_hashes),
            "raw_hash_manifest": items,
            "source_columns": list((manifest or {}).get("source_columns") or []),
        }

    def _preflight_replay_raw_dump_paths(
        self,
        replay_plan,
        extra_dump_paths=None,
        cancel_event=None,
    ) -> list[str]:
        dump_paths = self._dedupe_non_empty_strings(
            [
                version.get("raw_dump_path")
                for version in replay_plan or []
            ]
            + list(extra_dump_paths or [])
        )
        if not dump_paths:
            return []
        commands = [
            (
                f"if [ ! -f {shlex.quote(path)} ]; then "
                f"printf '%s\\n' {shlex.quote(path)}; fi"
            )
            for path in dump_paths
        ]
        output = self.run_remote_command(
            "\n".join(commands),
            cancel_event=cancel_event,
        )
        return [
            line.strip()
            for line in output.splitlines()
            if line.strip()
        ]

    def _preflight_replay_dependencies(
        self,
        database_name: str,
        target_full_table_name: str,
        replay_plan,
        recipe_transform=None,
        cancel_event=None,
        progress_callback=None,
    ):
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            72,
            "Analisando receitas e manifestos antes de alterar qualquer tabela...",
        )
        target_schema_name, target_table_name = split_table_name(
            target_full_table_name
        )
        target_relations = {
            (target_schema_name, target_table_name),
            (
                target_schema_name,
                self.resolve_original_table_name(target_table_name),
            ),
        }
        problems = []
        dependency_entries = []

        for version in replay_plan or []:
            version_code = str(version.get("version_code") or "?")
            operation_kind = str(
                version.get("operation_kind") or "standard"
            ).strip()
            dependencies = list(version.get("dependencies") or [])
            if (
                operation_kind == self.CROSS_TABLE_EXPANSION_OPERATION_KIND
                and not dependencies
            ):
                problems.append(
                    f"versao {version_code}: expansao sem manifesto de dependencia"
                )

            normalized_dependencies = []
            for dependency in dependencies:
                try:
                    normalized_dependency = self._normalize_replay_dependency(
                        dependency
                    )
                except Exception as exc:
                    problems.append(
                        f"versao {version_code}: dependencia invalida ({exc})"
                    )
                    continue
                if (
                    normalized_dependency["dependency_kind"]
                    != self.CROSS_TABLE_EXPANSION_DEPENDENCY_KIND
                ):
                    problems.append(
                        f"versao {version_code}: tipo de dependencia nao suportado "
                        f"({normalized_dependency['dependency_kind']})"
                    )
                    continue
                payload = normalized_dependency["dependency_payload"]
                raw_schemas = self._dedupe_non_empty_strings(
                    payload.get("raw_schemas")
                )
                if not raw_schemas:
                    problems.append(
                        f"versao {version_code}: manifesto sem raw_schema"
                    )
                    continue
                normalized_dependency["raw_schemas"] = raw_schemas
                normalized_dependency["version_code"] = version_code
                normalized_dependencies.append(normalized_dependency)
                dependency_entries.append(normalized_dependency)

            recipe = str(version.get("sql_recipe") or "").strip()
            if not recipe:
                continue
            if recipe_transform:
                try:
                    recipe = recipe_transform(recipe)
                except Exception as exc:
                    problems.append(
                        f"versao {version_code}: nao foi possivel adaptar a receita "
                        f"ao destino ({exc})"
                    )
                    continue
            recipe_relations = set(
                self._collect_sql_relation_references(
                    recipe,
                    default_schema=target_schema_name,
                )
            )
            declared_sources = {
                (
                    dependency["source_schema_name"],
                    dependency["source_table_name"],
                )
                for dependency in normalized_dependencies
                if dependency["source_database_name"] == database_name
            }
            declared_destination_reads = set()
            for dependency in normalized_dependencies:
                payload = dependency.get("dependency_payload") or {}
                for relation in payload.get("destination_read_relations") or []:
                    if not isinstance(relation, dict):
                        problems.append(
                            f"versao {version_code}: leitura de destino declarada "
                            "em formato invalido"
                        )
                        continue
                    schema_name = str(
                        relation.get("schema_name") or ""
                    ).strip()
                    table_name = str(
                        relation.get("table_name") or ""
                    ).strip()
                    if not schema_name or not table_name:
                        problems.append(
                            f"versao {version_code}: leitura de destino declarada "
                            "sem schema ou tabela"
                        )
                        continue
                    declared_destination_reads.add(
                        (schema_name, table_name)
                    )
            unexpected_relations = sorted(
                recipe_relations
                - target_relations
                - declared_sources
                - declared_destination_reads
            )
            if unexpected_relations:
                formatted_relations = ", ".join(
                    f"{schema_name}.{table_name}"
                    for schema_name, table_name in unexpected_relations
                )
                if normalized_dependencies:
                    problems.append(
                        f"versao {version_code}: receita consulta relacao nao declarada "
                        f"({formatted_relations})"
                    )
                else:
                    problems.append(
                        f"versao {version_code}: receita cross-table legada sem "
                        f"manifesto ({formatted_relations})"
                    )

        dependency_dump_paths = self._dedupe_non_empty_strings(
            reference.get("raw_dump_path")
            for dependency in dependency_entries
            for reference in (
                dependency["dependency_payload"].get("source_versions") or []
            )
            if isinstance(reference, dict)
        )
        missing_dump_paths = self._preflight_replay_raw_dump_paths(
            replay_plan,
            extra_dump_paths=dependency_dump_paths,
            cancel_event=cancel_event,
        )
        for dump_path in missing_dump_paths:
            problems.append(f"dump de replay ausente: {dump_path}")

        grouped_dependencies = {}
        for dependency in dependency_entries:
            source_key = (
                dependency["source_database_name"],
                dependency["source_schema_name"],
                dependency["source_table_name"],
            )
            grouped_dependencies.setdefault(source_key, []).append(dependency)

        total_sources = max(len(grouped_dependencies), 1)
        for source_index, (source_key, source_dependencies) in enumerate(
            grouped_dependencies.items(),
            start=1,
        ):
            self._raise_if_cancelled(cancel_event)
            source_database_name, source_schema_name, source_table_name = source_key
            source_label = (
                f"{source_database_name}."
                f"{source_schema_name}.{source_table_name}"
            )
            if source_database_name != database_name:
                problems.append(
                    f"origem externa {source_label}: replay entre bancos ainda nao e suportado"
                )
                continue

            union_raw_schemas = self._dedupe_non_empty_strings(
                raw_schema
                for dependency in source_dependencies
                for raw_schema in dependency["raw_schemas"]
            )
            progress = 74 + (20 * (source_index - 1) / total_sources)
            self._notify_progress(
                progress_callback,
                "prepare",
                progress,
                f"Conferindo registros e hashes de {source_label}...",
            )
            try:
                actual_manifest = self._capture_expansion_source_manifest(
                    source_database_name,
                    source_schema_name,
                    source_table_name,
                    union_raw_schemas,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                problems.append(
                    f"origem {source_label}: nao foi possivel validar ({exc})"
                )
                continue
            try:
                actual_source_versions = (
                    self.get_raw_schema_version_references(
                        source_database_name,
                        f"{source_schema_name}.{source_table_name}",
                        union_raw_schemas,
                        cancel_event=cancel_event,
                    )
                )
            except Exception as exc:
                problems.append(
                    f"origem {source_label}: nao foi possivel validar as versoes "
                    f"de origem ({exc})"
                )
                actual_source_versions = None

            current_columns = {
                str(item.get("name") or ""): str(item.get("type") or "")
                for item in actual_manifest.get("source_columns") or []
                if isinstance(item, dict) and item.get("name")
            }
            for dependency in source_dependencies:
                version_code = dependency["version_code"]
                payload = dependency["dependency_payload"]
                raw_schemas = dependency["raw_schemas"]
                expected_source_versions = (
                    self._canonical_source_version_references(
                        payload.get("source_versions")
                    )
                )
                if not expected_source_versions:
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        "manifesto sem referencias das versoes de origem"
                    )
                elif actual_source_versions is not None:
                    actual_version_subset = (
                        self._canonical_source_version_references(
                            reference
                            for reference in actual_source_versions
                            if str(reference.get("raw_schema") or "").strip()
                            in raw_schemas
                        )
                    )
                    if actual_version_subset != expected_source_versions:
                        problems.append(
                            f"versao {version_code}, origem {source_label}: "
                            "as versoes ou dumps associados ao raw_schema divergiram"
                        )
                actual_subset = self._manifest_subset(
                    actual_manifest,
                    raw_schemas,
                )
                missing_schemas = [
                    value
                    for value in raw_schemas
                    if value not in actual_subset["raw_schemas"]
                ]
                if missing_schemas:
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        "raw_schema sem registros: "
                        + ", ".join(missing_schemas)
                    )

                expected_columns = {
                    str(item.get("name") or ""): str(item.get("type") or "")
                    for item in payload.get("source_columns") or []
                    if isinstance(item, dict) and item.get("name")
                }
                missing_columns = sorted(
                    set(expected_columns) - set(current_columns)
                )
                changed_columns = sorted(
                    column_name
                    for column_name, expected_type in expected_columns.items()
                    if (
                        column_name in current_columns
                        and current_columns[column_name] != expected_type
                    )
                )
                if missing_columns:
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        "colunas ausentes: "
                        + ", ".join(missing_columns)
                    )
                if changed_columns:
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        "tipos alterados: "
                        + ", ".join(
                            (
                                f"{column_name} "
                                f"({expected_columns[column_name]} -> "
                                f"{current_columns[column_name]})"
                            )
                            for column_name in changed_columns
                        )
                    )

                try:
                    expected_row_count = int(
                        payload.get("expected_row_count")
                    )
                    expected_hash_count = int(
                        payload.get("expected_distinct_raw_hash_count")
                    )
                except (TypeError, ValueError):
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        "contagens esperadas invalidas"
                    )
                    continue
                if actual_subset["expected_row_count"] != expected_row_count:
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        f"registros esperados={expected_row_count}, "
                        f"atuais={actual_subset['expected_row_count']}"
                    )
                if (
                    actual_subset["expected_distinct_raw_hash_count"]
                    != expected_hash_count
                ):
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        f"hashes distintos esperados={expected_hash_count}, "
                        "atuais="
                        f"{actual_subset['expected_distinct_raw_hash_count']}"
                    )

                expected_hash_manifest = self._canonical_raw_hash_manifest(
                    payload.get("raw_hash_manifest")
                )
                if (
                    actual_subset["raw_hash_manifest"]
                    != expected_hash_manifest
                ):
                    problems.append(
                        f"versao {version_code}, origem {source_label}: "
                        "o conjunto de raw_hash ou suas contagens divergiu"
                    )

        if problems:
            raise RuntimeError(
                "Preflight da restauracao encontrou problema(s); "
                "nenhuma tabela foi removida:\n- "
                + "\n- ".join(problems)
            )

        self._notify_progress(
            progress_callback,
            "prepare",
            98,
            "Receitas, tabelas, colunas, registros e hashes validados.",
        )

    @staticmethod
    def _recipe_uses_delta_raw_dump(sql_recipe: str) -> bool:
        for line in str(sql_recipe or "").splitlines():
            normalized_line = line.strip().lower()
            if not normalized_line.startswith("-- raw dump scope:"):
                continue
            _prefix, _separator, value = normalized_line.partition(":")
            return value.strip() == "delta"
        return False

    def _replay_table_versions(
        self,
        database_name: str,
        full_table_name: str,
        replay_plan,
        recipe_transform=None,
        cancel_event=None,
        progress_callback=None,
    ):
        total_versions = len(replay_plan or [])
        if not total_versions:
            self._notify_progress(
                progress_callback,
                "replay",
                100,
                "Nenhuma versao precisa ser reaplicada para concluir a restauracao.",
            )
            return

        for index, replay_version in enumerate(replay_plan, start=1):
            self._raise_if_cancelled(cancel_event)
            version_code = replay_version.get("version_code") or f"item {index}"
            start_progress = 100 * (index - 1) / total_versions
            end_progress = 100 * index / total_versions
            recipe = replay_version.get("sql_recipe") or ""
            if recipe_transform:
                recipe = recipe_transform(recipe)
            recipe = recipe.strip()
            dump_path = replay_version.get("raw_dump_path")
            delta_dump_version = bool(dump_path) and self._recipe_uses_delta_raw_dump(recipe)

            self._notify_progress(
                progress_callback,
                "replay",
                min(start_progress + 2, 99),
                f"Reaplicando versao {version_code} ({index}/{total_versions})...",
            )
            if recipe:
                command = (
                    f"psql -h localhost -U {shlex.quote(self.sql_username)} "
                    f"-d {shlex.quote(database_name)} "
                    f"-X -v ON_ERROR_STOP=1"
                )

                try:
                    self.run_remote_command(
                        command,
                        stdin_text=recipe,
                        cancel_event=cancel_event,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Falha ao executar a receita SQL da versão {version_code}. "
                        f"Erro original: {type(exc).__name__}: {exc}"
                    ) from exc
                self._notify_progress(
                    progress_callback,
                    "replay",
                    min(start_progress + ((end_progress - start_progress) * 0.6), 99),
                    f"Receita SQL da versao {version_code} aplicada.",
                )

            if dump_path:
                self._notify_progress(
                    progress_callback,
                    "replay",
                    min(start_progress + ((end_progress - start_progress) * 0.75), 99),
                    (
                        f"Restaurando dump Raw delta da versao {version_code}..."
                        if delta_dump_version
                        else f"Restaurando dump Raw da versao {version_code}..."
                    ),
                )
                try:
                    self.restore_raw_snapshot_dump(
                        database_name,
                        full_table_name,
                        dump_path,
                        cancel_event=cancel_event,
                        merge_existing=not delta_dump_version,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Falha ao restaurar o dump da versão {version_code}. "
                        f"Arquivo: {dump_path}. "
                        f"Reconhecido como delta: {delta_dump_version}. "
                        f"Erro original: {type(exc).__name__}: {exc}"
                    ) from exc

            if recipe and dump_path:
                completion_message = (
                    f"Versao {version_code} reaplicada com receita SQL e dump Raw ({index}/{total_versions})."
                )
            elif recipe:
                completion_message = f"Versao {version_code} reaplicada com receita SQL ({index}/{total_versions})."
            elif dump_path:
                completion_message = f"Versao {version_code} reaplicada com dump Raw ({index}/{total_versions})."
            else:
                completion_message = f"Versao {version_code} nao possui acoes para replay ({index}/{total_versions})."

            self._notify_progress(
                progress_callback,
                "replay",
                end_progress,
                completion_message,
            )

    def provision_table_from_raw_file(
        self,
        database_name: str,
        full_table_name: str,
        file_path: str,
        raw_source: dict | None = None,
        progress_callback=None,
        cancel_event=None,
        post_commit_callback=None,
    ):
        schema_name, table_name = split_table_name(full_table_name)
        self.ensure_data_database_metadata(database_name, cancel_event=cancel_event)
        if raw_source is None:
            raw_source = self._load_raw_source(
                file_path,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
        appended_row_count = len(raw_source["records"])
        self._notify_progress(
            progress_callback,
            "payload",
            0,
            "Validando colunas existentes da tabela para carga Raw...",
        )
        self._raise_if_cancelled(cancel_event)
        existing_columns = self.get_table_column_definitions(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        table_already_exists = self.table_exists(database_name, full_table_name, cancel_event=cancel_event)
        existing_row_count = (
            self.get_table_row_count(database_name, full_table_name, cancel_event=cancel_event)
            if table_already_exists
            else 0
        )

        system_columns = self._get_raw_source_system_columns(raw_source)
        required_raw_columns = set(system_columns)
        blocking_columns = [
            column["name"]
            for column in existing_columns
            if column["name"] not in required_raw_columns and column["not_null"] and not column["default"]
        ]
        if blocking_columns:
            joined = ", ".join(blocking_columns)
            raise RuntimeError(
                "A tabela possui colunas obrigatorias sem valor padrao e nao pode receber carga Raw parcial: "
                f"{joined}."
            )

        existing_column_names = {
            str(column.get("name") or "").strip()
            for column in existing_columns
            if str(column.get("name") or "").strip()
        }
        missing_system_columns = [
            column_name
            for column_name in system_columns
            if column_name not in existing_column_names
        ]
        prepare_statements = []
        if not table_already_exists:
            prepare_statements.extend(
                [
                    f"CREATE SCHEMA IF NOT EXISTS {sql_ident(schema_name)};",
                    f"CREATE TABLE IF NOT EXISTS {sql_ident(schema_name)}.{sql_ident(table_name)} ();",
                    self._build_table_row_count_initialize_sql(schema_name, table_name),
                ]
            )
        if "raw_hash" in missing_system_columns:
            prepare_statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_hash')} text;"
            )
        if "raw_ingested_at" in missing_system_columns:
            prepare_statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_ingested_at')} timestamptz;"
            )
        if "raw_schema" in missing_system_columns:
            prepare_statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_schema')} text;"
            )
        if "raw_tab" in missing_system_columns:
            prepare_statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_tab')} text;"
            )
        if "raw_id" in missing_system_columns:
            prepare_statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw_id')} bigint;"
            )
        if "raw" in missing_system_columns:
            prepare_statements.append(
                f"ALTER TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} "
                f"ADD COLUMN IF NOT EXISTS {sql_ident('raw')} jsonb;"
            )

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -v ON_ERROR_STOP=1"
        )
        if prepare_statements:
            prepare_script = "BEGIN;\n" + "\n".join(prepare_statements) + "\nCOMMIT;\n"
            self._notify_progress(
                progress_callback,
                "payload",
                3,
                "Garantindo estrutura Raw da tabela...",
            )
            self.run_remote_command(command, stdin_text=prepare_script, cancel_event=cancel_event)
        else:
            self._notify_progress(
                progress_callback,
                "payload",
                3,
                "Estrutura Raw ja pronta. Nenhum ALTER TABLE necessario.",
            )

        if "raw_id" in system_columns:
            self._notify_progress(
                progress_callback,
                "payload",
                4,
                "Conferindo continuidade da sequencia universal de raw_id...",
            )
            self.backfill_missing_raw_ids(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )

        assigned_raw_id_start = None
        assigned_raw_id_end = None
        if "raw_id" in system_columns and appended_row_count:
            assigned_raw_id_start = self.reserve_table_raw_ids(
                database_name,
                full_table_name,
                appended_row_count,
                cancel_event=cancel_event,
            )
            assigned_raw_id_end = assigned_raw_id_start + appended_row_count - 1

        self._notify_progress(
            progress_callback,
            "payload",
            5,
            f"Montando carga de importacao para {len(raw_source['records'])} registros...",
        )
        copy_script_stream = None
        required_free_bytes = 0
        try:
            copy_script_stream, copy_payload_size = self._build_raw_copy_script_stream(
                schema_name,
                table_name,
                raw_source["file_hash"],
                raw_source["ingested_at"],
                raw_source.get("raw_schema"),
                raw_source["records"],
                system_columns=system_columns,
                raw_id_start=assigned_raw_id_start,
                row_count_delta=appended_row_count,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
            required_free_bytes = self._estimate_raw_import_required_free_bytes(copy_payload_size) if copy_payload_size else 0
            if required_free_bytes > 0:
                self._notify_progress(
                    progress_callback,
                    "payload",
                    92,
                    "Verificando espaco livre no data_directory do PostgreSQL...",
                )
                self._raise_if_postgres_storage_too_low(
                    database_name,
                    required_free_bytes,
                    cancel_event=cancel_event,
                )
            self._notify_progress(
                progress_callback,
                "database",
                0,
                "Enviando dados para o PostgreSQL...",
            )
            try:
                self.run_remote_command(
                    command,
                    stdin_text=copy_script_stream,
                    cancel_event=cancel_event,
                    stdin_progress_callback=lambda progress, message: self._notify_progress(
                        progress_callback,
                        "database",
                        progress,
                        message,
                    ),
                )
            except RuntimeError as exc:
                raise self._rewrite_storage_error(
                    exc,
                    database_name,
                    cancel_event=cancel_event,
                    required_bytes=required_free_bytes or None,
                ) from exc
            if post_commit_callback:
                post_commit_callback()
        finally:
            if copy_script_stream is not None:
                copy_script_stream.close()

        self._notify_progress(
            progress_callback,
            "database",
            100,
            "Carga concluida. Consolidando historico da tabela...",
        )

        total_row_count = self.get_table_row_count(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        append_start_row = existing_row_count + 1 if appended_row_count else None
        append_end_row = total_row_count if appended_row_count else None

        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "row_count": appended_row_count,
            "total_row_count": total_row_count,
            "raw_source": {
                "file_name": raw_source["file_name"],
                "file_hash": raw_source["file_hash"],
                "ingested_at": raw_source["ingested_at"],
                "raw_schema": raw_source.get("raw_schema"),
                "source_type": raw_source.get("source_type", "text"),
                "raw_system_columns": list(system_columns),
                "raw_dump_scope": "delta",
                "source_count": raw_source.get("source_count", 1),
                "source_files": list(raw_source.get("source_files") or []),
                "row_count": total_row_count,
                "appended_row_count": appended_row_count,
                "append_start_row": append_start_row,
                "append_end_row": append_end_row,
                "assigned_raw_id_start": assigned_raw_id_start,
                "assigned_raw_id_end": assigned_raw_id_end,
            },
        }

    def ensure_remote_admin_dirs(self):
        cmd = f"""
mkdir -p {shlex.quote(self.remote_storage_root)}/deletions/database
mkdir -p {shlex.quote(self.remote_storage_root)}/deletions/table
"""
        self.run_remote_command(cmd)

    def _control_database_exists(self, cancel_event=None) -> bool:
        sql = (
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_database "
            f"WHERE datname = {sql_literal(CONTROL_DB)}"
            ");"
        )
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d postgres -X -qAt -v ON_ERROR_STOP=1 -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip().lower()
        return output in {"t", "true", "1"}

    def ensure_control_database(self, cancel_event=None) -> bool:
        """Create the control database once; return True only when it was created."""
        if self._control_database_exists(cancel_event=cancel_event):
            return False

        create_sql = (
            f"CREATE DATABASE {sql_ident(CONTROL_DB)} "
            f"OWNER {sql_ident(self.sql_username)};"
        )
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d postgres -X -v ON_ERROR_STOP=1 -c {shlex.quote(create_sql)}"
        )
        try:
            self.run_remote_command(command, cancel_event=cancel_event)
        except RuntimeError as exc:
            # A concurrent first connection may have created it after our check.
            try:
                if self._control_database_exists(cancel_event=cancel_event):
                    return False
            except Exception:
                pass
            raise RuntimeError(
                f"Control database {CONTROL_DB!r} does not exist and could not be created. "
                f"PostgreSQL user {self.sql_username!r} must have CREATEDB, or an administrator "
                f"must run: {create_sql} Original error: {exc}"
            ) from exc

        return True

    def ensure_admin_schema(self, cancel_event=None, trace_context: dict | None = None):
        started_at = time.perf_counter()
        self._trace_table_open(trace_context, "ensure_admin_schema(): iniciando")
        sql = f"""
CREATE SCHEMA IF NOT EXISTS {CONTROL_SCHEMA_IDENT};

CREATE TABLE IF NOT EXISTS {CONTROL_TABLE_DELETE_AUDIT} (
    id bigserial PRIMARY KEY,
    action_type text NOT NULL,
    database_name text,
    schema_name text,
    table_name text,
    object_name text NOT NULL,
    requested_by text NOT NULL,
    workstation_name text NOT NULL,
    justification text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    backup_payload_path text NOT NULL,
    executed_sql text NOT NULL
);

CREATE TABLE IF NOT EXISTS {CONTROL_TABLE_VERSIONS} (
    id bigserial PRIMARY KEY,
    database_name text NOT NULL,
    schema_name text NOT NULL,
    table_name text NOT NULL,
    version_code text NOT NULL,
    version_title text NOT NULL,
    created_by text NOT NULL,
    workstation_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    sql_recipe text NOT NULL,
    restored_from_version text,
    version_history_log text,
    version_history_format text DEFAULT 'full',
    raw_dump_path text,
    raw_hash text,
    raw_ingested_at timestamptz,
    raw_schema text,
    operation_kind text NOT NULL DEFAULT 'standard',
    UNIQUE (database_name, schema_name, table_name, version_code)
);

CREATE TABLE IF NOT EXISTS {CONTROL_TABLE_VERSION_DEPENDENCIES} (
    id bigserial PRIMARY KEY,
    table_version_id bigint NOT NULL
        REFERENCES {CONTROL_TABLE_VERSIONS} (id)
        ON DELETE CASCADE,
    dependency_kind text NOT NULL,
    source_database_name text NOT NULL,
    source_schema_name text NOT NULL,
    source_table_name text NOT NULL,
    dependency_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (
        table_version_id,
        dependency_kind,
        source_database_name,
        source_schema_name,
        source_table_name
    )
);

CREATE INDEX IF NOT EXISTS table_version_dependencies_version_idx
ON {CONTROL_TABLE_VERSION_DEPENDENCIES} (table_version_id);

CREATE TABLE IF NOT EXISTS {CONTROL_TABLE_DATA_DICTIONARY} (
    standard_name text PRIMARY KEY,
    definition text NOT NULL,
    units text NOT NULL,
    value_domain text NOT NULL,
    aliases text,
    data_type text
);

CREATE TABLE IF NOT EXISTS {CONTROL_TABLE_DATA_DICTIONARY_USAGE} (
    database_name text NOT NULL,
    schema_name text NOT NULL,
    table_name text NOT NULL,
    column_name text NOT NULL,
    standard_name text NOT NULL REFERENCES {CONTROL_TABLE_DATA_DICTIONARY} (standard_name)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    linked_at timestamptz NOT NULL DEFAULT now(),
    linked_by text,
    PRIMARY KEY (database_name, schema_name, table_name, column_name)
);

CREATE TABLE IF NOT EXISTS {CONTROL_TABLE_RAW_ID_COUNTERS} (
    database_name text NOT NULL,
    schema_name text NOT NULL,
    table_name text NOT NULL,
    next_raw_id bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (database_name, schema_name, table_name)
);

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ADD COLUMN IF NOT EXISTS version_history_log text;

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ADD COLUMN IF NOT EXISTS version_history_format text;

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ALTER COLUMN version_history_format SET DEFAULT 'full';

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ADD COLUMN IF NOT EXISTS raw_dump_path text;

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ADD COLUMN IF NOT EXISTS raw_hash text;

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ADD COLUMN IF NOT EXISTS raw_ingested_at timestamptz;

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ADD COLUMN IF NOT EXISTS raw_schema text;

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ADD COLUMN IF NOT EXISTS operation_kind text;

UPDATE {CONTROL_TABLE_VERSIONS}
SET operation_kind = 'standard'
WHERE operation_kind IS NULL OR btrim(operation_kind) = '';

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ALTER COLUMN operation_kind SET DEFAULT 'standard';

ALTER TABLE {CONTROL_TABLE_VERSIONS}
ALTER COLUMN operation_kind SET NOT NULL;

ALTER TABLE {CONTROL_TABLE_DATA_DICTIONARY}
ADD COLUMN IF NOT EXISTS data_type text;

UPDATE {CONTROL_TABLE_VERSIONS}
SET version_history_log = sql_recipe
WHERE version_history_log IS NULL;

UPDATE {CONTROL_TABLE_VERSIONS}
SET version_history_format = CASE
    WHEN COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
    ELSE 'full'
END
WHERE version_history_format IS NULL;
"""
        if CONTROL_SCHEMA != LEGACY_CONTROL_SCHEMA:
            sql += f"""

DO $$
BEGIN
    IF to_regclass({sql_literal(LEGACY_CONTROL_TABLE_DELETE_AUDIT)}) IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM {CONTROL_TABLE_DELETE_AUDIT} LIMIT 1) THEN
        INSERT INTO {CONTROL_TABLE_DELETE_AUDIT} (
            action_type,
            database_name,
            schema_name,
            table_name,
            object_name,
            requested_by,
            workstation_name,
            justification,
            requested_at,
            backup_payload_path,
            executed_sql
        )
        SELECT
            action_type,
            database_name,
            schema_name,
            table_name,
            object_name,
            requested_by,
            workstation_name,
            justification,
            requested_at,
            backup_payload_path,
            executed_sql
        FROM {LEGACY_CONTROL_TABLE_DELETE_AUDIT};
    END IF;

    IF to_regclass({sql_literal(LEGACY_CONTROL_TABLE_VERSIONS)}) IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM {CONTROL_TABLE_VERSIONS} LIMIT 1) THEN
        INSERT INTO {CONTROL_TABLE_VERSIONS} (
            database_name,
            schema_name,
            table_name,
            version_code,
            version_title,
            created_by,
            workstation_name,
            created_at,
            sql_recipe,
            restored_from_version,
            version_history_log,
            version_history_format,
            raw_dump_path,
            raw_hash,
            raw_ingested_at,
            raw_schema
        )
        SELECT
            database_name,
            schema_name,
            table_name,
            version_code,
            version_title,
            created_by,
            workstation_name,
            created_at,
            sql_recipe,
            restored_from_version,
            version_history_log,
            CASE
                WHEN COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
                ELSE 'full'
            END,
            raw_dump_path,
            raw_hash,
            raw_ingested_at,
            NULL
        FROM {LEGACY_CONTROL_TABLE_VERSIONS};
    END IF;
END
$$;

UPDATE {CONTROL_TABLE_VERSIONS}
SET version_history_log = sql_recipe
WHERE version_history_log IS NULL;

UPDATE {CONTROL_TABLE_VERSIONS}
SET version_history_format = CASE
    WHEN COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
    ELSE 'full'
END
WHERE version_history_format IS NULL;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(
            command,
            cancel_event=cancel_event,
            trace_context=trace_context,
            trace_label="ensure_admin_schema",
        )
        self._trace_table_open(
            trace_context,
            f"ensure_admin_schema(): conclu?do em {(time.perf_counter() - started_at) * 1000:.1f} ms",
        )

    def list_dump_artifacts(self, cancel_event=None):
        self.normalize_archived_raw_dump_paths(cancel_event=cancel_event)

        dump_roots = [
            f"{self.remote_storage_root}/deletions/database",
            f"{self.remote_storage_root}/deletions/table",
            f"{self.remote_storage_root}/raw_versions",
        ]
        command = "\n".join(
            [
                "(",
                *[
                    (
                        f"if [ -d {shlex.quote(root)} ]; then "
                        f"find {shlex.quote(root)} -type f \\( -name '*.dump' -o -name '*.dump.gz' \\) "
                        "-printf '%s\\t%p\\n'; "
                        "fi"
                    )
                    for root in dump_roots
                ],
                ") | sort -k2",
            ]
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)

        items = []
        for line in (line.strip() for line in output.splitlines() if line.strip()):
            raw_size, raw_path = self._parse_dump_artifact_listing_line(line)
            tree_parts = self._build_dump_tree_parts(raw_path)
            if not tree_parts:
                continue

            items.append({
                "full_path": raw_path,
                "file_name": tree_parts[-1],
                "size_bytes": raw_size,
                "size_label": self._format_dump_size(raw_size),
                "tree_parts": tree_parts,
                "artifact_kind": tree_parts[0],
                "can_restore": tree_parts[0] == "raw_versions",
            })

        return items

    @staticmethod
    def _parse_dump_artifact_listing_line(line: str):
        if "\t" not in line:
            return None, line

        raw_size, raw_path = line.split("\t", 1)
        try:
            return int(raw_size), raw_path
        except ValueError:
            return None, raw_path

    @staticmethod
    def _format_dump_size(size_bytes: int | None) -> str:
        if size_bytes is None:
            return "tamanho desconhecido"

        units = ["KB", "MB", "GB", "TB", "PB"]
        value = max(size_bytes, 0) / 1024
        unit_index = 0

        while value >= 999.5 and unit_index < len(units) - 1:
            value /= 1024
            unit_index += 1

        if value < 100:
            number = f"{value:.1f}".replace(".", ",")
        else:
            number = str(int(round(value)))
            if number == "1000" and unit_index < len(units) - 1:
                value /= 1024
                unit_index += 1
                number = f"{value:.1f}".replace(".", ",")

        return f"{number} {units[unit_index]}"

    def _build_dump_tree_parts(self, dump_path: str):
        roots = [
            ("deletions/database", f"{self.remote_storage_root}/deletions/database"),
            ("deletions/table", f"{self.remote_storage_root}/deletions/table"),
            ("raw_versions", f"{self.remote_storage_root}/raw_versions"),
        ]

        normalized = dump_path.strip()
        for label, root in roots:
            prefix = f"{root}/"
            if not normalized.startswith(prefix):
                continue

            relative = normalized[len(prefix):]
            relative_parts = [part for part in relative.split("/") if part]
            if not relative_parts:
                return None
            return label.split("/") + relative_parts

        return None

    def _is_allowed_dump_artifact_path(self, dump_path: str) -> bool:
        normalized = dump_path.strip()
        allowed_roots = [
            f"{self.remote_storage_root}/deletions/database/",
            f"{self.remote_storage_root}/deletions/table/",
            f"{self.remote_storage_root}/raw_versions/",
        ]
        return any(normalized.startswith(root) for root in allowed_roots)

    def get_dump_artifact_context(self, dump_path: str, cancel_event=None):
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT database_name, schema_name, table_name
FROM (
    SELECT 1 AS priority, database_name, schema_name, table_name
    FROM {CONTROL_TABLE_VERSIONS}
    WHERE raw_dump_path = {sql_literal(dump_path)}

    UNION ALL

    SELECT 2 AS priority, database_name, schema_name, table_name
    FROM {CONTROL_TABLE_DELETE_AUDIT}
    WHERE backup_payload_path = {sql_literal(dump_path)}
) AS refs
ORDER BY priority
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        if not rows:
            return {
                "database_name": None,
                "schema_name": None,
                "table_name": None,
            }

        row = rows[0]
        return {
            "database_name": row[0] or None,
            "schema_name": row[1] or None,
            "table_name": row[2] or None,
        }

    def get_dump_artifact_restore_info(self, dump_path: str, cancel_event=None):
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT
    database_name,
    schema_name,
    table_name,
    version_code,
    version_title,
    COALESCE(raw_hash, ''),
    COALESCE(raw_ingested_at::text, ''),
    COALESCE(raw_schema, '')
FROM {CONTROL_TABLE_VERSIONS}
WHERE raw_dump_path = {sql_literal(dump_path)}
ORDER BY
    CASE
        WHEN table_name ~ '__deleted_[0-9]+$' THEN 0
        ELSE 1
    END,
    version_code DESC
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        if not rows:
            return None

        row = rows[0]
        source_table_name = row[2]
        original_table_name = self.resolve_original_table_name(source_table_name)
        schema_name = row[1]
        return {
            "database_name": row[0],
            "schema_name": schema_name,
            "table_name": source_table_name,
            "original_table_name": original_table_name,
            "recipe_source_table_name": original_table_name,
            "version_code": row[3],
            "version_title": row[4],
            "raw_hash": row[5] or None,
            "raw_ingested_at": row[6] or None,
            "raw_schema": row[7] or None,
            "default_restore_full_table_name": f"{schema_name}.{original_table_name}",
        }

    def database_exists(self, db_name: str, cancel_event=None) -> bool:
        sql = f"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = {sql_literal(db_name)});"
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip().lower()
        return output in {"t", "true", "1"}

    def table_exists(self, database_name: str, full_table_name: str, cancel_event=None) -> bool:
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT EXISTS (
    SELECT 1
    FROM pg_tables
    WHERE schemaname = {sql_literal(schema_name)}
      AND tablename = {sql_literal(table_name)}
);
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip().lower()
        return output in {"t", "true", "1"}

    @staticmethod
    def _data_table_row_counts_ddl_sql() -> str:
        return f"""
CREATE TABLE IF NOT EXISTS {DATA_TABLE_ROW_COUNTS} (
    schema_name text NOT NULL,
    table_name text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0),
    initialized_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_reconciled_at timestamptz,
    PRIMARY KEY (schema_name, table_name)
);

COMMENT ON TABLE {DATA_TABLE_ROW_COUNTS} IS
    'Contagem exata de linhas das tabelas gerenciadas pelo PGDM DB Manager.';
""".strip()

    def ensure_data_database_metadata(self, database_name: str, cancel_event=None):
        with self._data_metadata_lock:
            if database_name in self._data_metadata_ready_databases:
                return

            sql = (
                "BEGIN;\n"
                "SET LOCAL client_min_messages = warning;\n"
                + self._data_table_row_counts_ddl_sql()
                + "\nCOMMIT;\n"
            )
            command = (
                f"psql -h localhost -U {shlex.quote(self.sql_username)} "
                f"-d {shlex.quote(database_name)} -X -q -v ON_ERROR_STOP=1"
            )
            self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
            self._data_metadata_ready_databases.add(database_name)

    @staticmethod
    def _build_table_row_count_initialize_sql(schema_name: str, table_name: str, row_count: int = 0) -> str:
        normalized_count = int(row_count)
        if normalized_count < 0:
            raise ValueError("A contagem inicial de linhas nao pode ser negativa.")
        return f"""
INSERT INTO {DATA_TABLE_ROW_COUNTS} (
    schema_name,
    table_name,
    row_count,
    initialized_at,
    updated_at,
    last_reconciled_at
)
VALUES (
    {sql_literal(schema_name)},
    {sql_literal(table_name)},
    {normalized_count},
    clock_timestamp(),
    clock_timestamp(),
    clock_timestamp()
)
ON CONFLICT (schema_name, table_name) DO NOTHING;
""".strip()

    @staticmethod
    def _build_table_row_count_delta_sql(schema_name: str, table_name: str, row_delta: int) -> str:
        normalized_delta = int(row_delta)
        return PostgresAdminService._build_table_row_count_delta_expression_sql(
            schema_name,
            table_name,
            str(normalized_delta),
            delta_description=str(normalized_delta),
        )

    @staticmethod
    def _build_table_row_count_delta_expression_sql(
        schema_name: str,
        table_name: str,
        row_delta_expression: str,
        delta_description: str = "expressao",
    ) -> str:
        return f"""
DO $pgdm_table_row_count_delta$
BEGIN
    UPDATE {DATA_TABLE_ROW_COUNTS}
    SET row_count = row_count + ({row_delta_expression})::bigint,
        updated_at = clock_timestamp()
    WHERE schema_name = {sql_literal(schema_name)}
      AND table_name = {sql_literal(table_name)}
      AND row_count + ({row_delta_expression})::bigint >= 0;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Contador ausente ou delta invalido para %.% (delta=%).',
            {sql_literal(schema_name)},
            {sql_literal(table_name)},
            {sql_literal(delta_description)};
    END IF;
END
$pgdm_table_row_count_delta$;
""".strip()

    @staticmethod
    def _build_table_row_count_set_sql(schema_name: str, table_name: str, row_count_expression: str) -> str:
        return f"""
DO $pgdm_table_row_count_set$
BEGIN
    UPDATE {DATA_TABLE_ROW_COUNTS}
    SET row_count = ({row_count_expression})::bigint,
        updated_at = clock_timestamp()
    WHERE schema_name = {sql_literal(schema_name)}
      AND table_name = {sql_literal(table_name)};

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Contador ausente para %.%.',
            {sql_literal(schema_name)},
            {sql_literal(table_name)};
    END IF;
END
$pgdm_table_row_count_set$;
""".strip()

    @staticmethod
    def _build_table_row_count_delete_sql(schema_name: str, table_name: str) -> str:
        return f"""
DELETE FROM {DATA_TABLE_ROW_COUNTS}
WHERE schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
""".strip()

    def drop_table_with_row_counter(
        self,
        database_name: str,
        full_table_name: str,
        if_exists: bool = True,
        cancel_event=None,
    ) -> str:
        self.ensure_data_database_metadata(database_name, cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        if_exists_sql = " IF EXISTS" if if_exists else ""
        drop_sql = (
            f"DROP TABLE{if_exists_sql} "
            f"{sql_ident(schema_name)}.{sql_ident(table_name)} CASCADE;"
        )
        counter_sql = self._build_table_row_count_delete_sql(schema_name, table_name)
        sql = f"BEGIN;\n{drop_sql}\n{counter_sql}\nCOMMIT;\n"
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -q -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(
            command,
            stdin_text=sql,
            cancel_event=cancel_event,
        )
        return sql

    def reconcile_table_row_count(self, database_name: str, full_table_name: str, cancel_event=None) -> int:
        self.ensure_data_database_metadata(database_name, cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        qualified_table = f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
        sql = f"""
BEGIN;
LOCK TABLE {qualified_table} IN SHARE ROW EXCLUSIVE MODE;
INSERT INTO {DATA_TABLE_ROW_COUNTS} (
    schema_name,
    table_name,
    row_count,
    initialized_at,
    updated_at,
    last_reconciled_at
)
SELECT
    {sql_literal(schema_name)},
    {sql_literal(table_name)},
    COUNT(*),
    clock_timestamp(),
    clock_timestamp(),
    clock_timestamp()
FROM {qualified_table}
ON CONFLICT (schema_name, table_name)
DO UPDATE SET
    row_count = EXCLUDED.row_count,
    updated_at = EXCLUDED.updated_at,
    last_reconciled_at = EXCLUDED.last_reconciled_at
RETURNING row_count;
COMMIT;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -qAt -v ON_ERROR_STOP=1"
        )
        output = self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event).strip()
        first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
        return int(first_line or "0")

    def get_table_row_count(self, database_name: str, full_table_name: str, cancel_event=None) -> int:
        self.ensure_data_database_metadata(database_name, cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT row_count
FROM {DATA_TABLE_ROW_COUNTS}
WHERE schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        if output:
            return int(output)
        return self.reconcile_table_row_count(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )

    @staticmethod
    def _dedupe_non_empty_strings(values) -> list[str]:
        result = []
        seen = set()
        for value in values or []:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _is_simple_sql_identifier(name: str) -> bool:
        return re.fullmatch(r"[a-z_][a-z0-9_$]*", str(name or "")) is not None

    @staticmethod
    def _dedupe_rewrite_pairs(pairs):
        result = []
        seen = set()
        for source, target in pairs or []:
            source_text = str(source or "")
            target_text = str(target or "")
            key = (source_text, target_text)
            if not source_text or key in seen:
                continue
            seen.add(key)
            result.append(key)
        return result

    @staticmethod
    def _replace_once_values(text: str, replacements) -> str:
        rewritten = text
        for source, target in replacements:
            if source and source != target:
                rewritten = rewritten.replace(source, target)
        return rewritten

    @staticmethod
    def _table_reference_regex(source_identifier: str) -> str:
        table_keywords = (
            "FROM|JOIN|UPDATE|INTO|TABLE|REFERENCES|TRUNCATE|COPY|LOCK"
        )
        return (
            rf"(^|[^A-Za-z0-9_$\"])"
            rf"((?:{table_keywords})\s+"
            rf"(?:ONLY\s+)?"
            rf"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
            rf")"
            rf"{re.escape(source_identifier)}"
            rf"([^A-Za-z0-9_$\"]|$)"
        )

    @classmethod
    def _replace_table_reference_identifier(cls, text: str, source_identifier: str, target_identifier: str) -> str:
        if not source_identifier or source_identifier == target_identifier:
            return text
        pattern = re.compile(cls._table_reference_regex(source_identifier), flags=re.IGNORECASE)
        return pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}{target_identifier}{match.group(3)}", text)

    @staticmethod
    def _sql_regexp_replace_expression(
            expression_sql: str,
            pattern: str,
            replacement: str,
            flags: str = "g",
    ) -> str:
        return (
            "regexp_replace("
            f"{expression_sql}, "
            f"{sql_literal(pattern)}, "
            f"{sql_literal(replacement)}, "
            f"{sql_literal(flags)})"
        )

    def _recipe_table_exact_rewrite_pairs(
            self,
            source_schema_name: str,
            source_table_name: str,
            target_schema_name: str,
            target_table_name: str,
    ):
        source_full_ident = f"{sql_ident(source_schema_name)}.{sql_ident(source_table_name)}"
        target_full_ident = f"{sql_ident(target_schema_name)}.{sql_ident(target_table_name)}"
        pairs = [
            (source_full_ident, target_full_ident),
            (sql_literal(f"{source_schema_name}.{source_table_name}"), sql_literal(f"{target_schema_name}.{target_table_name}")),
            (sql_literal(source_table_name), sql_literal(target_table_name)),
        ]

        if source_schema_name != target_schema_name:
            pairs.extend([
                (
                    f"CREATE SCHEMA IF NOT EXISTS {sql_ident(source_schema_name)};",
                    f"CREATE SCHEMA IF NOT EXISTS {sql_ident(target_schema_name)};",
                ),
                (sql_literal(source_schema_name), sql_literal(target_schema_name)),
            ])

        if self._is_simple_sql_identifier(source_schema_name):
            pairs.append(
                (
                    f"{source_schema_name}.{sql_ident(source_table_name)}",
                    target_full_ident,
                )
            )
        if self._is_simple_sql_identifier(source_table_name):
            pairs.append(
                (
                    f"{sql_ident(source_schema_name)}.{source_table_name}",
                    target_full_ident,
                )
            )
        if (
                self._is_simple_sql_identifier(source_schema_name)
                and self._is_simple_sql_identifier(source_table_name)
        ):
            pairs.append((f"{source_schema_name}.{source_table_name}", target_full_ident))

        return self._dedupe_rewrite_pairs(pairs)

    @staticmethod
    def _object_name_prefix_regex(source_prefix: str) -> str:
        object_keywords = "CONSTRAINT|INDEX"
        return (
            rf"(^|[^A-Za-z0-9_$\"])"
            rf"((?:{object_keywords})\s+"
            rf"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
            rf")"
            rf"{re.escape(source_prefix)}"
        )

    @classmethod
    def _replace_prefixed_object_reference(cls, text: str, source_prefix: str, target_prefix: str) -> str:
        if not source_prefix or source_prefix == target_prefix:
            return text
        pattern = re.compile(cls._object_name_prefix_regex(source_prefix), flags=re.IGNORECASE)
        return pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}{target_prefix}", text)

    @staticmethod
    def _recipe_table_prefix_rewrite_pairs(source_table_name: str, target_table_name: str):
        escaped_source = str(source_table_name or "").replace('"', '""')
        escaped_target = str(target_table_name or "").replace('"', '""')
        pairs = [(f'"{escaped_source}_', f'"{escaped_target}_')]
        if PostgresAdminService._is_simple_sql_identifier(source_table_name):
            pairs.append((f"{source_table_name}_", f"{target_table_name}_"))
        return pairs

    def _rewrite_recipe_for_target_table(
            self,
            recipe: str,
            source_schema_name: str,
            source_table_names,
            target_schema_name: str,
            target_table_name: str,
    ) -> str:
        rewritten = (recipe or "").strip()
        if not rewritten:
            return rewritten

        if isinstance(source_table_names, str):
            raw_source_names = [source_table_names]
        else:
            raw_source_names = list(source_table_names or [])
        raw_source_names.append(self.resolve_original_table_name(target_table_name))
        source_table_names = self._dedupe_non_empty_strings(raw_source_names)
        source_table_names = [name for name in source_table_names if name != target_table_name]
        source_table_names.sort(key=len, reverse=True)

        placeholders = []
        placeholder_index = 0
        for source_table_name in source_table_names:
            for source, target in self._recipe_table_exact_rewrite_pairs(
                    source_schema_name,
                    source_table_name,
                    target_schema_name,
                    target_table_name,
            ):
                if source == target or source not in rewritten:
                    continue
                placeholder = f"__PGDM_TABLE_REWRITE_{placeholder_index}__"
                placeholder_index += 1
                rewritten = rewritten.replace(source, placeholder)
                placeholders.append((placeholder, target))

            table_reference_identifiers = [(sql_ident(source_table_name), sql_ident(target_table_name))]
            if self._is_simple_sql_identifier(source_table_name):
                table_reference_identifiers.append((source_table_name, sql_ident(target_table_name)))
            for source_identifier, target_identifier in table_reference_identifiers:
                placeholder = f"__PGDM_TABLE_REWRITE_{placeholder_index}__"
                placeholder_index += 1
                before = rewritten
                rewritten = self._replace_table_reference_identifier(
                    rewritten,
                    source_identifier,
                    placeholder,
                )
                if rewritten != before:
                    placeholders.append((placeholder, target_identifier))
        for source_table_name in source_table_names:
            for source_prefix, target_prefix in self._recipe_table_prefix_rewrite_pairs(
                    source_table_name,
                    target_table_name,
            ):
                rewritten = self._replace_prefixed_object_reference(
                    rewritten,
                    source_prefix,
                    target_prefix,
                )

        for placeholder, target in placeholders:
            rewritten = rewritten.replace(placeholder, target)

        return rewritten

    def _build_recipe_rewrite_sql_expression(
            self,
            expression_sql: str,
            source_schema_name: str,
            source_table_names,
            target_schema_name: str,
            target_table_name: str,
    ) -> str:
        if isinstance(source_table_names, str):
            raw_source_names = [source_table_names]
        else:
            raw_source_names = list(source_table_names or [])
        raw_source_names.append(self.resolve_original_table_name(target_table_name))
        source_table_names = self._dedupe_non_empty_strings(raw_source_names)
        source_table_names = [name for name in source_table_names if name != target_table_name]
        source_table_names.sort(key=len, reverse=True)

        rewritten_expression = expression_sql
        placeholders = []
        placeholder_index = 0
        for source_table_name in source_table_names:
            for source, target in self._recipe_table_exact_rewrite_pairs(
                    source_schema_name,
                    source_table_name,
                    target_schema_name,
                    target_table_name,
            ):
                if source == target:
                    continue
                placeholder = f"__PGDM_TABLE_REWRITE_{placeholder_index}__"
                placeholder_index += 1
                rewritten_expression = (
                    f"replace({rewritten_expression}, {sql_literal(source)}, {sql_literal(placeholder)})"
                )
                placeholders.append((placeholder, target))

            table_reference_identifiers = [(sql_ident(source_table_name), sql_ident(target_table_name))]
            if self._is_simple_sql_identifier(source_table_name):
                table_reference_identifiers.append((source_table_name, sql_ident(target_table_name)))
            for source_identifier, target_identifier in table_reference_identifiers:
                placeholder = f"__PGDM_TABLE_REWRITE_{placeholder_index}__"
                placeholder_index += 1
                rewritten_expression = self._sql_regexp_replace_expression(
                    rewritten_expression,
                    self._table_reference_regex(source_identifier),
                    rf"\1\2{placeholder}\3",
                    flags="gi",
                )
                placeholders.append((placeholder, target_identifier))
        for source_table_name in source_table_names:
            for source_prefix, target_prefix in self._recipe_table_prefix_rewrite_pairs(
                    source_table_name,
                    target_table_name,
            ):
                rewritten_expression = self._sql_regexp_replace_expression(
                    rewritten_expression,
                    self._object_name_prefix_regex(source_prefix),
                    rf"\1\2{target_prefix}",
                    flags="gi",
                )

        for placeholder, target in placeholders:
            rewritten_expression = (
                f"replace({rewritten_expression}, {sql_literal(placeholder)}, {sql_literal(target)})"
            )

        return rewritten_expression

    def _get_table_versions_for_clone(
            self,
            database_name: str,
            schema_name: str,
            table_name: str,
            target_version_code: str,
            cancel_event=None,
    ):
        self._ensure_control_metadata(
            cancel_event=cancel_event,
        )

        # Primeiro busca apenas os códigos. Essa consulta é pequena.
        version_codes_sql = f"""
    SELECT version_code
    FROM {CONTROL_TABLE_VERSIONS}
    WHERE database_name = {sql_literal(database_name)}
      AND schema_name = {sql_literal(schema_name)}
      AND table_name = {sql_literal(table_name)}
      AND version_code <= {sql_literal(target_version_code)}
    ORDER BY version_code ASC;
    """

        version_codes_command = (
            f"psql -h localhost "
            f"-U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} "
            f"-X -At -c {shlex.quote(version_codes_sql)}"
        )

        version_codes_output = self.run_remote_command(
            version_codes_command,
            cancel_event=cancel_event,
        )

        version_codes = [
            line.strip()
            for line in version_codes_output.splitlines()
            if line.strip()
        ]

        if not version_codes:
            return []

        versions = []

        # Busca uma versão por chamada. Isso evita uma saída CSV enorme
        # composta por vários version_history_log acumulativos.
        for version_code in version_codes:
            self._raise_if_cancelled(cancel_event)

            detail_sql = f"""
    SELECT json_build_object(
        'version_code',
            version_code,

        'version_title',
            COALESCE(version_title, ''),

        'created_at',
            created_at::text,

        'created_by',
            COALESCE(created_by, ''),

        'workstation_name',
            COALESCE(workstation_name, ''),

        'sql_recipe',
            COALESCE(sql_recipe, ''),

        'restored_from_version',
            COALESCE(restored_from_version, ''),

        'version_history_log',
            COALESCE(version_history_log, sql_recipe, ''),

        'version_history_format',
            CASE
        WHEN COALESCE(version_history_format, 'full') = 'entry'
          OR COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
        ELSE 'full'
    END,

        'raw_dump_path',
            COALESCE(raw_dump_path, ''),

        'raw_hash',
            COALESCE(raw_hash, ''),

        'raw_ingested_at',
            COALESCE(raw_ingested_at::text, ''),

        'raw_schema',
            COALESCE(raw_schema, '')
    )::text
    FROM {CONTROL_TABLE_VERSIONS}
    WHERE database_name = {sql_literal(database_name)}
      AND schema_name = {sql_literal(schema_name)}
      AND table_name = {sql_literal(table_name)}
      AND version_code = {sql_literal(version_code)}
    LIMIT 1;
    """

            detail_command = (
                f"psql -h localhost "
                f"-U {shlex.quote(self.sql_username)} "
                f"-d {shlex.quote(CONTROL_DB)} "
                f"-X -At -c {shlex.quote(detail_sql)}"
            )

            detail_output = self.run_remote_command(
                detail_command,
                cancel_event=cancel_event,
            )

            if not detail_output.strip():
                raise RuntimeError(
                    "A versão esperada durante a clonagem não foi encontrada. "
                    f"Versão: {version_code}."
                )

            try:
                version = json.loads(detail_output)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Não foi possível interpretar os metadados JSON "
                    f"da versão {version_code}. "
                    f"Saída recebida: {len(detail_output)} caracteres. "
                    f"Erro JSON: {exc}"
                ) from exc

            expected_fields = {
                "version_code",
                "version_title",
                "created_at",
                "created_by",
                "workstation_name",
                "sql_recipe",
                "restored_from_version",
                "version_history_log",
                "version_history_format",
                "raw_dump_path",
                "raw_hash",
                "raw_ingested_at",
                "raw_schema",
            }

            missing_fields = expected_fields.difference(version)

            if missing_fields:
                raise RuntimeError(
                    f"Metadados incompletos na versão {version_code}. "
                    "Campos ausentes: "
                    + ", ".join(sorted(missing_fields))
                )

            if version["version_code"] != version_code:
                raise RuntimeError(
                    "A consulta retornou uma versão diferente da solicitada. "
                    f"Solicitada: {version_code}. "
                    f"Recebida: {version['version_code']}."
                )

            version["raw_schema"] = (
                    version["raw_schema"] or None
            )

            versions.append(version)

        return versions

    def clone_table_version_lineage(
            self,
            database_name: str,
            source_schema_name: str,
            source_table_name: str,
            recipe_source_table_name: str,
            target_schema_name: str,
            target_table_name: str,
            target_version_code: str,
            cancel_event=None,
    ):
        if (
                source_schema_name == target_schema_name
                and source_table_name == target_table_name
        ):
            return

        self._ensure_control_metadata(
            cancel_event=cancel_event,
        )

        target_version_number = version_to_int(
            target_version_code
        )

        # Verificação pequena: não transporta receitas nem histórico.
        count_sql = f"""
    SELECT COUNT(*)
    FROM {CONTROL_TABLE_VERSIONS}
    WHERE database_name = {sql_literal(database_name)}
      AND schema_name = {sql_literal(source_schema_name)}
      AND table_name = {sql_literal(source_table_name)}
      AND version_code::integer <= {target_version_number};
    """

        count_command = (
            f"psql -h localhost "
            f"-U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} "
            f"-X -At -v ON_ERROR_STOP=1 "
            f"-c {shlex.quote(count_sql)}"
        )

        count_output = self.run_remote_command(
            count_command,
            cancel_event=cancel_event,
        ).strip()

        expected_count = int(count_output or "0")

        if expected_count <= 0:
            raise RuntimeError(
                "Nenhum histórico de versões foi encontrado "
                "para clonar nesta restauração."
            )

        # Caso existam metadados antigos com o mesmo nome do destino,
        # preserva-os usando o mecanismo já existente.
        self.archive_legacy_table_lineage(
            database_name,
            target_schema_name,
            target_table_name,
            cancel_event=cancel_event,
        )

        source_table_candidates = self._dedupe_non_empty_strings([
            source_table_name,
            recipe_source_table_name,
        ])
        rewritten_recipe_sql = self._build_recipe_rewrite_sql_expression(
            "COALESCE(sql_recipe, '')",
            source_schema_name,
            source_table_candidates,
            target_schema_name,
            target_table_name,
        )
        rewritten_history_sql = self._build_recipe_rewrite_sql_expression(
            "COALESCE(version_history_log, sql_recipe, '')",
            source_schema_name,
            source_table_candidates,
            target_schema_name,
            target_table_name,
        )
        # Toda a cópia acontece no servidor PostgreSQL.
        # Apenas "esperado|inserido" volta ao Python.
        clone_sql = f"""
    WITH source_versions AS MATERIALIZED (
        SELECT
            id AS source_version_id,
            version_code,
            version_title,
            created_by,
            workstation_name,
            created_at,
            sql_recipe,
            restored_from_version,
            version_history_log,
            CASE
        WHEN COALESCE(version_history_format, 'full') = 'entry'
          OR COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
        ELSE 'full'
    END AS version_history_format,
            raw_dump_path,
            raw_hash,
            raw_ingested_at,
            raw_schema,
            COALESCE(operation_kind, 'standard') AS operation_kind
        FROM {CONTROL_TABLE_VERSIONS}
        WHERE database_name = {sql_literal(database_name)}
          AND schema_name = {sql_literal(source_schema_name)}
          AND table_name = {sql_literal(source_table_name)}
          AND version_code::integer <= {target_version_number}
    ),
    inserted_versions AS (
        INSERT INTO {CONTROL_TABLE_VERSIONS} (
            database_name,
            schema_name,
            table_name,
            version_code,
            version_title,
            created_by,
            workstation_name,
            created_at,
            sql_recipe,
            restored_from_version,
            version_history_log,
            version_history_format,
            raw_dump_path,
            raw_hash,
            raw_ingested_at,
            raw_schema,
            operation_kind
        )
        SELECT
            {sql_literal(database_name)},
            {sql_literal(target_schema_name)},
            {sql_literal(target_table_name)},
            version_code,
            version_title,
            created_by,
            workstation_name,
            created_at,
            {rewritten_recipe_sql},
            restored_from_version,
            {rewritten_history_sql},
            version_history_format,
            raw_dump_path,
            raw_hash,
            raw_ingested_at,
            raw_schema,
            operation_kind
        FROM source_versions
        ORDER BY version_code::integer
        RETURNING id, version_code
    ),
    inserted_dependencies AS (
        INSERT INTO {CONTROL_TABLE_VERSION_DEPENDENCIES} (
            table_version_id,
            dependency_kind,
            source_database_name,
            source_schema_name,
            source_table_name,
            dependency_payload,
            created_at
        )
        SELECT
            inserted_versions.id,
            source_dependency.dependency_kind,
            source_dependency.source_database_name,
            source_dependency.source_schema_name,
            source_dependency.source_table_name,
            source_dependency.dependency_payload,
            source_dependency.created_at
        FROM source_versions
        JOIN inserted_versions
          ON inserted_versions.version_code = source_versions.version_code
        JOIN {CONTROL_TABLE_VERSION_DEPENDENCIES} AS source_dependency
          ON source_dependency.table_version_id = source_versions.source_version_id
        RETURNING 1
    )
    SELECT
        (SELECT COUNT(*) FROM source_versions),
        (SELECT COUNT(*) FROM inserted_versions);
    """

        clone_command = (
            f"psql -h localhost "
            f"-U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} "
            f"-X -At -F '|' -v ON_ERROR_STOP=1 "
            f"-c {shlex.quote(clone_sql)}"
        )

        clone_output = self.run_remote_command(
            clone_command,
            cancel_event=cancel_event,
        ).strip()

        result_line = ""

        for line in reversed(clone_output.splitlines()):
            candidate = line.strip()

            if re.fullmatch(r"\d+\|\d+", candidate):
                result_line = candidate
                break

        if not result_line:
            raise RuntimeError(
                "O PostgreSQL concluiu a tentativa de clonagem, "
                "mas não retornou a contagem esperada. "
                f"Saída recebida: {clone_output!r}"
            )

        expected_text, inserted_text = result_line.split(
            "|",
            1,
        )

        server_expected_count = int(expected_text)
        inserted_count = int(inserted_text)

        if server_expected_count != expected_count:
            raise RuntimeError(
                "A quantidade de versões de origem mudou durante "
                "a preparação da clonagem. "
                f"Contagem inicial: {expected_count}. "
                f"Contagem no INSERT: {server_expected_count}."
            )

        if inserted_count != expected_count:
            raise RuntimeError(
                "A clonagem da linhagem ficou incompleta. "
                f"Esperado: {expected_count} versões. "
                f"Inserido: {inserted_count} versões."
            )

    def restore_dump_artifact_to_table(
        self,
        dump_path: str,
        target_full_table_name: str,
        cancel_event=None,
    ):
        restore_info = self.get_dump_artifact_restore_info(dump_path, cancel_event=cancel_event)
        if not restore_info:
            raise RuntimeError("Este dump nao esta vinculado a uma versao restauravel.")

        database_name = restore_info["database_name"]
        target_schema_name, target_table_name = split_table_name(target_full_table_name)
        if not self.database_exists(database_name, cancel_event=cancel_event):
            raise RuntimeError(f"A base {database_name} nao existe para restauracao.")
        if self.table_exists(database_name, target_full_table_name, cancel_event=cancel_event):
            raise RuntimeError(f"A tabela {target_full_table_name} ja existe na base {database_name}.")

        source_schema_name = restore_info["schema_name"]
        source_table_name = restore_info["table_name"]
        recipe_source_table_name = restore_info["recipe_source_table_name"]
        replay_versions = self._get_table_versions_for_replay(
            database_name,
            source_schema_name,
            source_table_name,
            cancel_event=cancel_event,
        )
        replay_plan = self._build_replay_plan(replay_versions, restore_info["version_code"])
        self._preflight_replay_dependencies(
            database_name,
            target_full_table_name,
            replay_plan,
            recipe_transform=lambda recipe: self._rewrite_recipe_for_target_table(
                recipe,
                source_schema_name,
                [source_table_name, recipe_source_table_name],
                target_schema_name,
                target_table_name,
            ),
            cancel_event=cancel_event,
        )
        self.clone_table_version_lineage(
            database_name,
            source_schema_name,
            source_table_name,
            recipe_source_table_name,
            target_schema_name,
            target_table_name,
            restore_info["version_code"],
            cancel_event=cancel_event,
        )

        self.drop_table_with_row_counter(
            database_name,
            target_full_table_name,
            if_exists=True,
            cancel_event=cancel_event,
        )

        self._replay_table_versions(
            database_name,
            f"{target_schema_name}.{target_table_name}",
            replay_plan,
            recipe_transform=lambda recipe: self._rewrite_recipe_for_target_table(
                recipe,
                source_schema_name,
                [source_table_name, recipe_source_table_name],
                target_schema_name,
                target_table_name,
            ),
            cancel_event=cancel_event,
        )
        if self.table_exists(database_name, target_full_table_name, cancel_event=cancel_event):
            self.reconcile_table_row_count(
                database_name,
                target_full_table_name,
                cancel_event=cancel_event,
            )

        return restore_info

    def clear_dump_artifact_references(self, dump_path: str, cancel_event=None):
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
UPDATE {CONTROL_TABLE_VERSIONS}
SET raw_dump_path = NULL
WHERE raw_dump_path = {sql_literal(dump_path)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(command, cancel_event=cancel_event)

    def delete_dump_artifact(self, dump_path: str, info: dict, cancel_event=None):
        normalized_path = dump_path.strip()
        if not self._is_allowed_dump_artifact_path(normalized_path):
            raise RuntimeError("Caminho de dump nao permitido para exclusao.")
        if not (normalized_path.endswith(".dump") or normalized_path.endswith(".dump.gz")):
            raise RuntimeError("Somente arquivos .dump ou .dump.gz podem ser excluidos.")

        context = self.get_dump_artifact_context(normalized_path, cancel_event=cancel_event)
        delete_command = (
            f"if [ ! -f {shlex.quote(normalized_path)} ]; then "
            f"echo 'Arquivo nao encontrado: {normalized_path}' 1>&2; "
            "exit 1; "
            f"fi && rm -f {shlex.quote(normalized_path)}"
        )
        self.run_remote_command(delete_command, cancel_event=cancel_event)
        self.clear_dump_artifact_references(normalized_path, cancel_event=cancel_event)
        self.register_delete_audit(
            action_type="delete_dump_file",
            database_name=context["database_name"],
            schema_name=context["schema_name"],
            table_name=context["table_name"],
            object_name=normalized_path,
            requested_by=info["requested_by"],
            workstation_name=info["workstation_name"],
            justification=info["justification"],
            backup_payload_path=normalized_path,
            executed_sql=delete_command,
            cancel_event=cancel_event,
        )
        return context

    def check_can_create_database(self) -> bool:
        sql = "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user;"
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command).strip().lower()
        return output in {"t", "true", "1"}

    def list_databases(self):
        sql = "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )

        output = self.run_remote_command(command)
        if not output:
            return []

        return [
            line.strip()
            for line in output.splitlines()
            if line.strip() and line.strip().lower() not in self.HIDDEN_DATABASES
        ]

    def list_tables(self, db_name: str, cancel_event=None):
        if db_name == CONTROL_DB:
            sql = (
                "SELECT schemaname || '.' || tablename "
                "FROM pg_tables "
                f"WHERE schemaname NOT IN ('pg_catalog', 'information_schema', {sql_literal(LEGACY_CONTROL_SCHEMA)}) "
                f"AND NOT (schemaname = 'public' AND tablename = {sql_literal(DATA_TABLE_ROW_COUNTS_NAME)}) "
                "ORDER BY schemaname, tablename;"
            )
        else:
            sql = (
                "SELECT schemaname || '.' || tablename "
                "FROM pg_tables "
                f"WHERE schemaname NOT IN ('pg_catalog', 'information_schema', {sql_literal(CONTROL_SCHEMA)}, {sql_literal(LEGACY_CONTROL_SCHEMA)}) "
                f"AND NOT (schemaname = 'public' AND tablename = {sql_literal(DATA_TABLE_ROW_COUNTS_NAME)}) "
                "ORDER BY schemaname, tablename;"
            )

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(db_name)} -X -At -c {shlex.quote(sql)}"
        )

        output = self.run_remote_command(command, cancel_event=cancel_event)
        return [line.strip() for line in output.splitlines() if line.strip()] if output else []

    @staticmethod
    def _is_truthy_sql_value(value: str) -> bool:
        return str(value).strip().lower() in {"t", "true", "1", "yes"}

    @classmethod
    def _permission_definition_by_key(cls) -> dict:
        return {
            definition["key"]: definition
            for definition in cls.ROLE_PERMISSION_DEFINITIONS
        }

    def list_database_users(self, cancel_event=None) -> list[str]:
        sql = """
SELECT rolname
FROM pg_roles
WHERE rolcanlogin
  AND rolname !~ '^pg_'
ORDER BY rolname;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        return [line.strip() for line in output.splitlines() if line.strip()] if output else []

    def list_database_operations(self, cancel_event=None) -> dict:
        sql = """
WITH activity AS (
    SELECT
        a.pid,
        COALESCE(a.datname, '') AS database_name,
        COALESCE(a.usename, '') AS user_name,
        COALESCE(a.application_name, '') AS application_name,
        COALESCE(a.client_addr::text, a.client_hostname, '') AS client_addr,
        COALESCE(a.state, '') AS state,
        COALESCE(a.wait_event_type, '') AS wait_event_type,
        COALESCE(a.wait_event, '') AS wait_event,
        COALESCE(a.backend_type, '') AS backend_type,
        COALESCE(a.query_start::text, '') AS query_start,
        COALESCE(a.xact_start::text, '') AS xact_start,
        COALESCE(a.state_change::text, '') AS state_change,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - a.query_start))::bigint, 0) AS query_age_seconds,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - a.xact_start))::bigint, 0) AS xact_age_seconds,
        COALESCE(a.query, '') AS query_text,
        pg_blocking_pids(a.pid) AS blocking_pids
    FROM pg_stat_activity AS a
    WHERE a.pid <> pg_backend_pid()
      AND a.backend_type = 'client backend'
)
SELECT
    activity.pid::text,
    activity.database_name,
    activity.user_name,
    activity.application_name,
    activity.client_addr,
    activity.state,
    activity.wait_event_type,
    activity.wait_event,
    activity.backend_type,
    activity.query_start,
    activity.xact_start,
    activity.state_change,
    activity.query_age_seconds::text,
    activity.xact_age_seconds::text,
    CASE
        WHEN EXISTS (
            SELECT 1
            FROM pg_locks AS locks
            WHERE locks.pid = activity.pid
              AND NOT locks.granted
        ) THEN 't'
        ELSE 'f'
    END AS waiting_lock,
    COALESCE(array_length(activity.blocking_pids, 1), 0)::text AS blocking_pid_count,
    COALESCE(array_to_string(activity.blocking_pids, ', '), '') AS blocking_pids,
    COALESCE(
        (
            SELECT string_agg(
                DISTINCT quote_ident(ns.nspname) || '.' || quote_ident(cls.relname),
                ', '
            )
            FROM pg_locks AS locks
            JOIN pg_class AS cls ON cls.oid = locks.relation
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE locks.pid = activity.pid
              AND locks.relation IS NOT NULL
        ),
        ''
    ) AS relation_names,
    COALESCE(
        (
            SELECT string_agg(DISTINCT locks.mode, ', ')
            FROM pg_locks AS locks
            WHERE locks.pid = activity.pid
        ),
        ''
    ) AS lock_modes,
    activity.query_text
FROM activity
ORDER BY
    CASE
        WHEN COALESCE(array_length(activity.blocking_pids, 1), 0) > 0 THEN 0
        WHEN EXISTS (
            SELECT 1
            FROM pg_locks AS locks
            WHERE locks.pid = activity.pid
              AND NOT locks.granted
        ) THEN 1
        WHEN lower(activity.state) = 'active' THEN 2
        WHEN lower(activity.state) = 'idle in transaction' THEN 3
        ELSE 4
    END,
    activity.query_age_seconds DESC,
    activity.pid;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        headers, rows = self.parse_csv_output(output)
        if not headers:
            return {
                "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "summary": {
                    "total_sessions": 0,
                    "active_sessions": 0,
                    "blocked_sessions": 0,
                    "waiting_lock_sessions": 0,
                    "idle_in_transaction_sessions": 0,
                },
                "sessions": [],
            }

        index_map = {header: idx for idx, header in enumerate(headers)}
        sessions = []
        for row in rows:
            sessions.append(
                {
                    "pid": int(row[index_map["pid"]] or 0),
                    "database_name": row[index_map["database_name"]],
                    "user_name": row[index_map["user_name"]],
                    "application_name": row[index_map["application_name"]],
                    "client_addr": row[index_map["client_addr"]],
                    "state": row[index_map["state"]],
                    "wait_event_type": row[index_map["wait_event_type"]],
                    "wait_event": row[index_map["wait_event"]],
                    "backend_type": row[index_map["backend_type"]],
                    "query_start": row[index_map["query_start"]],
                    "xact_start": row[index_map["xact_start"]],
                    "state_change": row[index_map["state_change"]],
                    "query_age_seconds": int(row[index_map["query_age_seconds"]] or 0),
                    "xact_age_seconds": int(row[index_map["xact_age_seconds"]] or 0),
                    "waiting_lock": self._is_truthy_sql_value(row[index_map["waiting_lock"]]),
                    "blocking_pid_count": int(row[index_map["blocking_pid_count"]] or 0),
                    "blocking_pids": row[index_map["blocking_pids"]],
                    "relation_names": row[index_map["relation_names"]],
                    "lock_modes": row[index_map["lock_modes"]],
                    "query_text": row[index_map["query_text"]],
                }
            )

        summary = {
            "total_sessions": len(sessions),
            "active_sessions": sum(
                1
                for session in sessions
                if str(session.get("state") or "").strip().lower() == "active"
            ),
            "blocked_sessions": sum(
                1
                for session in sessions
                if int(session.get("blocking_pid_count") or 0) > 0
            ),
            "waiting_lock_sessions": sum(
                1
                for session in sessions
                if bool(session.get("waiting_lock"))
            ),
            "idle_in_transaction_sessions": sum(
                1
                for session in sessions
                if str(session.get("state") or "").strip().lower() == "idle in transaction"
            ),
        }
        return {
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary,
            "sessions": sessions,
        }

    def _run_backend_control_boolean_sql(
        self,
        sql: str,
        root_password: str | None = None,
        cancel_event=None,
    ) -> bool:
        if root_password:
            command = (
                "sudo -k -S -p '' -u postgres -- "
                f"psql -d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
            )
            output = self.run_remote_command(
                command,
                stdin_text=f"{root_password}\n",
                cancel_event=cancel_event,
                timeout_seconds=60,
            ).strip()
        else:
            command = (
                f"psql -h localhost -U {shlex.quote(self.sql_username)} "
                f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
            )
            output = self.run_remote_command(
                command,
                cancel_event=cancel_event,
                timeout_seconds=60,
            ).strip()
        return self._is_truthy_sql_value(output)

    def cancel_backend_query(
        self,
        pid: int,
        root_password: str | None = None,
        cancel_event=None,
    ) -> bool:
        normalized_pid = int(pid or 0)
        if normalized_pid <= 0:
            raise RuntimeError("PID invalido para cancelamento da query.")
        sql = f"SELECT pg_cancel_backend({normalized_pid});"
        return self._run_backend_control_boolean_sql(
            sql,
            root_password=root_password,
            cancel_event=cancel_event,
        )

    def terminate_backend_session(
        self,
        pid: int,
        root_password: str | None = None,
        cancel_event=None,
    ) -> bool:
        normalized_pid = int(pid or 0)
        if normalized_pid <= 0:
            raise RuntimeError("PID invalido para encerramento da sessao.")
        sql = f"SELECT pg_terminate_backend({normalized_pid});"
        return self._run_backend_control_boolean_sql(
            sql,
            root_password=root_password,
            cancel_event=cancel_event,
        )
    def create_database_user(
        self,
        user_name: str,
        password: str,
        root_password: str | None = None,
        cancel_event=None,
    ):
        if not user_name:
            raise RuntimeError("Nome de usuario nao informado.")
        if not password:
            raise RuntimeError("Senha do usuario nao informada.")

        sql = f"CREATE ROLE {sql_ident(user_name)} LOGIN PASSWORD {sql_literal(password)};\n"
        self._run_permission_sql(sql, root_password=root_password, cancel_event=cancel_event)

    def rename_database_user(
        self,
        current_user_name: str,
        new_user_name: str,
        root_password: str | None = None,
        cancel_event=None,
    ):
        if not current_user_name:
            raise RuntimeError("Nome de usuario atual nao informado.")
        if not new_user_name:
            raise RuntimeError("Novo nome de usuario nao informado.")
        if current_user_name == new_user_name:
            return

        sql = f"ALTER ROLE {sql_ident(current_user_name)} RENAME TO {sql_ident(new_user_name)};\n"
        self._run_permission_sql(sql, root_password=root_password, cancel_event=cancel_event)

    def delete_database_user(
        self,
        user_name: str,
        root_password: str | None = None,
        cancel_event=None,
    ):
        if not user_name:
            raise RuntimeError("Nome de usuario nao informado.")

        sql = f"DROP ROLE {sql_ident(user_name)};\n"
        self._run_permission_sql(sql, root_password=root_password, cancel_event=cancel_event)

    def _available_role_permission_definitions(self, cancel_event=None) -> list[dict]:
        definitions = [dict(item) for item in self.ROLE_PERMISSION_DEFINITIONS]
        predefined_roles = [
            definition["role_name"]
            for definition in definitions
            if definition.get("kind") == "membership"
        ]
        if not predefined_roles:
            return definitions

        roles_sql = ", ".join(sql_literal(role_name) for role_name in predefined_roles)
        sql = f"""
SELECT rolname
FROM pg_roles
WHERE rolname = ANY(ARRAY[{roles_sql}]::name[]);
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        existing_predefined_roles = {row[0] for row in rows if row}

        return [
            definition
            for definition in definitions
            if definition.get("kind") != "membership"
            or definition.get("role_name") in existing_predefined_roles
        ]

    def get_role_permission_state(self, role_name: str, cancel_event=None) -> dict:
        sql = f"""
SELECT
    rolname,
    rolsuper,
    rolcreaterole,
    rolcreatedb,
    rolcanlogin,
    rolreplication,
    rolbypassrls,
    rolinherit
FROM pg_roles
WHERE rolname = {sql_literal(role_name)}
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        if not rows:
            raise RuntimeError(f"Usuario/role {role_name} nao encontrado.")

        row = rows[0]
        role_attributes = {
            "rolsuper": self._is_truthy_sql_value(row[1]),
            "rolcreaterole": self._is_truthy_sql_value(row[2]),
            "rolcreatedb": self._is_truthy_sql_value(row[3]),
            "rolcanlogin": self._is_truthy_sql_value(row[4]),
            "rolreplication": self._is_truthy_sql_value(row[5]),
            "rolbypassrls": self._is_truthy_sql_value(row[6]),
            "rolinherit": self._is_truthy_sql_value(row[7]),
        }

        definitions = self._available_role_permission_definitions(cancel_event=cancel_event)
        assigned_keys = set()
        for definition in definitions:
            if definition.get("kind") == "attribute" and role_attributes.get(definition["attribute"]):
                assigned_keys.add(definition["key"])

        membership_definitions = [
            definition
            for definition in definitions
            if definition.get("kind") == "membership"
        ]
        if membership_definitions:
            values_sql = ", ".join(
                f"({sql_literal(definition['role_name'])})"
                for definition in membership_definitions
            )
            membership_sql = f"""
WITH target_roles(role_name) AS (
    VALUES {values_sql}
)
SELECT role_name, pg_has_role({sql_literal(role_name)}::name, role_name::name, 'member')
FROM target_roles
ORDER BY role_name;
"""
            membership_command = (
                f"psql -h localhost -U {shlex.quote(self.sql_username)} "
                f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(membership_sql)}"
            )
            membership_output = self.run_remote_command(membership_command, cancel_event=cancel_event)
            _membership_headers, membership_rows = self.parse_csv_output(membership_output)
            membership_by_role = {
                row[0]: self._is_truthy_sql_value(row[1])
                for row in membership_rows
                if len(row) >= 2
            }
            for definition in membership_definitions:
                if membership_by_role.get(definition["role_name"]):
                    assigned_keys.add(definition["key"])

        return {
            "role_name": role_name,
            "permissions": definitions,
            "assigned_permission_keys": sorted(assigned_keys),
        }

    @staticmethod
    def is_permission_denied_error(error: Exception | str) -> bool:
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "permission denied",
                "permissao negada",
                "permissÃ£o negada",
                "must be superuser",
                "deve ser superusuario",
                "deve ser superusuÃ¡rio",
                "must have admin option",
                "must have createrole",
                "must have create role",
                "not have privilege",
                "insufficient privilege",
                "privilegio insuficiente",
                "privilÃ©gio insuficiente",
            )
        )

    def _run_permission_sql(self, sql: str, root_password: str | None = None, cancel_event=None):
        if root_password:
            command = (
                "sudo -k -S -p '' -u postgres -- "
                f"psql -d {shlex.quote(CONTROL_DB)} -X -v ON_ERROR_STOP=1"
            )
            self.run_remote_command(
                command,
                stdin_text=f"{root_password}\n{sql}",
                cancel_event=cancel_event,
                timeout_seconds=60,
            )
            return

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event, timeout_seconds=60)

    def apply_role_permission_changes(
        self,
        role_name: str,
        grant_permission_keys=None,
        revoke_permission_keys=None,
        root_password: str | None = None,
        cancel_event=None,
    ):
        definitions_by_key = self._permission_definition_by_key()
        statements = []

        for permission_key in grant_permission_keys or []:
            definition = definitions_by_key.get(permission_key)
            if not definition:
                raise RuntimeError(f"Permissao desconhecida: {permission_key}.")
            if definition["kind"] == "attribute":
                statements.append(
                    f"ALTER ROLE {sql_ident(role_name)} {definition['grant_sql']};"
                )
            elif definition["kind"] == "membership":
                statements.append(
                    f"GRANT {sql_ident(definition['role_name'])} TO {sql_ident(role_name)};"
                )

        for permission_key in revoke_permission_keys or []:
            definition = definitions_by_key.get(permission_key)
            if not definition:
                raise RuntimeError(f"Permissao desconhecida: {permission_key}.")
            if definition["kind"] == "attribute":
                statements.append(
                    f"ALTER ROLE {sql_ident(role_name)} {definition['revoke_sql']};"
                )
            elif definition["kind"] == "membership":
                statements.append(
                    f"REVOKE {sql_ident(definition['role_name'])} FROM {sql_ident(role_name)};"
                )

        if not statements:
            return

        sql = "BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n"
        self._run_permission_sql(sql, root_password=root_password, cancel_event=cancel_event)

    @staticmethod
    def _order_table_columns_for_display(column_names) -> list[str]:
        raw_order = ["raw_hash", "raw_ingested_at", "raw_schema", "raw_tab", "raw_id", "raw"]
        raw_columns = set(raw_order)
        present_raw_columns = [column for column in raw_order if column in column_names]
        if not present_raw_columns:
            return list(column_names)

        first_raw_index = min(
            index
            for index, column in enumerate(column_names)
            if column in raw_columns
        )
        ordered_columns = []
        inserted_raw_columns = False
        for index, column in enumerate(column_names):
            if index == first_raw_index and not inserted_raw_columns:
                ordered_columns.extend(present_raw_columns)
                inserted_raw_columns = True
            if column in raw_columns:
                continue
            ordered_columns.append(column)
        return ordered_columns

    @staticmethod
    def _is_numeric_data_type(data_type: str) -> bool:
        normalized_type = PostgresAdminService._normalize_data_type_name(data_type)
        return normalized_type in {
            "smallint",
            "integer",
            "bigint",
            "decimal",
            "numeric",
            "real",
            "double precision",
        }

    @staticmethod
    def _normalize_numeric_text(value: str) -> str:
        return str(value).strip().replace(",", ".")

    @classmethod
    def _parse_numeric_filter_expression(cls, text: str):
        normalized = str(text or "").strip()
        if not normalized:
            return None

        number_pattern = r"([+-]?\d+(?:[\.,]\d+)?)"
        match = re.fullmatch(rf"MAIOR\s*\(\s*{number_pattern}\s*\)", normalized, flags=re.IGNORECASE)
        if match:
            return {
                "operator": "gt",
                "numeric_value": cls._normalize_numeric_text(match.group(1)),
            }

        match = re.fullmatch(rf"MENOR\s*\(\s*{number_pattern}\s*\)", normalized, flags=re.IGNORECASE)
        if match:
            return {
                "operator": "lt",
                "numeric_value": cls._normalize_numeric_text(match.group(1)),
            }

        match = re.fullmatch(
            rf"ENTRE\s*\(\s*{number_pattern}\s*[,;]\s*{number_pattern}\s*\)",
            normalized,
            flags=re.IGNORECASE,
        )
        if match:
            lower_value = cls._normalize_numeric_text(match.group(1))
            upper_value = cls._normalize_numeric_text(match.group(2))
            return {
                "operator": "between",
                "lower_value": lower_value,
                "upper_value": upper_value,
            }

        if re.fullmatch(number_pattern, normalized):
            return {
                "operator": "eq_numeric",
                "numeric_value": cls._normalize_numeric_text(normalized),
            }

        return None

    def _get_table_columns_info(self, db_name: str, schema_name: str, table_name: str, cancel_event=None) -> list[dict]:
        sql = (
            "SELECT column_name, data_type "
            "FROM information_schema.columns "
            f"WHERE table_schema = {sql_literal(schema_name)} "
            f"AND table_name = {sql_literal(table_name)} "
            "ORDER BY ordinal_position"
        )
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(db_name)} -X --csv -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        _headers, rows = self.parse_csv_output(output)
        if not rows:
            return []
        return [
            {
                "column_name": row[0],
                "data_type": row[1] if len(row) > 1 else "",
                "is_numeric": self._is_numeric_data_type(row[1] if len(row) > 1 else ""),
            }
            for row in rows
            if row
        ]

    def _get_table_column_names(self, db_name: str, schema_name: str, table_name: str, cancel_event=None) -> list[str]:
        return [
            column["column_name"]
            for column in self._get_table_columns_info(db_name, schema_name, table_name, cancel_event=cancel_event)
        ]

    def get_table_columns_info(self, db_name: str, full_table_name: str, cancel_event=None) -> list[dict]:
        schema_name, table_name = split_table_name(full_table_name)
        return self._get_table_columns_info(db_name, schema_name, table_name, cancel_event=cancel_event)

    def get_table_column_facet_rows(
        self,
        db_name: str,
        full_table_name: str,
        column_info: dict,
        cancel_event=None,
    ):
        schema_name, table_name = split_table_name(full_table_name)
        column_name = column_info["column_name"]
        column_sql = sql_ident(column_name)
        numeric_expr = f"{column_sql}::numeric::text" if column_info.get("is_numeric") else "NULL::text"
        sql = f"""
SELECT
    ctid::text AS row_key,
    ({column_sql} IS NULL) AS is_null,
    COALESCE({column_sql}::text, '') AS value_text,
    CASE WHEN {column_sql} IS NULL THEN NULL ELSE {numeric_expr} END AS numeric_value
FROM {sql_ident(schema_name)}.{sql_ident(table_name)}
ORDER BY ctid;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(db_name)} -X --csv -c {shlex.quote(sql)}"
        )

        def row_iterator():
            reader = csv.reader(self.iter_remote_command_lines(command, cancel_event=cancel_event))
            header_seen = False
            for row in reader:
                if not header_seen:
                    header_seen = True
                    continue
                if not row:
                    continue
                yield {
                    "row_key": row[0],
                    "is_null": self._is_truthy_sql_value(row[1]) if len(row) > 1 else False,
                    "value": row[2] if len(row) > 2 else "",
                    "numeric_value": row[3] if len(row) > 3 else None,
                }

        return row_iterator()

    @staticmethod
    def _normalize_filter_rows(
        filters=None,
        excluded_column: str | None = None,
        numeric_columns=None,
    ) -> list[dict]:
        rows = []
        if not filters:
            return rows

        numeric_column_names = set(numeric_columns or [])
        for column_name, selected_values in filters.items():
            if column_name == excluded_column:
                continue

            if selected_values is None:
                continue

            for item in selected_values:
                operator = item.get("operator", "eq")
                if operator == "eq_numeric" and column_name not in numeric_column_names:
                    operator = "eq"
                rows.append({
                    "column_name": column_name,
                    "operator": operator,
                    "is_null": bool(item.get("is_null")),
                    "value_text": "" if item.get("is_null") else str(item.get("value", "")),
                    "numeric_value": item.get("numeric_value"),
                    "lower_value": item.get("lower_value"),
                    "upper_value": item.get("upper_value"),
                })
        return rows

    def _build_filter_payload_cte(
        self,
        filters=None,
        excluded_column: str | None = None,
        numeric_columns=None,
    ) -> str:
        rows = self._normalize_filter_rows(
            filters,
            excluded_column=excluded_column,
            numeric_columns=numeric_columns,
        )
        if not rows:
            return ""

        payload = json.dumps(rows, ensure_ascii=False)
        return (
            "WITH filter_values AS (\n"
            "    SELECT column_name, operator, is_null, value_text, numeric_value, lower_value, upper_value\n"
            f"    FROM jsonb_to_recordset({sql_literal(payload)}::jsonb) "
            "AS item("
            "column_name text, operator text, is_null boolean, value_text text, "
            "numeric_value numeric, lower_value numeric, upper_value numeric"
            ")\n"
            ")\n"
        )

    def _build_table_filter_where_clause(
        self,
        filters=None,
        excluded_column: str | None = None,
        numeric_columns=None,
    ) -> str:
        if not filters:
            return ""

        conditions = []
        numeric_column_names = set(numeric_columns or [])
        for column_name, selected_values in filters.items():
            if column_name == excluded_column or selected_values is None:
                continue
            if not selected_values:
                conditions.append("FALSE")
                continue

            column_sql = sql_ident(column_name)
            column_name_sql = sql_literal(column_name)
            column_conditions = [
                f"({column_sql} IS NULL AND EXISTS ("
                "SELECT 1 FROM filter_values fv "
                f"WHERE fv.column_name = {column_name_sql} "
                "AND COALESCE(fv.operator, 'eq') = 'eq' AND fv.is_null"
                "))",
                "EXISTS ("
                "SELECT 1 FROM filter_values fv "
                f"WHERE fv.column_name = {column_name_sql} "
                "AND COALESCE(fv.operator, 'eq') = 'eq' "
                f"AND NOT fv.is_null AND {column_sql}::text = fv.value_text"
                ")",
            ]
            if column_name in numeric_column_names:
                column_conditions.extend(
                    [
                        "EXISTS ("
                        "SELECT 1 FROM filter_values fv "
                        f"WHERE fv.column_name = {column_name_sql} "
                        f"AND fv.operator = 'eq_numeric' AND {column_sql}::numeric = fv.numeric_value"
                        ")",
                        "EXISTS ("
                        "SELECT 1 FROM filter_values fv "
                        f"WHERE fv.column_name = {column_name_sql} "
                        f"AND fv.operator = 'gt' AND {column_sql}::numeric > fv.numeric_value"
                        ")",
                        "EXISTS ("
                        "SELECT 1 FROM filter_values fv "
                        f"WHERE fv.column_name = {column_name_sql} "
                        f"AND fv.operator = 'lt' AND {column_sql}::numeric < fv.numeric_value"
                        ")",
                        "EXISTS ("
                        "SELECT 1 FROM filter_values fv "
                        f"WHERE fv.column_name = {column_name_sql} "
                        f"AND fv.operator = 'between' AND {column_sql}::numeric BETWEEN fv.lower_value AND fv.upper_value"
                        ")",
                    ]
                )

            conditions.append(
                "("
                + " OR ".join(column_conditions)
                + ")"
            )

        if not conditions:
            return ""

        return "WHERE " + " AND ".join(conditions)

    def get_table_details(
        self,
        db_name: str,
        full_table_name: str,
        filters=None,
        cancel_event=None,
        trace_context: dict | None = None,
    ):
        started_at = time.perf_counter()
        self._trace_table_open(
            trace_context,
            f"get_table_details(): iniciando para {full_table_name}; filters={len(filters or {})}",
        )
        schema_name, pure_table_name = split_table_name(full_table_name)

        header_types = {}
        column_types = {}
        numeric_columns = []
        column_names = []
        columns_started_at = time.perf_counter()
        column_definitions = self.get_table_column_definitions(
            db_name,
            full_table_name,
            cancel_event=cancel_event,
            trace_context=trace_context,
        )
        self._trace_table_open(
            trace_context,
            f"get_table_details(): metadados de colunas carregados em {(time.perf_counter() - columns_started_at) * 1000:.1f} ms; columns={len(column_definitions)}",
        )
        constraint_metadata = self.get_table_column_constraint_metadata(
            db_name,
            full_table_name,
            cancel_event=cancel_event,
            trace_context=trace_context,
        )
        for column_definition in column_definitions:
            column_name = str(column_definition.get("name") or "").strip()
            if not column_name:
                continue

            display_type = self._resolve_dictionary_usage_column_type(
                column_definition.get("type"),
                column_definition.get("default"),
            ) or self._normalize_data_type_name(column_definition.get("type"))
            display_type = str(display_type or column_definition.get("type") or "").strip()
            column_names.append(column_name)
            header_lines = [f"Tipo: {display_type}"]
            constraint_info = constraint_metadata.get(column_name) or {}
            if constraint_info.get("is_primary_key"):
                primary_key_columns = list(constraint_info.get("primary_key_columns") or [])
                if len(primary_key_columns) <= 1:
                    header_lines.append("Primary key")
                else:
                    header_lines.append("Parte da primary key composta: " + ", ".join(primary_key_columns))
            for foreign_key in list(constraint_info.get("foreign_keys") or []):
                reference_label = foreign_key.get("constraint_name") or "foreign key"
                if foreign_key.get("referenced_table_name") and foreign_key.get("referenced_column_name"):
                    reference_label = (
                        f"{foreign_key['referenced_table_name']}.{foreign_key['referenced_column_name']}"
                    )
                cascade_parts = []
                if foreign_key.get("on_update"):
                    cascade_parts.append(f"UPDATE {foreign_key['on_update']}")
                if foreign_key.get("on_delete"):
                    cascade_parts.append(f"DELETE {foreign_key['on_delete']}")
                if int(foreign_key.get("key_count") or 0) > 1:
                    label_prefix = "Parte de foreign key composta"
                else:
                    label_prefix = "Foreign key"
                if cascade_parts:
                    header_lines.append(f"{label_prefix}: {reference_label} ({', '.join(cascade_parts)})")
                else:
                    header_lines.append(f"{label_prefix}: {reference_label}")
            header_types[column_name] = "\n".join(header_lines)
            column_types[column_name] = display_type
            if self._is_numeric_data_type(display_type):
                numeric_columns.append(column_name)

        display_columns = self._order_table_columns_for_display(column_names)
        select_columns = ", ".join(sql_ident(column) for column in display_columns) or "*"
        filter_cte = self._build_filter_payload_cte(filters, numeric_columns=numeric_columns)
        where_clause = self._build_table_filter_where_clause(filters, numeric_columns=numeric_columns)
        data_sql = (
            f"{filter_cte}"
            f"SELECT {select_columns} FROM {sql_ident(schema_name)}.{sql_ident(pure_table_name)} "
            f"{where_clause} "
            "LIMIT 50"
        )
        preview_sql = f"""
BEGIN READ ONLY;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '20s';
COPY (
{data_sql}
) TO STDOUT WITH CSV HEADER;
ROLLBACK;
""".strip() + "\n"
        data_command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(db_name)} -X -q -v ON_ERROR_STOP=1 -f -"
        )
        try:
            data_output = self.run_remote_command(
                data_command,
                stdin_text=preview_sql,
                cancel_event=cancel_event,
                timeout_seconds=25,
                trace_context=trace_context,
                trace_label=f"get_table_details.preview_query[{full_table_name}]",
            )
        except Exception as exc:
            normalized_preview_error = str(exc).lower()
            if "lock timeout" in normalized_preview_error:
                raise RuntimeError(
                    f"A preview da tabela {full_table_name} nao pode ser carregada agora porque a tabela esta bloqueada por outra sessao. "
                    "Conclua, cancele ou finalize a operacao pendente e tente novamente."
                ) from exc
            if "statement timeout" in normalized_preview_error or "excedeu" in normalized_preview_error:
                raise RuntimeError(
                    f"A preview da tabela {full_table_name} demorou demais para responder. "
                    "A tabela pode estar bloqueada ou sob carga pesada; tente novamente em seguida."
                ) from exc
            raise
        parse_started_at = time.perf_counter()
        data_headers, data_rows = self.parse_csv_output(data_output)
        self._trace_table_open(
            trace_context,
            (
                f"get_table_details(): parse da preview em {(time.perf_counter() - parse_started_at) * 1000:.1f} ms; "
                f"headers={len(data_headers)}; rows={len(data_rows)}"
            ),
        )
        dictionary_started_at = time.perf_counter()
        dictionary_links = self.get_table_data_dictionary_links(
            db_name,
            full_table_name,
            cancel_event=cancel_event,
            trace_context=trace_context,
        )
        self._trace_table_open(
            trace_context,
            f"get_table_details(): dictionary links carregados em {(time.perf_counter() - dictionary_started_at) * 1000:.1f} ms; links={len(dictionary_links)}",
        )

        result = {
            "data_headers": data_headers,
            "data_rows": data_rows,
            "header_types": header_types,
            "column_types": column_types,
            "numeric_columns": numeric_columns,
            "column_constraints": constraint_metadata,
            "dictionary_links": dictionary_links,
        }
        self._trace_table_open(
            trace_context,
            f"get_table_details(): conclu?do em {(time.perf_counter() - started_at) * 1000:.1f} ms",
        )
        return result

    def create_database(self, db_name: str):
        sql = f"CREATE DATABASE {sql_ident(db_name)} OWNER {sql_ident(self.sql_username)};"
        cmd = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(cmd)
        self.ensure_data_database_metadata(db_name)

    def create_table(self, database_name: str, full_table_name: str):
        schema_name, table_name = split_table_name(full_table_name)
        self.ensure_data_database_metadata(database_name)

        create_schema_sql = f"CREATE SCHEMA IF NOT EXISTS {sql_ident(schema_name)};"
        create_table_sql = f"CREATE TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} ();"
        initialize_counter_sql = self._build_table_row_count_initialize_sql(schema_name, table_name)
        sql = (
            "BEGIN;\n"
            "SET LOCAL client_min_messages = warning;\n"
            f"{create_schema_sql}\n"
            f"{create_table_sql}\n"
            f"{initialize_counter_sql}\n"
            "COMMIT;\n"
        )

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -q -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql)

        return {
            "schema_name": schema_name,
            "table_name": table_name,
            "sql_recipe": f"{create_schema_sql}\n{create_table_sql}\n{initialize_counter_sql}",
        }

    @staticmethod
    def _read_dollar_quote_tag(sql_text: str, start_index: int) -> str | None:
        if start_index < 0 or start_index >= len(sql_text) or sql_text[start_index] != "$":
            return None

        end_index = sql_text.find("$", start_index + 1)
        if end_index == -1:
            return None

        candidate = sql_text[start_index:end_index + 1]
        if candidate == "$$" or re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*\$", candidate):
            return candidate
        return None

    @classmethod
    def _split_sql_statements(cls, sql_text: str) -> list[str]:
        statements = []
        current = []
        index = 0
        text_length = len(sql_text)
        in_single_quote = False
        in_double_quote = False
        line_comment = False
        block_comment_depth = 0
        dollar_tag = None

        while index < text_length:
            current_char = sql_text[index]
            next_char = sql_text[index + 1] if index + 1 < text_length else ""

            if line_comment:
                current.append(current_char)
                if current_char == "\n":
                    line_comment = False
                index += 1
                continue

            if block_comment_depth > 0:
                current.append(current_char)
                if current_char == "/" and next_char == "*":
                    current.append(next_char)
                    block_comment_depth += 1
                    index += 2
                    continue
                if current_char == "*" and next_char == "/":
                    current.append(next_char)
                    block_comment_depth -= 1
                    index += 2
                    continue
                index += 1
                continue

            if dollar_tag is not None:
                if sql_text.startswith(dollar_tag, index):
                    current.append(dollar_tag)
                    index += len(dollar_tag)
                    dollar_tag = None
                    continue
                current.append(current_char)
                index += 1
                continue

            if in_single_quote:
                current.append(current_char)
                if current_char == "'" and next_char == "'":
                    current.append(next_char)
                    index += 2
                    continue
                if current_char == "'":
                    in_single_quote = False
                index += 1
                continue

            if in_double_quote:
                current.append(current_char)
                if current_char == '"' and next_char == '"':
                    current.append(next_char)
                    index += 2
                    continue
                if current_char == '"':
                    in_double_quote = False
                index += 1
                continue

            if current_char == "-" and next_char == "-":
                current.append(current_char)
                current.append(next_char)
                line_comment = True
                index += 2
                continue

            if current_char == "/" and next_char == "*":
                current.append(current_char)
                current.append(next_char)
                block_comment_depth = 1
                index += 2
                continue

            dollar_candidate = cls._read_dollar_quote_tag(sql_text, index)
            if dollar_candidate:
                current.append(dollar_candidate)
                index += len(dollar_candidate)
                dollar_tag = dollar_candidate
                continue

            if current_char == "'":
                current.append(current_char)
                in_single_quote = True
                index += 1
                continue

            if current_char == '"':
                current.append(current_char)
                in_double_quote = True
                index += 1
                continue

            if current_char == ";":
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
                index += 1
                continue

            current.append(current_char)
            index += 1

        tail_statement = "".join(current).strip()
        if tail_statement:
            statements.append(tail_statement)
        return statements

    @classmethod
    def _mask_sql_literals_and_comments(cls, sql_text: str) -> str:
        masked = []
        index = 0
        text_length = len(sql_text)
        in_single_quote = False
        in_double_quote = False
        line_comment = False
        block_comment_depth = 0
        dollar_tag = None

        while index < text_length:
            current_char = sql_text[index]
            next_char = sql_text[index + 1] if index + 1 < text_length else ""

            if line_comment:
                masked.append("\n" if current_char == "\n" else " ")
                if current_char == "\n":
                    line_comment = False
                index += 1
                continue

            if block_comment_depth > 0:
                masked.append("\n" if current_char == "\n" else " ")
                if current_char == "/" and next_char == "*":
                    masked.append(" ")
                    block_comment_depth += 1
                    index += 2
                    continue
                if current_char == "*" and next_char == "/":
                    masked.append(" ")
                    block_comment_depth -= 1
                    index += 2
                    continue
                index += 1
                continue

            if dollar_tag is not None:
                if sql_text.startswith(dollar_tag, index):
                    masked.extend(" " * len(dollar_tag))
                    index += len(dollar_tag)
                    dollar_tag = None
                    continue
                masked.append("\n" if current_char == "\n" else " ")
                index += 1
                continue

            if in_single_quote:
                masked.append("\n" if current_char == "\n" else " ")
                if current_char == "'" and next_char == "'":
                    masked.append(" ")
                    index += 2
                    continue
                if current_char == "'":
                    in_single_quote = False
                index += 1
                continue

            if in_double_quote:
                masked.append("\n" if current_char == "\n" else " ")
                if current_char == '"' and next_char == '"':
                    masked.append(" ")
                    index += 2
                    continue
                if current_char == '"':
                    in_double_quote = False
                index += 1
                continue

            if current_char == "-" and next_char == "-":
                masked.append(" ")
                masked.append(" ")
                line_comment = True
                index += 2
                continue

            if current_char == "/" and next_char == "*":
                masked.append(" ")
                masked.append(" ")
                block_comment_depth = 1
                index += 2
                continue

            dollar_candidate = cls._read_dollar_quote_tag(sql_text, index)
            if dollar_candidate:
                masked.extend(" " * len(dollar_candidate))
                index += len(dollar_candidate)
                dollar_tag = dollar_candidate
                continue

            if current_char == "'":
                masked.append(" ")
                in_single_quote = True
                index += 1
                continue

            if current_char == '"':
                masked.append(" ")
                in_double_quote = True
                index += 1
                continue

            masked.append(current_char)
            index += 1

        return "".join(masked)

    @classmethod
    def _classify_readonly_query(cls, sql_text: str) -> dict:
        statements = cls._split_sql_statements(sql_text or "")
        non_empty_statements = [statement for statement in statements if statement.strip()]
        if not non_empty_statements:
            raise RuntimeError("Digite um SQL para consultar.")
        if len(non_empty_statements) != 1:
            raise RuntimeError("Consulta aceita apenas uma unica instrucao SQL por vez.")

        statement = non_empty_statements[0].strip()
        masked = cls._mask_sql_literals_and_comments(statement)
        normalized = re.sub(r"\s+", " ", masked).strip().lower()
        if not normalized:
            raise RuntimeError("Digite um SQL para consultar.")
        if re.search(r"(^|[\s])\\", masked, flags=re.MULTILINE):
            raise RuntimeError("A opcao Consulta nao aceita comandos internos do psql.")

        first_keyword_match = re.match(r"([a-z]+)", normalized)
        first_keyword = first_keyword_match.group(1) if first_keyword_match else ""
        allowed_first_keywords = {"select", "with", "values", "table"}
        if first_keyword not in allowed_first_keywords:
            raise RuntimeError(
                "A opcao Consulta aceita apenas consultas de leitura iniciadas por SELECT, WITH, VALUES ou TABLE."
            )

        forbidden_keywords = {
            "insert",
            "update",
            "delete",
            "merge",
            "alter",
            "drop",
            "truncate",
            "create",
            "grant",
            "revoke",
            "comment",
            "copy",
            "refresh",
            "vacuum",
            "analyze",
            "call",
            "do",
            "lock",
            "checkpoint",
            "cluster",
            "reindex",
            "listen",
            "notify",
            "unlisten",
            "set",
            "reset",
            "begin",
            "start",
            "commit",
            "rollback",
            "savepoint",
            "release",
            "prepare",
            "execute",
            "deallocate",
            "discard",
            "security",
            "into",
        }
        keyword_pattern = r"\b(" + "|".join(sorted(forbidden_keywords)) + r")\b"
        matched_keyword = re.search(keyword_pattern, normalized)
        if matched_keyword:
            raise RuntimeError(
                f"A opcao Consulta bloqueou a instrucao porque encontrou o termo {matched_keyword.group(1).upper()}."
            )

        return {
            "statement": statement.rstrip().rstrip(";").strip(),
            "query_kind": first_keyword,
        }

    def execute_readonly_query(
        self,
        database_name: str,
        sql_script: str,
        cancel_event=None,
        progress_callback=None,
        preview_limit: int = 500,
    ):
        normalized_sql = (sql_script or "").strip()
        query_info = self._classify_readonly_query(normalized_sql)
        query_statement = query_info["statement"]
        query_kind = query_info["query_kind"]
        fetch_limit = max(int(preview_limit or 0), 1)
        copy_limit = fetch_limit + 1

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "prepare", 10, "Consulta validada. Preparando sessao SQL somente leitura...")

        wrapped_sql = f"""
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5min';
COPY (
    SELECT *
    FROM (
{query_statement}
    ) AS pgdm_query_preview
    LIMIT {copy_limit}
) TO STDOUT WITH CSV HEADER;
ROLLBACK;
""".strip() + "\n"

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -q -v ON_ERROR_STOP=1 -f -"
        )

        self._notify_progress(progress_callback, "execute", 5, "Executando consulta em transacao somente leitura...")
        output = self.run_remote_command(
            command,
            stdin_text=wrapped_sql,
            cancel_event=cancel_event,
            stdin_progress_callback=lambda progress, message: self._notify_progress(
                progress_callback,
                "execute",
                10 + (progress * 0.55),
                message,
            ),
        )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "result", 60, "Lendo resultado da consulta...")
        headers, rows = self.parse_csv_output(output)
        truncated = len(rows) > fetch_limit
        if truncated:
            rows = rows[:fetch_limit]

        if truncated:
            message = (
                f"Mostrando as primeiras {fetch_limit} linhas retornadas pela consulta. "
                "Use filtros ou LIMIT no SQL para reduzir o resultado."
            )
        else:
            message = f"Consulta concluida com {len(rows)} linha(s) retornada(s)."

        self._notify_progress(progress_callback, "result", 100, "Resultado pronto para exibicao.")
        return {
            "headers": headers,
            "rows": rows,
            "query_kind": query_kind,
            "displayed_row_count": len(rows),
            "truncated": truncated,
            "message": message,
        }

    @classmethod
    def _tokenize_managed_sql(cls, sql_text: str) -> list[tuple[str, str, int]]:
        tokens = []
        index = 0
        text_length = len(sql_text)
        depth = 0

        while index < text_length:
            current_char = sql_text[index]
            next_char = sql_text[index + 1] if index + 1 < text_length else ""

            if current_char.isspace():
                index += 1
                continue

            if current_char == "-" and next_char == "-":
                newline_index = sql_text.find("\n", index + 2)
                index = text_length if newline_index == -1 else newline_index + 1
                continue

            if current_char == "/" and next_char == "*":
                block_depth = 1
                index += 2
                while index < text_length and block_depth > 0:
                    next_char = sql_text[index + 1] if index + 1 < text_length else ""
                    if sql_text[index] == "/" and next_char == "*":
                        block_depth += 1
                        index += 2
                    elif sql_text[index] == "*" and next_char == "/":
                        block_depth -= 1
                        index += 2
                    else:
                        index += 1
                if block_depth:
                    raise RuntimeError("O SQL contem um comentario de bloco nao finalizado.")
                continue

            if current_char == "'":
                index += 1
                while index < text_length:
                    next_char = sql_text[index + 1] if index + 1 < text_length else ""
                    if sql_text[index] == "'" and next_char == "'":
                        index += 2
                        continue
                    if sql_text[index] == "'":
                        index += 1
                        break
                    index += 1
                else:
                    raise RuntimeError("O SQL contem um texto entre aspas simples nao finalizado.")
                continue

            dollar_tag = cls._read_dollar_quote_tag(sql_text, index)
            if dollar_tag:
                closing_index = sql_text.find(dollar_tag, index + len(dollar_tag))
                if closing_index == -1:
                    raise RuntimeError("O SQL contem um bloco com dollar quote nao finalizado.")
                index = closing_index + len(dollar_tag)
                continue

            if current_char == '"':
                identifier = []
                index += 1
                while index < text_length:
                    next_char = sql_text[index + 1] if index + 1 < text_length else ""
                    if sql_text[index] == '"' and next_char == '"':
                        identifier.append('"')
                        index += 2
                        continue
                    if sql_text[index] == '"':
                        index += 1
                        break
                    identifier.append(sql_text[index])
                    index += 1
                else:
                    raise RuntimeError("O SQL contem um identificador entre aspas nao finalizado.")
                tokens.append(("identifier", "".join(identifier), depth))
                continue

            if current_char == "(":
                tokens.append(("symbol", current_char, depth))
                depth += 1
                index += 1
                continue

            if current_char == ")":
                if depth == 0:
                    raise RuntimeError("O SQL contem parenteses nao balanceados.")
                depth -= 1
                tokens.append(("symbol", current_char, depth))
                index += 1
                continue

            if current_char.isalpha() or current_char == "_":
                end_index = index + 1
                while end_index < text_length and (
                    sql_text[end_index].isalnum() or sql_text[end_index] in {"_", "$"}
                ):
                    end_index += 1
                tokens.append(("word", sql_text[index:end_index].lower(), depth))
                index = end_index
                continue

            if current_char.isdigit():
                end_index = index + 1
                while end_index < text_length and (
                    sql_text[end_index].isdigit() or sql_text[end_index] in {".", "e", "E", "+", "-"}
                ):
                    end_index += 1
                tokens.append(("number", sql_text[index:end_index], depth))
                index = end_index
                continue

            tokens.append(("symbol", current_char, depth))
            index += 1

        if depth:
            raise RuntimeError("O SQL contem parenteses nao balanceados.")
        return tokens

    @staticmethod
    def _managed_sql_parse_relation(
        tokens: list[tuple[str, str, int]],
        index: int,
        default_schema: str,
    ) -> tuple[str, str, int, bool]:
        if index >= len(tokens) or tokens[index][0] not in {"word", "identifier"}:
            raise RuntimeError("Nao foi possivel identificar a tabela alvo da instrucao SQL.")

        first_name = tokens[index][1]
        index += 1
        if (
            index + 1 < len(tokens)
            and tokens[index][0] == "symbol"
            and tokens[index][1] == "."
            and tokens[index + 1][0] in {"word", "identifier"}
        ):
            return first_name, tokens[index + 1][1], index + 2, True
        return default_schema, first_name, index, False

    @staticmethod
    def _managed_sql_assert_selected_relation(
        parsed_schema: str,
        parsed_table: str,
        selected_schema: str,
        selected_table: str,
        was_qualified: bool,
    ):
        if not was_qualified and selected_schema != "public":
            raise RuntimeError(
                "Para tabelas fora do schema public, informe o nome completo schema.tabela no SQL."
            )
        if parsed_schema != selected_schema or parsed_table != selected_table:
            raise RuntimeError(
                "O botao SQL so pode alterar a tabela selecionada "
                f"({selected_schema}.{selected_table})."
            )

    @staticmethod
    def _managed_sql_top_level_words(tokens: list[tuple[str, str, int]]) -> list[str]:
        return [
            value
            for kind, value, depth in tokens
            if kind == "word" and depth == 0
        ]

    @classmethod
    def _classify_managed_sql_statement(
        cls,
        statement: str,
        selected_schema: str,
        selected_table: str,
    ) -> dict:
        tokens = cls._tokenize_managed_sql(statement)
        if not tokens:
            raise RuntimeError("O SQL contem uma instrucao vazia ou apenas comentarios.")
        if any(kind == "symbol" and value == "\\" for kind, value, _depth in tokens):
            raise RuntimeError("Rodar SQL nao aceita comandos internos do psql iniciados por barra invertida.")
        if any(
            kind in {"word", "identifier"} and value.lower() == DATA_TABLE_ROW_COUNTS_NAME
            for kind, value, _depth in tokens
        ):
            raise RuntimeError(
                f"A tabela interna {DATA_TABLE_ROW_COUNTS_NAME} e protegida e nao pode ser alterada pelo editor SQL."
            )

        first_kind, first_keyword, first_depth = tokens[0]
        if first_kind != "word" or first_depth != 0:
            raise RuntimeError("Nao foi possivel identificar o comando SQL informado.")

        top_level_words = cls._managed_sql_top_level_words(tokens)
        top_level_word_set = set(top_level_words)
        target_schema = selected_schema
        target_table = selected_table
        target_was_qualified = False
        action = "execute"

        if first_keyword in {"select", "values", "table"}:
            raise RuntimeError("Use o botao Consulta para instrucoes de leitura.")

        if first_keyword == "with":
            raise RuntimeError(
                "Rodar SQL nao aceita instrucoes iniciadas por WITH, pois elas podem esconder "
                "alteracoes de quantidade. Reescreva a operacao como INSERT, UPDATE ou DELETE direto."
            )

        if first_keyword == "insert":
            index = 1
            if index >= len(tokens) or tokens[index][0] != "word" or tokens[index][1] != "into":
                raise RuntimeError("Use a forma INSERT INTO schema.tabela para inserir dados.")
            target_schema, target_table, _index, target_was_qualified = cls._managed_sql_parse_relation(
                tokens,
                index + 1,
                selected_schema,
            )
            action = "add"
            if "returning" in top_level_word_set:
                raise RuntimeError(
                    "RETURNING nao e aceito em Rodar SQL. Remova essa clausula; o botao registra "
                    "internamente a quantidade inserida."
                )
            if "conflict" in top_level_word_set:
                conflict_index = top_level_words.index("conflict")
                if "update" in top_level_words[conflict_index + 1:]:
                    raise RuntimeError(
                        "INSERT ... ON CONFLICT DO UPDATE e ambiguo para o contador. "
                        "Separe insercoes e atualizacoes em comandos distintos."
                    )

        elif first_keyword == "delete":
            index = 1
            if index >= len(tokens) or tokens[index][0] != "word" or tokens[index][1] != "from":
                raise RuntimeError("Use a forma DELETE FROM schema.tabela para excluir linhas.")
            index += 1
            if index < len(tokens) and tokens[index][0] == "word" and tokens[index][1] == "only":
                index += 1
            target_schema, target_table, _index, target_was_qualified = cls._managed_sql_parse_relation(
                tokens,
                index,
                selected_schema,
            )
            action = "subtract"
            if "returning" in top_level_word_set:
                raise RuntimeError(
                    "RETURNING nao e aceito em Rodar SQL. Remova essa clausula; o botao registra "
                    "internamente a quantidade excluida."
                )

        elif first_keyword == "update":
            index = 1
            if index < len(tokens) and tokens[index][0] == "word" and tokens[index][1] == "only":
                index += 1
            target_schema, target_table, _index, target_was_qualified = cls._managed_sql_parse_relation(
                tokens,
                index,
                selected_schema,
            )
            if "returning" in top_level_word_set:
                raise RuntimeError(
                    "RETURNING nao e aceito em Rodar SQL porque o resultado nao e exibido. "
                    "Remova essa clausula."
                )

        elif first_keyword == "truncate":
            index = 1
            if index < len(tokens) and tokens[index][0] == "word" and tokens[index][1] == "table":
                index += 1
            if index < len(tokens) and tokens[index][0] == "word" and tokens[index][1] == "only":
                index += 1
            target_schema, target_table, relation_end, target_was_qualified = cls._managed_sql_parse_relation(
                tokens,
                index,
                selected_schema,
            )
            if any(
                kind == "symbol" and value == "," and depth == 0
                for kind, value, depth in tokens[relation_end:]
            ):
                raise RuntimeError("TRUNCATE deve mencionar somente a tabela selecionada.")
            if "cascade" in top_level_word_set:
                raise RuntimeError(
                    "TRUNCATE CASCADE foi bloqueado porque pode esvaziar outras tabelas sem atualizar seus contadores."
                )
            action = "set_zero"

        elif first_keyword == "alter":
            if len(tokens) < 3 or tokens[1][0] != "word" or tokens[1][1] != "table":
                raise RuntimeError("Rodar SQL aceita ALTER somente na forma ALTER TABLE.")
            index = 2
            if (
                index + 1 < len(tokens)
                and tokens[index][0] == "word"
                and tokens[index][1] == "if"
                and tokens[index + 1][0] == "word"
                and tokens[index + 1][1] == "exists"
            ):
                index += 2
            if index < len(tokens) and tokens[index][0] == "word" and tokens[index][1] == "only":
                index += 1
            target_schema, target_table, _index, target_was_qualified = cls._managed_sql_parse_relation(
                tokens,
                index,
                selected_schema,
            )
            blocked_alter_words = {
                "rename",
                "schema",
                "inherit",
                "partition",
                "trigger",
            }
            found_blocked_words = sorted(blocked_alter_words & top_level_word_set)
            if found_blocked_words:
                raise RuntimeError(
                    "ALTER TABLE foi bloqueado porque contem operacao estrutural que pode "
                    "invalidar o alvo ou o contador: "
                    + ", ".join(word.upper() for word in found_blocked_words)
                    + "."
                )

        elif first_keyword == "comment":
            if len(tokens) < 4 or tokens[1][0] != "word" or tokens[1][1] != "on":
                raise RuntimeError("Use COMMENT ON TABLE ou COMMENT ON COLUMN na tabela selecionada.")
            object_kind = tokens[2][1] if tokens[2][0] == "word" else ""
            try:
                is_index = next(
                    index
                    for index in range(3, len(tokens))
                    if tokens[index][0] == "word" and tokens[index][1] == "is" and tokens[index][2] == 0
                )
            except StopIteration as exc:
                raise RuntimeError("Nao foi possivel identificar o alvo do COMMENT.") from exc
            name_parts = [
                value
                for kind, value, depth in tokens[3:is_index]
                if kind in {"word", "identifier"} and depth == 0
            ]
            if object_kind == "table" and len(name_parts) in {1, 2}:
                if len(name_parts) == 1:
                    target_schema, target_table = selected_schema, name_parts[0]
                else:
                    target_schema, target_table = name_parts
                    target_was_qualified = True
            elif object_kind == "column" and len(name_parts) in {2, 3}:
                if len(name_parts) == 2:
                    target_schema, target_table = selected_schema, name_parts[0]
                else:
                    target_schema, target_table = name_parts[:2]
                    target_was_qualified = True
            else:
                raise RuntimeError("COMMENT deve apontar para a tabela selecionada ou para uma coluna dela.")

        elif first_keyword == "create":
            index = 1
            if index < len(tokens) and tokens[index][0] == "word" and tokens[index][1] == "unique":
                index += 1
            if index >= len(tokens) or tokens[index][0] != "word" or tokens[index][1] != "index":
                raise RuntimeError("Rodar SQL aceita CREATE somente para indices da tabela selecionada.")
            if "concurrently" in top_level_word_set:
                raise RuntimeError(
                    "CREATE INDEX CONCURRENTLY nao pode ser executado dentro da transacao automatica do botao SQL."
                )
            try:
                on_index = next(
                    token_index
                    for token_index in range(index + 1, len(tokens))
                    if tokens[token_index][0] == "word"
                    and tokens[token_index][1] == "on"
                    and tokens[token_index][2] == 0
                )
            except StopIteration as exc:
                raise RuntimeError("Nao foi possivel identificar a tabela do CREATE INDEX.") from exc
            relation_index = on_index + 1
            if (
                relation_index < len(tokens)
                and tokens[relation_index][0] == "word"
                and tokens[relation_index][1] == "only"
            ):
                relation_index += 1
            target_schema, target_table, _index, target_was_qualified = cls._managed_sql_parse_relation(
                tokens,
                relation_index,
                selected_schema,
            )

        else:
            blocked_commands = {
                "begin",
                "start",
                "commit",
                "rollback",
                "savepoint",
                "release",
                "end",
                "merge",
                "copy",
                "do",
                "call",
                "prepare",
                "execute",
                "deallocate",
                "set",
                "reset",
                "discard",
                "drop",
                "grant",
                "revoke",
                "vacuum",
                "analyze",
                "cluster",
                "reindex",
                "refresh",
                "lock",
                "checkpoint",
                "listen",
                "notify",
                "unlisten",
            }
            if first_keyword in blocked_commands:
                raise RuntimeError(
                    f"O comando {first_keyword.upper()} nao e aceito pelo modo seguro do botao SQL. "
                    "A transacao e o contador sao administrados automaticamente."
                )
            raise RuntimeError(
                "Comando nao reconhecido pelo modo seguro do botao SQL. "
                "Sao aceitos INSERT, UPDATE, DELETE, TRUNCATE, ALTER TABLE, "
                "CREATE INDEX e COMMENT na tabela selecionada."
            )

        cls._managed_sql_assert_selected_relation(
            target_schema,
            target_table,
            selected_schema,
            selected_table,
            target_was_qualified,
        )
        return {
            "statement": statement.rstrip().rstrip(";").strip(),
            "command": first_keyword,
            "action": action,
        }

    @staticmethod
    def _build_managed_dml_sql(
        statement: str,
        schema_name: str,
        table_name: str,
        action: str,
        timing_sequence: int | None = None,
        timing_stage: str | None = None,
        timing_name: str | None = None,
        timing_destination: str | None = None,
        timing_statement_index: int | None = None,
    ) -> str:
        tag_base = "pgdm_managed_sql"
        tag = f"${tag_base}$"
        suffix = 0
        while tag in statement:
            suffix += 1
            tag = f"${tag_base}_{suffix}$"

        if action not in {"add", "subtract"}:
            raise ValueError("Acao de contador invalida para DML gerenciado.")
        operator = "+" if action == "add" else "-"
        action_description = "insercao" if action == "add" else "exclusao"
        consistency_condition = (
            "\n      AND row_count >= pgdm_affected_rows"
            if action == "subtract"
            else ""
        )
        missing_counter_message = (
            "Contador ausente ou menor que a quantidade excluida"
            if action == "subtract"
            else "Contador ausente"
        )
        timing_declaration = ""
        timing_insert = ""
        if timing_sequence is not None and timing_stage and timing_name:
            timing_declaration = (
                "\n    pgdm_timing_started_at timestamptz := clock_timestamp();"
            )
            timing_insert = f"""

    INSERT INTO pg_temp.pgdm_expansion_timings (
        sequence_number,
        stage_key,
        item_name,
        started_at,
        finished_at,
        rows_affected,
        destination_name,
        statement_index
    ) VALUES (
        {int(timing_sequence)},
        {sql_literal(timing_stage)},
        {sql_literal(timing_name)},
        pgdm_timing_started_at,
        clock_timestamp(),
        pgdm_affected_rows,
        {sql_literal(timing_destination) if timing_destination else 'NULL'},
        {int(timing_statement_index) if timing_statement_index is not None else 'NULL'}
    );"""
        return f"""
DO {tag}
DECLARE
    pgdm_affected_rows bigint;{timing_declaration}
BEGIN
    {statement}
    ;
    GET DIAGNOSTICS pgdm_affected_rows = ROW_COUNT;

    UPDATE {DATA_TABLE_ROW_COUNTS}
    SET row_count = row_count {operator} pgdm_affected_rows,
        updated_at = clock_timestamp()
    WHERE schema_name = {sql_literal(schema_name)}
      AND table_name = {sql_literal(table_name)}{consistency_condition};

    IF NOT FOUND THEN
        RAISE EXCEPTION
            '{missing_counter_message} para %.% durante {action_description}.',
            {sql_literal(schema_name)},
            {sql_literal(table_name)};
    END IF;
{timing_insert}
END
{tag};
""".strip()

    @classmethod
    def _prepare_managed_sql_script(
        cls,
        sql_script: str,
        full_table_name: str,
    ) -> dict:
        normalized_sql = (sql_script or "").strip()
        if not normalized_sql:
            raise RuntimeError("Digite um SQL para executar.")

        schema_name, table_name = split_table_name(full_table_name)
        statements = cls._split_sql_statements(normalized_sql)
        if not statements:
            raise RuntimeError("Digite um SQL para executar.")

        prepared_statements = []
        classifications = []
        for statement in statements:
            if not cls._tokenize_managed_sql(statement):
                continue
            classification = cls._classify_managed_sql_statement(
                statement,
                schema_name,
                table_name,
            )
            action = classification["action"]
            if action in {"add", "subtract"}:
                prepared_statement = cls._build_managed_dml_sql(
                    classification["statement"],
                    schema_name,
                    table_name,
                    action,
                )
            elif action == "set_zero":
                prepared_statement = (
                    f"{classification['statement']}\n;\n"
                    + cls._build_table_row_count_set_sql(schema_name, table_name, "0")
                )
            else:
                prepared_statement = f"{classification['statement']}\n;"
            prepared_statements.append(prepared_statement)
            classifications.append(classification)

        if not classifications:
            raise RuntimeError("Digite ao menos uma instrucao SQL para executar.")

        wrapped_sql = (
            "BEGIN;\n"
            "SET LOCAL client_min_messages = warning;\n"
            + "\n\n".join(prepared_statements)
            + "\nCOMMIT;\n"
        )
        return {
            "sql_recipe": normalized_sql,
            "wrapped_sql": wrapped_sql,
            "classifications": classifications,
            "schema_name": schema_name,
            "table_name": table_name,
        }

    @classmethod
    def inspect_managed_sql_script(
        cls,
        sql_script: str,
        full_table_name: str,
    ) -> dict:
        prepared = cls._prepare_managed_sql_script(sql_script, full_table_name)
        commands = sorted(
            {
                str(item.get("command") or "").strip().lower()
                for item in prepared["classifications"]
                if str(item.get("command") or "").strip()
            }
        )
        return {
            "commands": commands,
            "contains_delete": "delete" in commands,
        }

    def _validate_managed_sql_dependencies(
        self,
        database_name: str,
        full_table_name: str,
        commands: set[str],
        delete_relationship_acknowledged: bool = False,
        cancel_event=None,
    ):
        dml_commands = commands & {"insert", "update", "delete", "truncate"}
        if not dml_commands:
            return

        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT json_build_object(
    'relation_kind', target.relkind,
    'is_partition', target.relispartition,
    'row_security', target.relrowsecurity,
    'triggers', COALESCE((
        SELECT json_agg(trigger_data.tgname ORDER BY trigger_data.tgname)
        FROM pg_trigger AS trigger_data
        WHERE trigger_data.tgrelid = target.oid
          AND NOT trigger_data.tgisinternal
    ), '[]'::json),
    'rules', COALESCE((
        SELECT json_agg(rule_data.rulename ORDER BY rule_data.rulename)
        FROM pg_rewrite AS rule_data
        WHERE rule_data.ev_class = target.oid
          AND rule_data.rulename <> '_RETURN'
    ), '[]'::json),
    'inheritance_children', COALESCE((
        SELECT json_agg(
            format('%I.%I', child_namespace.nspname, child.relname)
            ORDER BY child_namespace.nspname, child.relname
        )
        FROM pg_inherits AS inheritance
        JOIN pg_class AS child ON child.oid = inheritance.inhrelid
        JOIN pg_namespace AS child_namespace ON child_namespace.oid = child.relnamespace
        WHERE inheritance.inhparent = target.oid
    ), '[]'::json),
    'delete_side_effect_tables', COALESCE((
        SELECT json_agg(
            json_build_object(
                'table_name',
                format(
                    '%I.%I',
                    delete_dependency.schema_name,
                    delete_dependency.table_name
                ),
                'delete_action', CASE delete_dependency.delete_action
                    WHEN 'c' THEN 'CASCADE'
                    WHEN 'n' THEN 'SET NULL'
                    WHEN 'd' THEN 'SET DEFAULT'
                    ELSE delete_dependency.delete_action::text
                END,
                'row_count',
                child_counter.row_count
            )
            ORDER BY
                delete_dependency.schema_name,
                delete_dependency.table_name,
                delete_dependency.delete_action
        )
        FROM (
            SELECT DISTINCT
                child_namespace.nspname AS schema_name,
                child.relname AS table_name,
                constraint_data.confdeltype AS delete_action
            FROM pg_constraint AS constraint_data
            JOIN pg_class AS child ON child.oid = constraint_data.conrelid
            JOIN pg_namespace AS child_namespace ON child_namespace.oid = child.relnamespace
            WHERE constraint_data.confrelid = target.oid
              AND constraint_data.contype = 'f'
              AND constraint_data.confdeltype IN ('c', 'n', 'd')
        ) AS delete_dependency
        LEFT JOIN {DATA_TABLE_ROW_COUNTS} AS child_counter
          ON child_counter.schema_name = delete_dependency.schema_name
         AND child_counter.table_name = delete_dependency.table_name
    ), '[]'::json)
)::text
FROM pg_class AS target
JOIN pg_namespace AS target_namespace ON target_namespace.oid = target.relnamespace
WHERE target_namespace.nspname = {sql_literal(schema_name)}
  AND target.relname = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -v ON_ERROR_STOP=1 -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        if not output:
            raise RuntimeError(f"A tabela selecionada {schema_name}.{table_name} nao existe.")

        metadata = json.loads(output.splitlines()[-1])
        if metadata.get("relation_kind") != "r" or metadata.get("is_partition"):
            raise RuntimeError(
                "O contador automatico do botao SQL aceita alteracao de linhas apenas em tabelas comuns, "
                "nao em views ou tabelas particionadas."
            )

        triggers = metadata.get("triggers") or []
        if triggers:
            raise RuntimeError(
                "A operacao foi bloqueada porque a tabela possui gatilho(s) que podem alterar "
                "outras tabelas sem atualizar seus contadores: "
                + ", ".join(triggers)
                + "."
            )

        rules = metadata.get("rules") or []
        if rules:
            raise RuntimeError(
                "A operacao foi bloqueada porque a tabela possui regra(s) de reescrita "
                "que podem alterar outras tabelas: "
                + ", ".join(rules)
                + "."
            )

        if metadata.get("row_security"):
            raise RuntimeError(
                "A operacao foi bloqueada porque a tabela usa seguranca em nivel de linha. "
                "As politicas podem executar funcoes fora do escopo protegido."
            )

        inheritance_children = metadata.get("inheritance_children") or []
        if inheritance_children:
            raise RuntimeError(
                "A operacao foi bloqueada porque a tabela possui tabelas filhas/particoes: "
                + ", ".join(inheritance_children)
                + "."
            )

        delete_side_effect_dependencies = (
            metadata.get("delete_side_effect_tables")
            or metadata.get("delete_cascade_tables")
            or []
        )
        unsafe_delete_side_effects = []
        for dependency in delete_side_effect_dependencies:
            if isinstance(dependency, dict):
                dependency_name = str(dependency.get("table_name") or "tabela desconhecida")
                delete_action = str(dependency.get("delete_action") or "CASCADE").strip().upper()
                dependency_row_count = dependency.get("row_count")
                try:
                    normalized_row_count = int(dependency_row_count)
                except (TypeError, ValueError):
                    normalized_row_count = None
                if normalized_row_count is None or normalized_row_count > 0:
                    unsafe_delete_side_effects.append(
                        f"{dependency_name} (ON DELETE {delete_action})"
                    )
            else:
                unsafe_delete_side_effects.append(str(dependency))

        if (
            "delete" in dml_commands
            and unsafe_delete_side_effects
            and not delete_relationship_acknowledged
        ):
            raise RuntimeError(
                "DELETE foi bloqueado porque acoes referenciais podem alterar outras tabelas "
                "que nao estao vazias ou nao possuem contador confiavel: "
                + ", ".join(unsafe_delete_side_effects)
                + ". A execucao so pode continuar depois da confirmacao explicita do risco."
            )

    def list_table_raw_schemas(
        self,
        database_name: str,
        full_table_name: str,
        cancel_event=None,
    ) -> list[str]:
        self._ensure_control_metadata(cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT raw_schema
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
  AND raw_schema IS NOT NULL
  AND btrim(raw_schema) <> ''
GROUP BY raw_schema
ORDER BY MIN(version_code::integer), raw_schema;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -v ON_ERROR_STOP=1 "
            f"-c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        return self._dedupe_non_empty_strings(output.splitlines())

    def get_raw_schema_version_references(
        self,
        database_name: str,
        full_table_name: str,
        raw_schemas,
        cancel_event=None,
    ) -> list[dict]:
        self._ensure_control_metadata(cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        normalized_raw_schemas = self._dedupe_non_empty_strings(raw_schemas)
        if not normalized_raw_schemas:
            return []
        scope_sql = ", ".join(
            sql_literal(value)
            for value in normalized_raw_schemas
        )
        sql = f"""
SELECT COALESCE(
    json_agg(
        json_build_object(
            'version_code', version_code,
            'raw_schema', raw_schema,
            'raw_dump_path', COALESCE(raw_dump_path, ''),
            'raw_hash', COALESCE(raw_hash, '')
        )
        ORDER BY version_code::integer
    ),
    '[]'::json
)::text
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
  AND raw_schema IN ({scope_sql});
"""
        references = self._run_control_json_query(
            sql,
            cancel_event=cancel_event,
        ) or []
        if not isinstance(references, list):
            raise RuntimeError(
                "Source version references were returned in an unexpected format."
            )
        return [
            dict(item)
            for item in references
            if isinstance(item, dict)
        ]

    @staticmethod
    def resolve_raw_schema_interval(
        available_raw_schemas,
        first_raw_schema: str,
        last_raw_schema: str | None = None,
    ) -> list[str]:
        available = PostgresAdminService._dedupe_non_empty_strings(
            available_raw_schemas
        )
        first_value = str(first_raw_schema or "").strip()
        last_value = str(last_raw_schema or first_value).strip()
        if not first_value:
            raise RuntimeError("Select the first raw_schema for the expansion.")
        if not last_value:
            last_value = first_value
        if first_value not in available:
            raise RuntimeError(
                f"First raw_schema {first_value!r} is not registered for the source."
            )
        if last_value not in available:
            raise RuntimeError(
                f"Last raw_schema {last_value!r} is not registered for the source."
            )

        first_index = available.index(first_value)
        last_index = available.index(last_value)
        if first_index > last_index:
            raise RuntimeError(
                "The first raw_schema must appear before or equal the last raw_schema."
            )
        return available[first_index:last_index + 1]

    @classmethod
    def _collect_sql_relation_references(
        cls,
        sql_text: str,
        default_schema: str = "public",
    ) -> list[tuple[str, str]]:
        tokens = cls._tokenize_managed_sql(sql_text)
        references = []
        from_clause_by_depth = {}
        clause_terminators = {
            "where",
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "returning",
            "union",
            "intersect",
            "except",
            "window",
        }

        def parse_relation_at(index: int):
            while (
                index < len(tokens)
                and tokens[index][0] == "word"
                and tokens[index][1] in {"lateral", "only"}
            ):
                index += 1
            if index >= len(tokens):
                return None
            if tokens[index][0] == "symbol" and tokens[index][1] == "(":
                return None
            if tokens[index][0] not in {"word", "identifier"}:
                return None

            first_name = tokens[index][1]
            next_index = index + 1
            if (
                next_index < len(tokens)
                and tokens[next_index][0] == "symbol"
                and tokens[next_index][1] == "("
            ):
                return None
            if (
                next_index + 1 < len(tokens)
                and tokens[next_index][0] == "symbol"
                and tokens[next_index][1] == "."
                and tokens[next_index + 1][0] in {"word", "identifier"}
            ):
                relation_end = next_index + 2
                if (
                    relation_end < len(tokens)
                    and tokens[relation_end][0] == "symbol"
                    and tokens[relation_end][1] == "("
                ):
                    return None
                return first_name, tokens[next_index + 1][1]
            return default_schema, first_name

        for index, (kind, value, depth) in enumerate(tokens):
            if kind == "word" and value in clause_terminators:
                from_clause_by_depth[depth] = False
                continue

            relation_index = None
            if kind == "word" and value == "from":
                from_clause_by_depth[depth] = True
                relation_index = index + 1
            elif kind == "word" and value == "join":
                relation_index = index + 1
            elif kind == "word" and value == "references":
                relation_index = index + 1
            elif kind == "word" and value == "table":
                previous_kind = tokens[index - 1][0] if index > 0 else None
                previous_value = tokens[index - 1][1] if index > 0 else None
                if (
                    index == 0
                    or (
                        previous_kind == "word"
                        and previous_value
                        in {"union", "all", "distinct", "intersect", "except"}
                    )
                    or (
                        previous_kind == "symbol"
                        and previous_value == "("
                    )
                ):
                    relation_index = index + 1
            elif (
                kind == "symbol"
                and value == ","
                and from_clause_by_depth.get(depth)
            ):
                relation_index = index + 1

            if relation_index is None:
                continue
            relation = parse_relation_at(relation_index)
            if relation and relation not in references:
                references.append(relation)

        return references

    @staticmethod
    def _assert_expansion_relation_groups_are_visible(
        tokens: list[tuple[str, str, int]],
    ):
        from_clause_by_depth = {}
        clause_terminators = {
            "where",
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "returning",
            "union",
            "intersect",
            "except",
            "window",
        }
        for index, (kind, value, depth) in enumerate(tokens):
            if kind == "word" and value in clause_terminators:
                from_clause_by_depth[depth] = False
                continue

            relation_index = None
            if kind == "word" and value == "from":
                from_clause_by_depth[depth] = True
                relation_index = index + 1
            elif kind == "word" and value == "join":
                relation_index = index + 1
            elif (
                kind == "symbol"
                and value == ","
                and from_clause_by_depth.get(depth)
            ):
                relation_index = index + 1
            if relation_index is None:
                continue

            while (
                relation_index < len(tokens)
                and tokens[relation_index][0] == "word"
                and tokens[relation_index][1] == "lateral"
            ):
                relation_index += 1
            if (
                relation_index >= len(tokens)
                or tokens[relation_index][0] != "symbol"
                or tokens[relation_index][1] != "("
            ):
                continue

            inner_index = relation_index + 1
            if (
                inner_index >= len(tokens)
                or tokens[inner_index][0] != "word"
                or tokens[inner_index][1] not in {"select", "values"}
            ):
                raise RuntimeError(
                    "Expand does not accept parenthesized relations or JOINs. "
                    "Use only the source placeholder or an explicit SELECT subquery."
                )

    @classmethod
    def _collect_sql_function_references(
        cls,
        sql_text: str,
    ) -> list[tuple[str | None, str]]:
        tokens = cls._tokenize_managed_sql(sql_text)
        select_index = next(
            (
                index
                for index, (kind, value, depth) in enumerate(tokens)
                if kind == "word" and value == "select" and depth == 0
            ),
            len(tokens),
        )
        special_forms = {
            "all",
            "any",
            "array",
            "cast",
            "case",
            "coalesce",
            "distinct",
            "else",
            "exists",
            "except",
            "extract",
            "filter",
            "from",
            "greatest",
            "group",
            "having",
            "in",
            "intersect",
            "join",
            "lateral",
            "least",
            "limit",
            "nullif",
            "offset",
            "on",
            "order",
            "over",
            "overlay",
            "partition",
            "position",
            "row",
            "select",
            "some",
            "substring",
            "then",
            "trim",
            "union",
            "values",
            "when",
            "where",
        }
        references = []
        for index in range(select_index, len(tokens) - 1):
            kind, value, _depth = tokens[index]
            next_kind, next_value, _next_depth = tokens[index + 1]
            if (
                kind not in {"word", "identifier"}
                or next_kind != "symbol"
                or next_value != "("
            ):
                continue
            if kind == "word" and value in special_forms:
                continue
            if (
                index > 0
                and tokens[index - 1][0] == "word"
                and tokens[index - 1][1] == "as"
            ):
                continue
            if (
                index > 0
                and tokens[index - 1][0] == "symbol"
                and tokens[index - 1][1] == ")"
            ):
                continue
            if (
                index >= 2
                and tokens[index - 1][0] == "symbol"
                and tokens[index - 1][1] == ":"
                and tokens[index - 2][0] == "symbol"
                and tokens[index - 2][1] == ":"
            ):
                continue

            function_schema = None
            if (
                index >= 2
                and tokens[index - 1][0] == "symbol"
                and tokens[index - 1][1] == "."
                and tokens[index - 2][0] in {"word", "identifier"}
            ):
                function_schema = tokens[index - 2][1]
            reference = (function_schema, value)
            if reference not in references:
                references.append(reference)
        return references

    @classmethod
    def _parse_expansion_destination_columns(
        cls,
        tokens: list[tuple[str, str, int]],
        relation_end: int,
    ) -> list[str]:
        if (
            relation_end >= len(tokens)
            or tokens[relation_end][0] != "symbol"
            or tokens[relation_end][1] != "("
            or tokens[relation_end][2] != 0
        ):
            raise RuntimeError(
                "Every expansion INSERT must explicitly list its destination columns."
            )

        columns = []
        index = relation_end + 1
        expect_column = True
        while index < len(tokens):
            kind, value, depth = tokens[index]
            if kind == "symbol" and value == ")" and depth == 0:
                if expect_column or not columns:
                    raise RuntimeError("The destination column list is empty or incomplete.")
                return columns
            if expect_column:
                if kind not in {"word", "identifier"} or depth != 1:
                    raise RuntimeError(
                        "The destination column list must contain column names only."
                    )
                columns.append(value)
                expect_column = False
            else:
                if kind != "symbol" or value != "," or depth != 1:
                    raise RuntimeError(
                        "Separate destination columns with commas."
                    )
                expect_column = True
            index += 1

        raise RuntimeError("The destination column list was not terminated.")

    @classmethod
    def _prepare_cross_table_expansion_statement(
        cls,
        statement: str,
        source_schema_name: str,
        source_table_name: str,
        destination_schema_name: str,
        destination_table_name: str,
        raw_schemas,
        allowed_read_relations=None,
    ) -> dict:
        placeholder = cls.CROSS_TABLE_SOURCE_PLACEHOLDER
        masked_statement = cls._mask_sql_literals_and_comments(statement)
        placeholder_count = masked_statement.count(placeholder)
        if placeholder_count <= 0:
            raise RuntimeError(
                f"Every INSERT must read the source through the {placeholder} placeholder."
            )
        if statement.count(placeholder) != placeholder_count:
            raise RuntimeError(
                f"The {placeholder} placeholder cannot appear inside text or comments."
            )

        sentinel_schema_name = "__pgdm_expansion_guard__"
        sentinel_table_name = "__raw_schema_scope__"
        sentinel_relation = (
            f"{sql_ident(sentinel_schema_name)}."
            f"{sql_ident(sentinel_table_name)}"
        )
        reserved_identifiers = {
            sentinel_schema_name,
            sentinel_table_name,
        }
        original_tokens = cls._tokenize_managed_sql(statement)
        if any(
            kind in {"word", "identifier"}
            and value.lower() in reserved_identifiers
            for kind, value, _depth in original_tokens
        ):
            raise RuntimeError("The recipe uses a reserved internal identifier.")
        validation_statement = statement.replace(placeholder, sentinel_relation)
        tokens = cls._tokenize_managed_sql(validation_statement)
        if not tokens:
            raise RuntimeError("The expansion recipe is empty.")
        if any(
            kind == "symbol" and value == "\\"
            for kind, value, _depth in tokens
        ):
            raise RuntimeError(
                "Expand does not accept psql meta-commands beginning with a backslash."
            )
        cls._assert_expansion_relation_groups_are_visible(tokens)

        first_kind, first_keyword, first_depth = tokens[0]
        if first_kind != "word" or first_keyword != "insert" or first_depth != 0:
            raise RuntimeError(
                "Expand accepts only INSERT INTO destination (...) SELECT ... FROM source statements."
            )

        forbidden_words = {
            "update",
            "delete",
            "truncate",
            "merge",
            "alter",
            "drop",
            "create",
            "copy",
            "call",
            "do",
            "grant",
            "revoke",
            "execute",
            "prepare",
            "vacuum",
            "analyze",
            "refresh",
            "lock",
            "notify",
            "listen",
            "unlisten",
            "only",
        }
        token_words = [
            value
            for kind, value, _depth in tokens
            if kind == "word"
        ]
        found_forbidden = sorted(forbidden_words.intersection(token_words))
        if found_forbidden:
            raise RuntimeError(
                "The expansion recipe contains a forbidden command: "
                + ", ".join(word.upper() for word in found_forbidden)
                + "."
            )
        if token_words.count("insert") != 1:
            raise RuntimeError(
                "Every expansion statement must contain exactly one INSERT."
            )
        if "with" in token_words:
            raise RuntimeError(
                "WITH/CTE is not accepted by Expand because it can hide other changes."
            )
        if "returning" in token_words or "conflict" in token_words:
            raise RuntimeError(
                "RETURNING and ON CONFLICT are not accepted by Expand."
            )

        top_level_words = cls._managed_sql_top_level_words(tokens)
        if "select" not in top_level_words:
            raise RuntimeError(
                "Expand accepts INSERT only when its data is produced by SELECT."
            )
        select_index = next(
            index
            for index, (kind, value, depth) in enumerate(tokens)
            if kind == "word" and value == "select" and depth == 0
        )
        nondeterministic_expressions = {
            "current_catalog",
            "current_date",
            "current_role",
            "current_schema",
            "current_time",
            "current_timestamp",
            "current_user",
            "localtime",
            "localtimestamp",
            "session_user",
            "system_user",
            "user",
        }
        found_nondeterministic_expressions = []
        for index in range(select_index, len(tokens)):
            kind, value, _depth = tokens[index]
            if kind != "word" or value not in nondeterministic_expressions:
                continue
            previous_is_dot = (
                index > 0
                and tokens[index - 1][0] == "symbol"
                and tokens[index - 1][1] == "."
            )
            next_is_dot = (
                index + 1 < len(tokens)
                and tokens[index + 1][0] == "symbol"
                and tokens[index + 1][1] == "."
            )
            if not previous_is_dot and not next_is_dot:
                found_nondeterministic_expressions.append(value)
        if found_nondeterministic_expressions:
            raise RuntimeError(
                "The recipe uses a session- or time-dependent expression, "
                "which would make replay nondeterministic: "
                + ", ".join(
                    sorted(set(found_nondeterministic_expressions))
                )
                + "."
            )

        if len(tokens) < 3 or tokens[1][0] != "word" or tokens[1][1] != "into":
            raise RuntimeError("Use INSERT INTO schema.table in the expansion.")
        parsed_schema, parsed_table, relation_end, was_qualified = (
            cls._managed_sql_parse_relation(
                tokens,
                2,
                destination_schema_name,
            )
        )
        cls._managed_sql_assert_selected_relation(
            parsed_schema,
            parsed_table,
            destination_schema_name,
            destination_table_name,
            was_qualified,
        )
        destination_columns = cls._parse_expansion_destination_columns(
            tokens,
            relation_end,
        )

        source_relation = (sentinel_schema_name, sentinel_table_name)
        normalized_allowed_read_relations = {
            (
                str(schema_name or "").strip().lower(),
                str(table_name or "").strip().lower(),
            )
            for schema_name, table_name in (allowed_read_relations or [])
            if str(schema_name or "").strip() and str(table_name or "").strip()
        }
        references = cls._collect_sql_relation_references(
            validation_statement,
            default_schema=sentinel_schema_name,
        )
        unexpected_relations = [
            f"{schema_name}.{table_name}"
            for schema_name, table_name in references
            if (
                (schema_name, table_name) != source_relation
                and (schema_name, table_name)
                not in normalized_allowed_read_relations
            )
        ]
        if source_relation not in references:
            raise RuntimeError(
                "Could not confirm that the recipe reads the source table."
            )
        if unexpected_relations:
            raise RuntimeError(
                "The expansion may read only the protected source, the current "
                "destination, and earlier destinations in the same batch. "
                "Additional relations found: "
                + ", ".join(unexpected_relations)
                + "."
            )
        read_relations = [
            relation
            for relation in references
            if relation != source_relation
        ]

        function_references = cls._collect_sql_function_references(
            validation_statement
        )
        external_functions = [
            f"{schema_name}.{function_name}"
            for schema_name, function_name in function_references
            if schema_name and schema_name != "pg_catalog"
        ]
        if external_functions:
            raise RuntimeError(
                "Expand accepts native PostgreSQL functions only. "
                "Functions outside pg_catalog were blocked: "
                + ", ".join(external_functions)
                + "."
            )
        scope_sql = ", ".join(sql_literal(value) for value in raw_schemas)
        source_relation_sql = (
            "(\n"
            f"    SELECT *\n"
            f"    FROM {sql_ident(source_schema_name)}.{sql_ident(source_table_name)}\n"
            f"    WHERE {sql_ident('raw_schema')} IN ({scope_sql})\n"
            ")"
        )
        guarded_statement = statement.replace(placeholder, source_relation_sql)
        return {
            "statement": guarded_statement.rstrip().rstrip(";").strip(),
            "destination_columns": destination_columns,
            "function_references": function_references,
            "read_relations": read_relations,
        }

    @classmethod
    def _prepare_cross_table_expansion(
        cls,
        source_full_table_name: str,
        raw_schemas,
        destinations,
    ) -> dict:
        source_schema_name, source_table_name = split_table_name(source_full_table_name)
        normalized_raw_schemas = cls._dedupe_non_empty_strings(raw_schemas)
        if not normalized_raw_schemas:
            raise RuntimeError("Select at least one raw_schema for the expansion.")

        prepared_destinations = []
        seen_destinations = set()
        for position, destination in enumerate(destinations or [], start=1):
            full_table_name = str(
                (destination or {}).get("table_name") or ""
            ).strip()
            version_title = str(
                (destination or {}).get("version_title") or ""
            ).strip()
            sql_script = str((destination or {}).get("sql") or "").strip()
            if not full_table_name:
                raise RuntimeError(f"Enter the table for destination {position}.")
            if not version_title:
                raise RuntimeError(f"Enter the version title for destination {position}.")
            if not sql_script:
                raise RuntimeError(f"Enter the SQL for destination {position}.")

            destination_schema_name, destination_table_name = split_table_name(
                full_table_name
            )
            destination_key = (
                destination_schema_name,
                destination_table_name,
            )
            if destination_key == (source_schema_name, source_table_name):
                raise RuntimeError(
                    "The source table cannot be used as an expansion destination."
                )
            if destination_key in seen_destinations:
                raise RuntimeError(
                    f"Destination {full_table_name} was provided more than once."
                )
            seen_destinations.add(destination_key)
            allowed_read_relations = set(seen_destinations)

            statements = cls._split_sql_statements(sql_script)
            prepared_statements = []
            destination_columns = []
            function_references = []
            read_relations = []
            for statement in statements:
                if not cls._tokenize_managed_sql(statement):
                    continue
                prepared_statement = cls._prepare_cross_table_expansion_statement(
                    statement,
                    source_schema_name,
                    source_table_name,
                    destination_schema_name,
                    destination_table_name,
                    normalized_raw_schemas,
                    allowed_read_relations=allowed_read_relations,
                )
                prepared_statements.append(prepared_statement["statement"])
                for column_name in prepared_statement["destination_columns"]:
                    if column_name not in destination_columns:
                        destination_columns.append(column_name)
                for function_reference in prepared_statement["function_references"]:
                    if function_reference not in function_references:
                        function_references.append(function_reference)
                for read_relation in prepared_statement["read_relations"]:
                    if read_relation not in read_relations:
                        read_relations.append(read_relation)

            if not prepared_statements:
                raise RuntimeError(
                    f"Destination {full_table_name} has no valid INSERT statement."
                )

            recipe_header = [
                "-- PGDM cross-table expansion",
                (
                    "-- Source: "
                    f"{source_schema_name}.{source_table_name} (read only)"
                ),
                (
                    "-- Raw schemas: "
                    + json.dumps(normalized_raw_schemas, ensure_ascii=False)
                ),
                (
                    "-- Destination reads: "
                    + (
                        ", ".join(
                            f"{schema_name}.{table_name}"
                            for schema_name, table_name in read_relations
                        )
                        if read_relations
                        else "(none)"
                    )
                ),
            ]
            sql_recipe = "\n".join(
                recipe_header
                + [
                    "",
                    ";\n\n".join(prepared_statements) + ";",
                ]
            )
            prepared_destinations.append(
                {
                    "table_name": full_table_name,
                    "schema_name": destination_schema_name,
                    "pure_table_name": destination_table_name,
                    "version_title": version_title,
                    "statements": prepared_statements,
                    "sql_recipe": sql_recipe,
                    "destination_columns": destination_columns,
                    "function_references": function_references,
                    "read_relations": read_relations,
                }
            )

        if not prepared_destinations:
            raise RuntimeError("Add at least one expansion destination.")

        return {
            "source_schema_name": source_schema_name,
            "source_table_name": source_table_name,
            "raw_schemas": normalized_raw_schemas,
            "destinations": prepared_destinations,
        }

    @staticmethod
    def _build_cross_table_expansion_lock_sql(destinations) -> str:
        relations = sorted(
            {
                (
                    str(destination.get("schema_name") or "").strip(),
                    str(destination.get("pure_table_name") or "").strip(),
                )
                for destination in (destinations or [])
                if (
                    str(destination.get("schema_name") or "").strip()
                    and str(destination.get("pure_table_name") or "").strip()
                )
            }
        )
        if not relations:
            raise RuntimeError(
                "Could not identify the destinations for expansion locking."
            )
        relation_sql = ",\n    ".join(
            f"{sql_ident(schema_name)}.{sql_ident(table_name)}"
            for schema_name, table_name in relations
        )
        return (
            "LOCK TABLE\n"
            f"    {relation_sql}\n"
            "IN SHARE ROW EXCLUSIVE MODE;"
        )

    def _validate_cross_table_expansion_functions(
        self,
        database_name: str,
        destinations,
        cancel_event=None,
    ):
        function_references = []
        for destination in destinations or []:
            for reference in destination.get("function_references") or []:
                normalized_reference = (
                    str(reference[0]).strip() if reference[0] else None,
                    str(reference[1]).strip(),
                )
                if (
                    normalized_reference[1]
                    and normalized_reference not in function_references
                ):
                    function_references.append(normalized_reference)
        if not function_references:
            return

        values_sql = ",\n".join(
            (
                "("
                f"{sql_literal(schema_name) if schema_name else 'NULL'}, "
                f"{sql_literal(function_name)}"
                ")"
            )
            for schema_name, function_name in function_references
        )
        sql = f"""
WITH requested(function_schema, function_name) AS (
    VALUES
    {values_sql}
)
SELECT COALESCE(
    json_agg(
        json_build_object(
            'requested_schema', requested.function_schema,
            'function_name', requested.function_name,
            'candidates', COALESCE((
                SELECT json_agg(
                    json_build_object(
                        'schema_name', namespace.nspname,
                        'volatility', function_data.provolatile,
                        'security_definer', function_data.prosecdef,
                        'function_kind', function_data.prokind
                    )
                    ORDER BY namespace.nspname, function_data.oid
                )
                FROM pg_proc AS function_data
                JOIN pg_namespace AS namespace
                  ON namespace.oid = function_data.pronamespace
                WHERE function_data.proname = requested.function_name
                  AND (
                      (
                          requested.function_schema IS NOT NULL
                          AND namespace.nspname = requested.function_schema
                      )
                      OR (
                          requested.function_schema IS NULL
                          AND pg_function_is_visible(function_data.oid)
                      )
                  )
            ), '[]'::json)
        )
        ORDER BY requested.function_schema NULLS FIRST, requested.function_name
    ),
    '[]'::json
)::text
FROM requested;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -qAt -v ON_ERROR_STOP=1 "
            f"-c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(
            command,
            cancel_event=cancel_event,
        ).strip()
        try:
            function_metadata = json.loads(output.splitlines()[-1] if output else "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Could not validate the functions used by the expansion."
            ) from exc

        problems = []
        for item in function_metadata or []:
            requested_schema = str(item.get("requested_schema") or "").strip()
            function_name = str(item.get("function_name") or "").strip()
            function_label = (
                f"{requested_schema}.{function_name}"
                if requested_schema
                else function_name
            )
            candidates = list(item.get("candidates") or [])
            if not candidates:
                problems.append(f"{function_label}: function not found")
                continue
            unsafe_candidates = [
                candidate
                for candidate in candidates
                if (
                    str(candidate.get("schema_name") or "") != "pg_catalog"
                    or str(candidate.get("volatility") or "") != "i"
                    or bool(candidate.get("security_definer"))
                    or str(candidate.get("function_kind") or "") == "p"
                )
            ]
            if unsafe_candidates:
                problems.append(
                    f"{function_label}: only native immutable functions without "
                    "SECURITY DEFINER are accepted"
                )
        if problems:
            raise RuntimeError(
                "The expansion was blocked because a function may access or modify "
                "data outside the protected source:\n- "
                + "\n- ".join(problems)
            )

    @staticmethod
    def _build_expansion_manifest_sql(
        source_schema_name: str,
        source_table_name: str,
        raw_schemas,
    ) -> str:
        scope_sql = ", ".join(sql_literal(value) for value in raw_schemas)
        expected_scope_array = f"ARRAY[{scope_sql}]::text[]"
        source_table_sql = (
            f"{sql_ident(source_schema_name)}.{sql_ident(source_table_name)}"
        )
        manifest_table = sql_ident("pgdm_cross_table_hash_manifest")
        return f"""
CREATE TEMP TABLE {manifest_table} ON COMMIT DROP AS
SELECT
    {sql_ident('raw_schema')}::text AS raw_schema,
    {sql_ident('raw_hash')}::text AS raw_hash,
    COUNT(*)::bigint AS row_count
FROM {source_table_sql}
WHERE {sql_ident('raw_schema')} IN ({scope_sql})
GROUP BY {sql_ident('raw_schema')}, {sql_ident('raw_hash')};

DO $pgdm_expansion_scope$
DECLARE
    missing_schemas text;
BEGIN
    SELECT string_agg(expected.raw_schema, ', ' ORDER BY expected.raw_schema)
    INTO missing_schemas
    FROM unnest({expected_scope_array}) AS expected(raw_schema)
    WHERE NOT EXISTS (
        SELECT 1
        FROM {manifest_table} AS actual
        WHERE actual.raw_schema = expected.raw_schema
    );

    IF missing_schemas IS NOT NULL THEN
        RAISE EXCEPTION
            'raw_schema has no source rows: %.',
            missing_schemas;
    END IF;
END
$pgdm_expansion_scope$;

SELECT json_build_object(
    'raw_schemas', to_json({expected_scope_array}),
    'expected_row_count', COALESCE((
        SELECT SUM(row_count)
        FROM {manifest_table}
    ), 0),
    'expected_distinct_raw_hash_count', (
        SELECT COUNT(*)
        FROM (
            SELECT DISTINCT raw_hash
            FROM {manifest_table}
        ) AS distinct_hashes
    ),
    'raw_hash_manifest', COALESCE((
        SELECT json_agg(
            json_build_object(
                'raw_schema', raw_schema,
                'raw_hash', raw_hash,
                'row_count', row_count
            )
            ORDER BY raw_schema, raw_hash NULLS FIRST
        )
        FROM {manifest_table}
    ), '[]'::json),
    'source_columns', COALESCE((
        SELECT json_agg(
            json_build_object(
                'name', attribute.attname,
                'type', pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)
            )
            ORDER BY attribute.attnum
        )
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation
          ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = {sql_literal(source_schema_name)}
          AND relation.relname = {sql_literal(source_table_name)}
          AND relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
    ), '[]'::json)
)::text;
""".strip()

    def execute_cross_table_expansion(
        self,
        database_name: str,
        source_full_table_name: str,
        raw_schemas,
        destinations,
        cancel_event=None,
        progress_callback=None,
        post_commit_callback=None,
        timing_report: dict | None = None,
    ) -> dict:
        if timing_report is None:
            timing_report = self.create_cross_table_expansion_timing_report(
                database_name,
                source_full_table_name,
                raw_schemas,
                [item.get("table_name") for item in (destinations or [])],
            )
        with self.expansion_timing_scope(
            timing_report,
            "prepare",
            "Normalize, parse, and validate expansion recipes",
        ):
            prepared = self._prepare_cross_table_expansion(
                source_full_table_name,
                raw_schemas,
                destinations,
            )
        source_schema_name = prepared["source_schema_name"]
        source_table_name = prepared["source_table_name"]

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "preflight",
            5,
            "Validating source, destinations, and counters...",
        )
        with self.expansion_timing_scope(
            timing_report,
            "preflight",
            "Verify source table existence",
        ):
            source_exists = self.table_exists(
                database_name,
                source_full_table_name,
                cancel_event=cancel_event,
            )
        if not source_exists:
            raise RuntimeError(
                f"Source table {source_full_table_name} does not exist."
            )

        with self.expansion_timing_scope(
            timing_report,
            "preflight",
            "Read and validate source column metadata",
        ):
            source_columns = {
                item["name"]: item["type"]
                for item in self.get_table_column_definitions(
                    database_name,
                    source_full_table_name,
                    cancel_event=cancel_event,
                )
            }
        missing_source_columns = [
            column_name
            for column_name in ("raw_schema", "raw_hash")
            if column_name not in source_columns
        ]
        if missing_source_columns:
            raise RuntimeError(
                f"Source {source_full_table_name} is missing required columns: "
                + ", ".join(missing_source_columns)
                + "."
            )

        with self.expansion_timing_scope(
            timing_report,
            "preflight",
            "Load Raw version references for the selected scope",
        ):
            source_versions = self.get_raw_schema_version_references(
                database_name,
                source_full_table_name,
                prepared["raw_schemas"],
                cancel_event=cancel_event,
            )
        referenced_raw_schemas = {
            str(item.get("raw_schema") or "").strip()
            for item in source_versions
            if isinstance(item, dict)
        }
        missing_version_schemas = [
            raw_schema
            for raw_schema in prepared["raw_schemas"]
            if raw_schema not in referenced_raw_schemas
        ]
        if missing_version_schemas:
            raise RuntimeError(
                "No Raw version is registered for the selected scope: "
                + ", ".join(missing_version_schemas)
                + "."
            )

        with self.expansion_timing_scope(
            timing_report,
            "preflight",
            "Validate SQL function safety",
        ):
            self._validate_cross_table_expansion_functions(
                database_name,
                prepared["destinations"],
                cancel_event=cancel_event,
            )
        with self.expansion_timing_scope(
            timing_report,
            "preflight",
            "Ensure data-database metadata and row counters",
        ):
            self.ensure_data_database_metadata(
                database_name,
                cancel_event=cancel_event,
            )
        for destination in prepared["destinations"]:
            full_table_name = destination["table_name"]
            with self.expansion_timing_scope(
                timing_report,
                "preflight",
                f"Verify destination {full_table_name}",
            ):
                destination_exists = self.table_exists(
                    database_name,
                    full_table_name,
                    cancel_event=cancel_event,
                )
            if not destination_exists:
                raise RuntimeError(
                    f"Destination table {full_table_name} does not exist."
                )
            with self.expansion_timing_scope(
                timing_report,
                "preflight",
                f"Read row counter for {full_table_name}",
            ):
                self.get_table_row_count(
                    database_name,
                    full_table_name,
                    cancel_event=cancel_event,
                )
            with self.expansion_timing_scope(
                timing_report,
                "preflight",
                f"Validate managed dependencies for {full_table_name}",
            ):
                self._validate_managed_sql_dependencies(
                    database_name,
                    full_table_name,
                    {"insert"},
                    cancel_event=cancel_event,
                )

        self._notify_progress(
            progress_callback,
            "preflight",
            100,
            (
                f"Preflight completed for {len(prepared['destinations'])} "
                "destination(s)."
            ),
        )
        self._raise_if_cancelled(cancel_event)

        with self.expansion_timing_scope(
            timing_report,
            "prepare",
            "Build protected transactional SQL batch",
        ):
            execution_statements = []
            timing_sequence = 10
            for destination in prepared["destinations"]:
                for statement_index, statement in enumerate(
                    destination["statements"],
                    start=1,
                ):
                    execution_statements.append(
                        self._build_managed_dml_sql(
                            statement,
                            destination["schema_name"],
                            destination["pure_table_name"],
                            "add",
                            timing_sequence=timing_sequence,
                            timing_stage="execute",
                            timing_name=(
                                f"Insert into {destination['table_name']} "
                                f"(statement {statement_index})"
                            ),
                            timing_destination=destination["table_name"],
                            timing_statement_index=statement_index,
                        )
                    )
                    timing_sequence += 1

            manifest_sql = self._build_expansion_manifest_sql(
                source_schema_name,
                source_table_name,
                prepared["raw_schemas"],
            )
            destination_lock_sql = self._build_cross_table_expansion_lock_sql(
                prepared["destinations"]
            )
            timing_table_sql = """
CREATE TEMP TABLE pgdm_expansion_timings (
    sequence_number integer PRIMARY KEY,
    stage_key text NOT NULL,
    item_name text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    rows_affected bigint,
    destination_name text,
    statement_index integer
) ON COMMIT PRESERVE ROWS;
""".strip()
            timing_result_sql = """
SELECT json_build_object(
    'pgdm_expansion_timings', COALESCE(
        json_agg(
            json_build_object(
                'sequence', sequence_number,
                'stage', stage_key,
                'name', item_name,
                'duration_seconds', EXTRACT(
                    EPOCH FROM (finished_at - started_at)
                ),
                'rows', rows_affected,
                'destination', destination_name,
                'statement_index', statement_index
            ) ORDER BY sequence_number
        ),
        '[]'::json
    )
)::text
FROM pg_temp.pgdm_expansion_timings;
""".strip()
            wrapped_sql = (
                timing_table_sql
                + "\n\nBEGIN ISOLATION LEVEL REPEATABLE READ;\n"
                + "INSERT INTO pg_temp.pgdm_expansion_timings VALUES "
                + "(1, 'execute', 'Initialize database transaction', "
                + "clock_timestamp(), NULL, NULL, NULL, NULL);\n"
                + "SET LOCAL client_min_messages = warning;\n"
                + "UPDATE pg_temp.pgdm_expansion_timings SET finished_at = "
                + "clock_timestamp() WHERE sequence_number = 1;\n\n"
                + "INSERT INTO pg_temp.pgdm_expansion_timings VALUES "
                + "(2, 'execute', 'Acquire destination table locks', "
                + "clock_timestamp(), NULL, NULL, NULL, NULL);\n"
                + destination_lock_sql
                + "\nUPDATE pg_temp.pgdm_expansion_timings SET finished_at = "
                + "clock_timestamp() WHERE sequence_number = 2;\n\n"
                + "INSERT INTO pg_temp.pgdm_expansion_timings VALUES "
                + "(3, 'execute', 'Scan source and build dependency manifest', "
                + "clock_timestamp(), NULL, NULL, NULL, NULL);\n"
                + manifest_sql
                + "\nUPDATE pg_temp.pgdm_expansion_timings SET finished_at = "
                + "clock_timestamp() WHERE sequence_number = 3;\n\n"
                + "\n\n".join(execution_statements)
                + "\n\nINSERT INTO pg_temp.pgdm_expansion_timings VALUES "
                + "(9000, 'execute', 'Commit expansion transaction', "
                + "clock_timestamp(), NULL, NULL, NULL, NULL);\n"
                + "COMMIT;\n"
                + "UPDATE pg_temp.pgdm_expansion_timings SET finished_at = "
                + "clock_timestamp() WHERE sequence_number = 9000;\n\n"
                + timing_result_sql
                + "\nDROP TABLE pg_temp.pgdm_expansion_timings;\n"
            )
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} "
            "-X -qAt -v ON_ERROR_STOP=1 -f -"
        )
        self._notify_progress(
            progress_callback,
            "execute",
            5,
            "Running all expansions in a single transaction...",
        )
        with self.expansion_timing_scope(
            timing_report,
            "execute",
            "Expansion database batch envelope",
            record_local_cpu=False,
            remote_include_in_total=False,
        ):
            output = self.run_remote_command(
                command,
                stdin_text=wrapped_sql,
                cancel_event=cancel_event,
                stdin_progress_callback=lambda progress, message: self._notify_progress(
                    progress_callback,
                    "execute",
                    10 + (progress * 0.8),
                    message,
                ),
            )
        if post_commit_callback:
            post_commit_callback()

        with self.expansion_timing_scope(
            timing_report,
            "execute",
            "Parse database manifest and timing telemetry",
        ):
            manifest = None
            server_timing_items = []
            for line in output.splitlines():
                candidate = line.strip()
                if not candidate.startswith("{"):
                    continue
                try:
                    parsed_candidate = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed_candidate, dict):
                    continue
                if "raw_hash_manifest" in parsed_candidate:
                    manifest = parsed_candidate
                if "pgdm_expansion_timings" in parsed_candidate:
                    server_timing_items = list(
                        parsed_candidate.get("pgdm_expansion_timings") or []
                    )
            for item in server_timing_items:
                self._append_expansion_timing(
                    timing_report,
                    item.get("stage") or "execute",
                    item.get("name") or "PostgreSQL expansion step",
                    float(item.get("duration_seconds") or 0.0),
                    "postgresql_backend_wall",
                    rows=item.get("rows"),
                    destination=item.get("destination"),
                    details=(
                        "Measured with PostgreSQL clock_timestamp() in the server "
                        "session; SSH transport is excluded."
                    ),
                )
        if manifest is None:
            raise RuntimeError(
                "The expansion completed, but the source manifest was not returned."
            )

        self._notify_progress(
            progress_callback,
            "execute",
            100,
            "Data committed to the destinations.",
        )

        timing_report["source_rows"] = int(
            manifest.get("expected_row_count") or 0
        )
        destination_row_totals = {}
        for item in timing_report.get("items") or []:
            destination_name = item.get("destination")
            rows_affected = item.get("rows")
            if not destination_name or rows_affected is None:
                continue
            destination_row_totals[destination_name] = (
                destination_row_totals.get(destination_name, 0)
                + int(rows_affected)
            )
        timing_report["destination_rows"] = [
            {"table_name": destination["table_name"], "rows": int(
                destination_row_totals.get(destination["table_name"], 0)
            )}
            for destination in prepared["destinations"]
        ]
        timing_report["total_rows_inserted"] = sum(
            item["rows"] for item in timing_report["destination_rows"]
        )

        return {
            "source_database_name": database_name,
            "source_schema_name": source_schema_name,
            "source_table_name": source_table_name,
            "raw_schemas": list(prepared["raw_schemas"]),
            "source_versions": source_versions,
            "dependency_manifest": manifest,
            "destinations": prepared["destinations"],
            "timing_report": timing_report,
        }

    def execute_sql_script(
        self,
        database_name: str,
        full_table_name: str,
        sql_script: str,
        delete_relationship_acknowledged: bool = False,
        cancel_event=None,
        progress_callback=None,
    ):
        prepared = self._prepare_managed_sql_script(sql_script, full_table_name)

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "execute",
            5,
            "Validando tabela, dependencias e contador...",
        )
        self.ensure_data_database_metadata(database_name, cancel_event=cancel_event)
        self.get_table_row_count(database_name, full_table_name, cancel_event=cancel_event)
        self._validate_managed_sql_dependencies(
            database_name,
            full_table_name,
            {item["command"] for item in prepared["classifications"]},
            delete_relationship_acknowledged=delete_relationship_acknowledged,
            cancel_event=cancel_event,
        )

        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -q -o /dev/null -v ON_ERROR_STOP=1 -f -"
        )

        self._notify_progress(
            progress_callback,
            "execute",
            10,
            "Executando SQL e atualizando o contador na mesma transacao...",
        )
        self.run_remote_command(
            command,
            stdin_text=prepared["wrapped_sql"],
            cancel_event=cancel_event,
            stdin_progress_callback=lambda progress, message: self._notify_progress(
                progress_callback,
                "execute",
                10 + (progress * 0.55),
                message,
            ),
        )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "execute", 100, "SQL executado com sucesso.")
        return {"sql_recipe": prepared["sql_recipe"]}

    def list_table_versions(
        self,
        database_name: str,
        full_table_name: str,
        cancel_event=None,
        trace_context: dict | None = None,
    ):
        started_at = time.perf_counter()
        self._trace_table_open(trace_context, f"list_table_versions(): iniciando para {full_table_name}")
        ensure_started_at = time.perf_counter()
        self._ensure_control_metadata(cancel_event=cancel_event, trace_context=trace_context)
        self._trace_table_open(
            trace_context,
            f"list_table_versions(): ensure_control_metadata em {(time.perf_counter() - ensure_started_at) * 1000:.1f} ms",
        )
        schema_name, table_name = split_table_name(full_table_name)

        sql = f"""
SELECT
    version_code,
    version_title,
    created_at::text,
    created_by,
    COALESCE(restored_from_version, ''),
    COALESCE(raw_dump_path, '')
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
ORDER BY version_code DESC;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )

        output = self.run_remote_command(
            command,
            cancel_event=cancel_event,
            trace_context=trace_context,
            trace_label=f"list_table_versions[{full_table_name}]",
        )
        parse_started_at = time.perf_counter()
        _headers, rows = self.parse_csv_output(output)
        self._trace_table_open(
            trace_context,
            f"list_table_versions(): parse do CSV em {(time.perf_counter() - parse_started_at) * 1000:.1f} ms; rows={len(rows)}",
        )

        results = []
        for row in rows:
            values = self._normalize_csv_row(
                row,
                6,
                trace_context=trace_context,
                trace_label=f"list_table_versions[{full_table_name}]",
            )
            results.append({
                "version_code": values[0],
                "version_title": values[1],
                "created_at": values[2],
                "created_by": values[3],
                "restored_from_version": values[4],
                "raw_dump_path": values[5],
                "schema_name": schema_name,
                "table_name": table_name,
            })
        self._trace_table_open(
            trace_context,
            f"list_table_versions(): conclu?do em {(time.perf_counter() - started_at) * 1000:.1f} ms; versions={len(results)}",
        )
        return results

    def get_table_version_detail(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        version_code: str,
        cancel_event=None,
        trace_context: dict | None = None,
    ):
        started_at = time.perf_counter()
        full_table_name = f"{schema_name}.{table_name}"
        self._trace_table_open(
            trace_context,
            f"get_table_version_detail(): iniciando para {full_table_name} @ {version_code}",
        )
        ensure_started_at = time.perf_counter()
        self._ensure_control_metadata(cancel_event=cancel_event, trace_context=trace_context)
        self._trace_table_open(
            trace_context,
            f"get_table_version_detail(): ensure_control_metadata em {(time.perf_counter() - ensure_started_at) * 1000:.1f} ms",
        )
        sql = f"""
SELECT
    version_code,
    version_title,
    created_at::text,
    created_by,
    sql_recipe,
    COALESCE(restored_from_version, ''),
    CASE
        WHEN CASE
        WHEN COALESCE(version_history_format, 'full') = 'entry'
          OR COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
        ELSE 'full'
    END = 'entry'
            THEN COALESCE(version_history_log, sql_recipe, '')
        ELSE ''
    END,
    CASE
        WHEN COALESCE(version_history_format, 'full') = 'entry'
          OR COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
        ELSE 'full'
    END,
    COALESCE(raw_dump_path, ''),
    COALESCE(raw_hash, ''),
    COALESCE(raw_ingested_at::text, ''),
    COALESCE(raw_schema, '')
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
  AND version_code = {sql_literal(version_code)}
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X --csv -c {shlex.quote(sql)}"
        )

        output = self.run_remote_command(
            command,
            cancel_event=cancel_event,
            trace_context=trace_context,
            trace_label=f"get_table_version_detail[{full_table_name}@{version_code}]",
        )
        parse_started_at = time.perf_counter()
        _headers, rows = self.parse_csv_output(output)
        self._trace_table_open(
            trace_context,
            f"get_table_version_detail(): parse do CSV em {(time.perf_counter() - parse_started_at) * 1000:.1f} ms; rows={len(rows)}",
        )
        if not rows:
            raise RuntimeError(f"Versao {version_code} nao encontrada.")

        values = self._normalize_csv_row(
            rows[0],
            12,
            trace_context=trace_context,
            trace_label=f"get_table_version_detail[{full_table_name}@{version_code}]",
        )
        result = {
            "version_code": values[0],
            "version_title": values[1],
            "created_at": values[2],
            "created_by": values[3],
            "sql_recipe": values[4],
            "restored_from_version": values[5],
            "version_history_log": values[6],
            "version_history_format": values[7],
            "raw_dump_path": values[8],
            "raw_hash": values[9],
            "raw_ingested_at": values[10],
            "raw_schema": values[11] or None,
            "schema_name": schema_name,
            "table_name": table_name,
        }
        self._trace_table_open(
            trace_context,
            (
                f"get_table_version_detail(): conclu?do em {(time.perf_counter() - started_at) * 1000:.1f} ms; "
                f"sql_recipe_chars={len(result['sql_recipe'])}; version_history_chars={len(result['version_history_log'])}"
            ),
        )
        return result

    def _run_control_json_query(self, sql: str, cancel_event=None):
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Nao foi possivel interpretar JSON retornado pelo banco de controle. "
                f"Saida recebida: {len(output)} caracteres."
            ) from exc

    def get_table_version_history_log(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        version_code: str | None = None,
        cancel_event=None,
    ) -> str:
        self._ensure_control_metadata(cancel_event=cancel_event)

        if version_code is None:
            latest_sql = f"""
SELECT version_code
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
ORDER BY version_code::integer DESC
LIMIT 1;
"""
            latest_command = (
                f"psql -h localhost -U {shlex.quote(self.sql_username)} "
                f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(latest_sql)}"
            )
            version_code = self.run_remote_command(latest_command, cancel_event=cancel_event).strip() or None
            if version_code is None:
                return ""

        try:
            target_version_number = version_to_int(version_code)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Codigo de versao invalido para historico: {version_code!r}.") from exc

        metadata_sql = f"""
SELECT COALESCE(
    json_agg(
        json_build_object(
            'version_code', version_code,
            'restored_from_version', COALESCE(restored_from_version, ''),
            'version_history_format', CASE
        WHEN COALESCE(version_history_format, 'full') = 'entry'
          OR COALESCE(version_history_log, sql_recipe, '') = COALESCE(sql_recipe, '') THEN 'entry'
        ELSE 'full'
    END
        )
        ORDER BY version_code::integer
    ),
    '[]'::json
)::text
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
  AND version_code::integer <= {target_version_number};
"""
        metadata = self._run_control_json_query(metadata_sql, cancel_event=cancel_event) or []
        if not isinstance(metadata, list):
            raise RuntimeError("Metadados de historico retornaram em formato inesperado.")

        ordered_versions = []
        for item in metadata:
            code = str(item.get("version_code") or "").strip()
            if not code:
                continue
            ordered_versions.append(
                {
                    "version_code": code,
                    "restored_from_version": str(item.get("restored_from_version") or "").strip(),
                    "version_history_format": str(
                        item.get("version_history_format") or self.VERSION_HISTORY_FORMAT_FULL
                    ).strip().lower(),
                }
            )

        available_codes = {item["version_code"] for item in ordered_versions}
        if version_code not in available_codes:
            raise RuntimeError(f"Versao {version_code} nao encontrada para montar historico.")

        memo = {}

        def flatten_history(target_code: str, stack=None):
            if target_code in memo:
                return [dict(item) for item in memo[target_code]]

            if stack is None:
                stack = set()
            if target_code in stack:
                raise RuntimeError("Ciclo detectado no historico de restauracoes.")
            if target_code not in available_codes:
                raise RuntimeError(f"Versao base {target_code} nao encontrada para montar historico.")

            stack.add(target_code)
            try:
                result = []
                for item in ordered_versions:
                    restored_from = item.get("restored_from_version") or ""
                    if restored_from:
                        result = flatten_history(restored_from, stack)
                        result.append(dict(item))
                    else:
                        result.append(dict(item))

                    if item["version_code"] == target_code:
                        memo[target_code] = [dict(entry) for entry in result]
                        return [dict(entry) for entry in result]
            finally:
                stack.discard(target_code)

            raise RuntimeError(f"Versao {target_code} nao encontrada para montar historico.")

        history_plan = flatten_history(version_code)
        base_index = -1
        for index, item in enumerate(history_plan):
            history_format = item.get("version_history_format") or self.VERSION_HISTORY_FORMAT_FULL
            if history_format != self.VERSION_HISTORY_FORMAT_ENTRY:
                base_index = index

        append_start = base_index + 1 if base_index >= 0 else 0
        needed_codes = []
        if base_index >= 0:
            needed_codes.append(history_plan[base_index]["version_code"])
        needed_codes.extend(item["version_code"] for item in history_plan[append_start:])

        if not needed_codes:
            return ""

        codes_sql = ", ".join(sql_literal(code) for code in dict.fromkeys(needed_codes))
        logs_sql = f"""
SELECT COALESCE(
    json_agg(
        json_build_object(
            'version_code', version_code,
            'history', COALESCE(version_history_log, sql_recipe, '')
        )
        ORDER BY version_code::integer
    ),
    '[]'::json
)::text
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
  AND version_code IN ({codes_sql});
"""
        log_rows = self._run_control_json_query(logs_sql, cancel_event=cancel_event) or []
        if not isinstance(log_rows, list):
            raise RuntimeError("Historico de versoes retornou em formato inesperado.")

        log_by_code = {
            str(row.get("version_code") or "").strip(): row.get("history") or ""
            for row in log_rows
            if str(row.get("version_code") or "").strip()
        }
        missing_codes = [code for code in needed_codes if code not in log_by_code]
        if missing_codes:
            raise RuntimeError(
                "Historico incompleto para as versoes: " + ", ".join(missing_codes)
            )

        parts = []
        if base_index >= 0:
            base_history = str(log_by_code.get(history_plan[base_index]["version_code"]) or "").strip()
            if base_history:
                parts.append(base_history)

        for item in history_plan[append_start:]:
            entry_history = str(log_by_code.get(item["version_code"]) or "").strip()
            if entry_history:
                parts.append(entry_history)

        return "\n\n".join(parts)

    def get_latest_version_history_log(self, database_name: str, schema_name: str, table_name: str, cancel_event=None) -> str:
        return self.get_table_version_history_log(
            database_name,
            schema_name,
            table_name,
            version_code=None,
            cancel_event=cancel_event,
        )

    def get_latest_table_version_code(self, database_name: str, full_table_name: str, cancel_event=None):
        self._ensure_control_metadata(cancel_event=cancel_event)
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT version_code
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)}
ORDER BY version_code DESC
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        version_code = self.run_remote_command(command, cancel_event=cancel_event).strip()
        return version_code or None

    def get_table_change_token(self, database_name: str, full_table_name: str, cancel_event=None):
        schema_name, table_name = split_table_name(full_table_name)
        sql = f"""
SELECT CONCAT_WS(
    ':',
    COALESCE(stats.n_tup_ins, 0),
    COALESCE(stats.n_tup_upd, 0),
    COALESCE(stats.n_tup_del, 0),
    COALESCE(stats.n_live_tup, 0),
    COALESCE(stats.n_dead_tup, 0),
    COALESCE(pg_relation_size(class.oid), 0)
)
FROM pg_class AS class
JOIN pg_namespace AS namespace
  ON namespace.oid = class.relnamespace
LEFT JOIN pg_stat_all_tables AS stats
  ON stats.relid = class.oid
WHERE namespace.nspname = {sql_literal(schema_name)}
  AND class.relname = {sql_literal(table_name)}
  AND class.relkind IN ('r', 'p')
LIMIT 1;
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -At -c {shlex.quote(sql)}"
        )
        change_token = self.run_remote_command(command, cancel_event=cancel_event).strip()
        return change_token or None

    def get_next_table_version(self, database_name: str, schema_name: str, table_name: str, cancel_event=None) -> str:
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT COALESCE(MAX(version_code), '----')
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        out = self.run_remote_command(command, cancel_event=cancel_event).strip()

        if out == "----" or not out:
            return "0000"

        return int_to_version(version_to_int(out) + 1)

    def get_table_version_count(self, database_name: str, schema_name: str, table_name: str, cancel_event=None) -> int:
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT COUNT(*)
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        return int(output or "0")

    def get_table_audit_count(self, database_name: str, schema_name: str, table_name: str, cancel_event=None) -> int:
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
SELECT COUNT(*)
FROM {CONTROL_TABLE_DELETE_AUDIT}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event).strip()
        return int(output or "0")

    @classmethod
    def _normalize_replay_dependency(cls, dependency: dict) -> dict:
        normalized = dict(dependency or {})
        dependency_kind = str(
            normalized.get("dependency_kind")
            or cls.CROSS_TABLE_EXPANSION_DEPENDENCY_KIND
        ).strip()
        source_database_name = str(
            normalized.get("source_database_name") or ""
        ).strip()
        source_schema_name = str(
            normalized.get("source_schema_name") or ""
        ).strip()
        source_table_name = str(
            normalized.get("source_table_name") or ""
        ).strip()
        payload = normalized.get("dependency_payload")
        if not isinstance(payload, dict):
            raise RuntimeError("O manifesto da dependencia deve ser um objeto JSON.")
        if not dependency_kind:
            raise RuntimeError("O tipo da dependencia de replay esta vazio.")
        if not source_database_name or not source_schema_name or not source_table_name:
            raise RuntimeError("A dependencia de replay nao identifica a tabela de origem.")
        return {
            "dependency_kind": dependency_kind,
            "source_database_name": source_database_name,
            "source_schema_name": source_schema_name,
            "source_table_name": source_table_name,
            "dependency_payload": payload,
        }

    @classmethod
    def _build_version_dependency_insert_sql(
        cls,
        inserted_version_cte: str,
        replay_dependencies,
    ) -> str:
        dependencies = [
            cls._normalize_replay_dependency(item)
            for item in (replay_dependencies or [])
        ]
        if not dependencies:
            return f"SELECT id FROM {inserted_version_cte}"

        values_sql = ",\n".join(
            (
                "("
                f"{sql_literal(item['dependency_kind'])}, "
                f"{sql_literal(item['source_database_name'])}, "
                f"{sql_literal(item['source_schema_name'])}, "
                f"{sql_literal(item['source_table_name'])}, "
                f"{sql_literal(json.dumps(item['dependency_payload'], ensure_ascii=False, sort_keys=True))}::jsonb"
                ")"
            )
            for item in dependencies
        )
        return f"""
INSERT INTO {CONTROL_TABLE_VERSION_DEPENDENCIES} (
    table_version_id,
    dependency_kind,
    source_database_name,
    source_schema_name,
    source_table_name,
    dependency_payload
)
SELECT
    inserted_version.id,
    dependency_data.dependency_kind,
    dependency_data.source_database_name,
    dependency_data.source_schema_name,
    dependency_data.source_table_name,
    dependency_data.dependency_payload
FROM {inserted_version_cte} AS inserted_version
CROSS JOIN (
    VALUES
    {values_sql}
) AS dependency_data (
    dependency_kind,
    source_database_name,
    source_schema_name,
    source_table_name,
    dependency_payload
)
RETURNING table_version_id
""".strip()

    def register_table_version(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        version_title: str,
        requested_by: str,
        workstation_name: str,
        sql_recipe: str,
        restored_from_version=None,
        base_history_log=None,
        raw_dump_path=None,
        raw_hash=None,
        raw_ingested_at=None,
        raw_schema=None,
        version_notes=None,
        version_code=None,
        progress_callback=None,
        cancel_event=None,
        operation_kind: str = "standard",
        replay_dependencies=None,
    ):
        self._ensure_control_metadata(cancel_event=cancel_event)
        if base_history_log == "" and not restored_from_version:
            self.archive_legacy_table_lineage(
                database_name,
                schema_name,
                table_name,
                cancel_event=cancel_event,
            )

        if version_code is None:
            self._raise_if_cancelled(cancel_event)
            self._notify_progress(progress_callback, "version", 72, "Calculando codigo da nova versao...")
            version_code = self.get_next_table_version(
                database_name,
                schema_name,
                table_name,
                cancel_event=cancel_event,
            )
        else:
            self._notify_progress(
                progress_callback,
                "version",
                72,
                f"Utilizando codigo reservado da nova versao: {version_code}.",
            )

        # base_history_log is kept only for compatibility with older callers.

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(progress_callback, "version", 90, "Montando registro final da nova versao...")
        new_entry = format_version_history_entry(
            version_code=version_code,
            version_title=version_title,
            created_by=requested_by,
            workstation_name=workstation_name,
            sql_recipe=sql_recipe,
            restored_from_version=restored_from_version,
            notes=version_notes,
        )

        version_history_log = new_entry
        version_history_format = self.VERSION_HISTORY_FORMAT_ENTRY
        normalized_operation_kind = str(operation_kind or "standard").strip() or "standard"

        inserted_version_sql = f"""
INSERT INTO {CONTROL_TABLE_VERSIONS} (
    database_name,
    schema_name,
    table_name,
    version_code,
    version_title,
    created_by,
    workstation_name,
    sql_recipe,
    restored_from_version,
    version_history_log,
    version_history_format,
    raw_dump_path,
    raw_hash,
    raw_ingested_at,
    raw_schema,
    operation_kind
) VALUES (
    {sql_literal(database_name)},
    {sql_literal(schema_name)},
    {sql_literal(table_name)},
    {sql_literal(version_code)},
    {sql_literal(version_title)},
    {sql_literal(requested_by)},
    {sql_literal(workstation_name)},
    {sql_literal(sql_recipe)},
    {sql_literal(restored_from_version) if restored_from_version else 'NULL'},
    {sql_literal(version_history_log)},
    {sql_literal(version_history_format)},
    {sql_literal(raw_dump_path) if raw_dump_path else 'NULL'},
    {sql_literal(raw_hash) if raw_hash else 'NULL'},
    {sql_literal(raw_ingested_at) if raw_ingested_at else 'NULL'},
    {sql_literal(raw_schema) if raw_schema else 'NULL'},
    {sql_literal(normalized_operation_kind)}
)
RETURNING id
""".strip()
        if replay_dependencies:
            dependency_insert_sql = self._build_version_dependency_insert_sql(
                "inserted_version",
                replay_dependencies,
            )
            sql = f"""
BEGIN;
WITH inserted_version AS (
    {inserted_version_sql}
)
{dependency_insert_sql};
COMMIT;
"""
        else:
            sql = f"BEGIN;\n{inserted_version_sql};\nCOMMIT;\n"
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -q -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(command, stdin_text=sql, cancel_event=cancel_event)
        self._notify_progress(progress_callback, "version", 100, "Versao registrada com sucesso.")
        return version_code

    @staticmethod
    def _build_server_version_history_expression(
        version_code_expression: str,
        version_title: str,
        requested_by: str,
        workstation_name: str,
        sql_recipe: str,
        version_notes: str,
    ) -> str:
        prefix_sql = sql_literal("=== VERSAO ")
        title_sql = sql_literal(
            " ===\nTitulo: " + version_title + "\nData/Hora: "
        )
        author_sql = sql_literal("\nAutor: " + requested_by)
        workstation_sql = sql_literal("\nComputador: " + workstation_name)
        notes_sql = sql_literal("\n\nNOTAS:\n" + version_notes)
        recipe_sql = sql_literal("\n\nSQL:\n" + sql_recipe)
        return " || ".join(
            [
                prefix_sql,
                version_code_expression,
                title_sql,
                "to_char(clock_timestamp(), 'YYYY-MM-DD HH24:MI:SS')",
                author_sql,
                workstation_sql,
                notes_sql,
                recipe_sql,
            ]
        )

    def register_cross_table_expansion_versions(
        self,
        expansion_result: dict,
        requested_by: str,
        workstation_name: str,
        progress_callback=None,
        cancel_event=None,
        timing_report: dict | None = None,
    ) -> list[dict]:
        if timing_report is None:
            timing_report = (expansion_result or {}).get("timing_report")
        with self.expansion_timing_scope(
            timing_report,
            "version",
            "Ensure control metadata for version registration",
        ):
            self._ensure_control_metadata(cancel_event=cancel_event)
        build_cpu_started_at = time.thread_time()
        database_name = str(
            (expansion_result or {}).get("source_database_name") or ""
        ).strip()
        source_schema_name = str(
            (expansion_result or {}).get("source_schema_name") or ""
        ).strip()
        source_table_name = str(
            (expansion_result or {}).get("source_table_name") or ""
        ).strip()
        raw_schemas = self._dedupe_non_empty_strings(
            (expansion_result or {}).get("raw_schemas")
        )
        destinations = list(
            (expansion_result or {}).get("destinations") or []
        )
        base_manifest = dict(
            (expansion_result or {}).get("dependency_manifest") or {}
        )
        if not database_name or not source_schema_name or not source_table_name:
            raise RuntimeError("The expansion result does not identify its source.")
        if not raw_schemas or not destinations or not base_manifest:
            raise RuntimeError(
                "The expansion result does not contain complete destinations or a manifest."
            )

        source_full_table_name = f"{source_schema_name}.{source_table_name}"
        source_versions = self._canonical_source_version_references(
            (expansion_result or {}).get("source_versions")
        )
        if not source_versions:
            raise RuntimeError(
                "The expansion result did not preserve Raw version references."
            )
        base_manifest["source_versions"] = source_versions
        base_manifest["raw_schemas"] = raw_schemas

        self._notify_progress(
            progress_callback,
            "version",
            5,
            "Preparing atomic destination version registration...",
        )
        sql_parts = [
            """
CREATE TEMP TABLE pgdm_expansion_timings (
    sequence_number integer PRIMARY KEY,
    stage_key text NOT NULL,
    item_name text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    rows_affected bigint,
    destination_name text,
    statement_index integer
) ON COMMIT PRESERVE ROWS;
""".strip(),
            "BEGIN;",
        ]
        normalized_destinations = []
        destination_lock_keys = []
        seen_destination_keys = set()
        for destination in destinations:
            schema_name = str(destination.get("schema_name") or "").strip()
            table_name = str(destination.get("pure_table_name") or "").strip()
            if not schema_name or not table_name:
                raise RuntimeError(
                    "A destination does not identify its table for version locking."
                )
            destination_key = (schema_name, table_name)
            if destination_key in seen_destination_keys:
                raise RuntimeError(
                    f"Destination {schema_name}.{table_name} was provided more than once."
                )
            seen_destination_keys.add(destination_key)
            destination_lock_keys.append(
                f"{CONTROL_SCHEMA}.table_versions:"
                f"{database_name}:{schema_name}:{table_name}"
            )
        timing_sequence = 1
        for lock_key in sorted(destination_lock_keys):
            lock_table_name = lock_key.rsplit(":", 1)[-1]
            sql_parts.append(
                "INSERT INTO pg_temp.pgdm_expansion_timings VALUES ("
                f"{timing_sequence}, 'version', "
                f"{sql_literal(f'Acquire version lock for {lock_table_name}')}, "
                "clock_timestamp(), NULL, NULL, "
                f"{sql_literal(lock_table_name)}, NULL);\n"
                "SELECT pg_advisory_xact_lock("
                f"hashtextextended({sql_literal(lock_key)}, 0)"
                ");\n"
                "UPDATE pg_temp.pgdm_expansion_timings SET finished_at = "
                f"clock_timestamp() WHERE sequence_number = {timing_sequence};"
            )
            timing_sequence += 1

        version_statement_sequence = 100
        for destination in destinations:
            schema_name = str(destination.get("schema_name") or "").strip()
            table_name = str(destination.get("pure_table_name") or "").strip()
            full_table_name = str(destination.get("table_name") or "").strip()
            version_title = str(destination.get("version_title") or "").strip()
            sql_recipe = str(destination.get("sql_recipe") or "").strip()
            destination_columns = self._dedupe_non_empty_strings(
                destination.get("destination_columns")
            )
            destination_read_relations = [
                {
                    "schema_name": str(schema_name).strip(),
                    "table_name": str(table_name).strip(),
                }
                for schema_name, table_name in (
                    destination.get("read_relations") or []
                )
                if str(schema_name).strip() and str(table_name).strip()
            ]
            if (
                not schema_name
                or not table_name
                or not full_table_name
                or not version_title
                or not sql_recipe
            ):
                raise RuntimeError(
                    "A destination does not contain enough metadata for versioning."
                )

            dependency_payload = dict(base_manifest)
            dependency_payload["destination_columns"] = destination_columns
            dependency_payload[
                "destination_read_relations"
            ] = destination_read_relations
            dependency_json = json.dumps(
                dependency_payload,
                ensure_ascii=False,
                sort_keys=True,
            )
            version_notes = (
                f"Versioned expansion from {source_full_table_name}. "
                "Exact raw_schema scope: "
                + ", ".join(raw_schemas)
                + ". No additional dump was created."
            )
            version_history_expression = (
                self._build_server_version_history_expression(
                    "next_version.version_code",
                    version_title,
                    requested_by,
                    workstation_name,
                    sql_recipe,
                    version_notes,
                )
            )
            sql_parts.append(
                (
                    "INSERT INTO pg_temp.pgdm_expansion_timings VALUES ("
                    f"{version_statement_sequence}, 'version', "
                    f"{sql_literal(f'Register version and dependency for {full_table_name}')}, "
                    "clock_timestamp(), NULL, NULL, "
                    f"{sql_literal(full_table_name)}, NULL);\n"
                    + f"""
WITH next_version_number AS (
    SELECT COALESCE(MAX(version_code::integer) + 1, 0) AS value
    FROM {CONTROL_TABLE_VERSIONS}
    WHERE database_name = {sql_literal(database_name)}
      AND schema_name = {sql_literal(schema_name)}
      AND table_name = {sql_literal(table_name)}
),
next_version AS (
    SELECT CASE
        WHEN value < 10000 THEN lpad(value::text, 4, '0')
        ELSE value::text
    END AS version_code
    FROM next_version_number
),
inserted_version AS (
    INSERT INTO {CONTROL_TABLE_VERSIONS} (
        database_name,
        schema_name,
        table_name,
        version_code,
        version_title,
        created_by,
        workstation_name,
        sql_recipe,
        restored_from_version,
        version_history_log,
        version_history_format,
        raw_dump_path,
        raw_hash,
        raw_ingested_at,
        raw_schema,
        operation_kind
    )
    SELECT
        {sql_literal(database_name)},
        {sql_literal(schema_name)},
        {sql_literal(table_name)},
        next_version.version_code,
        {sql_literal(version_title)},
        {sql_literal(requested_by)},
        {sql_literal(workstation_name)},
        {sql_literal(sql_recipe)},
        NULL,
        {version_history_expression},
        {sql_literal(self.VERSION_HISTORY_FORMAT_ENTRY)},
        NULL,
        NULL,
        NULL,
        NULL,
        {sql_literal(self.CROSS_TABLE_EXPANSION_OPERATION_KIND)}
    FROM next_version
    RETURNING id, version_code
),
inserted_dependency AS (
    INSERT INTO {CONTROL_TABLE_VERSION_DEPENDENCIES} (
        table_version_id,
        dependency_kind,
        source_database_name,
        source_schema_name,
        source_table_name,
        dependency_payload
    )
    SELECT
        inserted_version.id,
        {sql_literal(self.CROSS_TABLE_EXPANSION_DEPENDENCY_KIND)},
        {sql_literal(database_name)},
        {sql_literal(source_schema_name)},
        {sql_literal(source_table_name)},
        {sql_literal(dependency_json)}::jsonb
    FROM inserted_version
    RETURNING table_version_id
)
SELECT json_build_object(
    'table_name', {sql_literal(full_table_name)},
    'version_code', inserted_version.version_code,
    'dependency_count', (SELECT COUNT(*) FROM inserted_dependency)
)::text
FROM inserted_version;
""".strip()
                    + "\nUPDATE pg_temp.pgdm_expansion_timings SET "
                    "finished_at = clock_timestamp(), rows_affected = 1 "
                    f"WHERE sequence_number = {version_statement_sequence};"
                )
            )
            normalized_destinations.append(full_table_name)
            version_statement_sequence += 1

        sql_parts.extend(
            [
                """
INSERT INTO pg_temp.pgdm_expansion_timings VALUES (
    9000,
    'version',
    'Commit atomic version batch',
    clock_timestamp(),
    NULL,
    NULL,
    NULL,
    NULL
);
COMMIT;
UPDATE pg_temp.pgdm_expansion_timings
SET finished_at = clock_timestamp()
WHERE sequence_number = 9000;
""".strip(),
                """
SELECT json_build_object(
    'pgdm_expansion_timings', COALESCE(
        json_agg(
            json_build_object(
                'sequence', sequence_number,
                'stage', stage_key,
                'name', item_name,
                'duration_seconds', EXTRACT(
                    EPOCH FROM (finished_at - started_at)
                ),
                'rows', rows_affected,
                'destination', destination_name,
                'statement_index', statement_index
            ) ORDER BY sequence_number
        ),
        '[]'::json
    )
)::text
FROM pg_temp.pgdm_expansion_timings;
DROP TABLE pg_temp.pgdm_expansion_timings;
""".strip(),
            ]
        )
        self._append_expansion_timing(
            timing_report,
            "version",
            "Build atomic version registration SQL - local Python CPU",
            time.thread_time() - build_cpu_started_at,
            "local_python_cpu",
            details=(
                "Per-thread CPU time; blocking, SSH transit, and remote database "
                "wait are excluded."
            ),
        )
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} "
            "-X -qAt -v ON_ERROR_STOP=1 -f -"
        )
        with self.expansion_timing_scope(
            timing_report,
            "version",
            "Version database batch envelope",
            record_local_cpu=False,
            remote_include_in_total=False,
        ):
            output = self.run_remote_command(
                command,
                stdin_text="\n\n".join(sql_parts) + "\n",
                cancel_event=cancel_event,
            )
        with self.expansion_timing_scope(
            timing_report,
            "version",
            "Parse version registration results and timing telemetry",
        ):
            registered = []
            server_timing_items = []
            for line in output.splitlines():
                candidate = line.strip()
                if not candidate.startswith("{"):
                    continue
                try:
                    item = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("table_name") and item.get("version_code"):
                    registered.append(item)
                if "pgdm_expansion_timings" in item:
                    server_timing_items = list(
                        item.get("pgdm_expansion_timings") or []
                    )
            for item in server_timing_items:
                self._append_expansion_timing(
                    timing_report,
                    item.get("stage") or "version",
                    item.get("name") or "PostgreSQL versioning step",
                    float(item.get("duration_seconds") or 0.0),
                    "postgresql_backend_wall",
                    rows=item.get("rows"),
                    destination=item.get("destination"),
                    details=(
                        "Measured with PostgreSQL clock_timestamp() in the control "
                        "database session; SSH transport is excluded."
                    ),
                )

        registered_by_table = {
            str(item["table_name"]): item
            for item in registered
        }
        missing_tables = [
            table_name
            for table_name in normalized_destinations
            if table_name not in registered_by_table
        ]
        invalid_dependency_tables = [
            str(item.get("table_name"))
            for item in registered
            if int(item.get("dependency_count") or 0) != 1
        ]
        if missing_tables or invalid_dependency_tables:
            problems = []
            if missing_tables:
                problems.append(
                    "without version confirmation: " + ", ".join(missing_tables)
                )
            if invalid_dependency_tables:
                problems.append(
                    "without a unique manifest: "
                    + ", ".join(invalid_dependency_tables)
                )
            raise RuntimeError(
                "The control database did not confirm the version batch: "
                + "; ".join(problems)
                + "."
            )

        self._notify_progress(
            progress_callback,
            "version",
            100,
            f"{len(registered)} version(s) registered atomically.",
        )
        return [
            registered_by_table[table_name]
            for table_name in normalized_destinations
        ]

    def register_delete_audit(
        self,
        action_type: str,
        database_name,
        schema_name,
        table_name,
        object_name: str,
        requested_by: str,
        workstation_name: str,
        justification: str,
        backup_payload_path: str,
        executed_sql: str,
        cancel_event=None,
    ):
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
INSERT INTO {CONTROL_TABLE_DELETE_AUDIT} (
    action_type, database_name, schema_name, table_name, object_name,
    requested_by, workstation_name, justification,
    backup_payload_path, executed_sql
) VALUES (
    {sql_literal(action_type)},
    {sql_literal(database_name) if database_name else 'NULL'},
    {sql_literal(schema_name) if schema_name else 'NULL'},
    {sql_literal(table_name) if table_name else 'NULL'},
    {sql_literal(object_name)},
    {sql_literal(requested_by)},
    {sql_literal(workstation_name)},
    {sql_literal(justification)},
    {sql_literal(backup_payload_path)},
    {sql_literal(executed_sql)}
);
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(command, cancel_event=cancel_event)

    def get_deleted_table_archive_name(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        cancel_event=None,
    ) -> str:
        archive_regex = rf"^{re.escape(table_name)}__deleted_(\d+)$"
        sql = f"""
SELECT table_name
FROM {CONTROL_TABLE_VERSIONS}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND (
      table_name = {sql_literal(table_name)}
      OR table_name ~ {sql_literal(archive_regex)}
  )
UNION
SELECT table_name
FROM {CONTROL_TABLE_DELETE_AUDIT}
WHERE action_type = 'drop_table'
  AND database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name IS NOT NULL
  AND (
      table_name = {sql_literal(table_name)}
      OR table_name ~ {sql_literal(archive_regex)}
  );
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -At -c {shlex.quote(sql)}"
        )
        output = self.run_remote_command(command, cancel_event=cancel_event)
        suffix_pattern = re.compile(rf"^{re.escape(table_name)}__deleted_(\d+)$")
        max_suffix = 0
        current_names = {
            line.strip()
            for line in output.splitlines()
            if line.strip()
        }
        current_names.update(
            self._list_existing_archived_table_dirs(
                database_name,
                schema_name,
                cancel_event=cancel_event,
            )
        )

        for current_name in current_names:
            match = suffix_pattern.fullmatch(current_name)
            if match:
                max_suffix = max(max_suffix, int(match.group(1)))

        return f"{table_name}__deleted_{max_suffix + 1:04d}"

    @staticmethod
    def build_deleted_table_recovery_reference(
        database_name: str,
        schema_name: str,
        archived_table_name: str,
    ) -> str:
        return f"version_history::{database_name}::{schema_name}::{archived_table_name}"

    @staticmethod
    def build_deleted_table_no_recovery_reference(
        database_name: str,
        schema_name: str,
        archived_table_name: str,
    ) -> str:
        return f"no_recovery_snapshot::{database_name}::{schema_name}::{archived_table_name}"

    def archive_deleted_table_versions(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        archived_table_name: str,
        cancel_event=None,
    ):
        self._ensure_control_metadata(cancel_event=cancel_event)
        self.move_archived_table_raw_dumps(
            database_name,
            schema_name,
            table_name,
            archived_table_name,
            cancel_event=cancel_event,
        )

        sql = f"""
UPDATE {CONTROL_TABLE_VERSIONS}
SET table_name = {sql_literal(archived_table_name)}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(command, cancel_event=cancel_event)

    def archive_deleted_table_audit_entries(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        archived_table_name: str,
        cancel_event=None,
    ):
        self._ensure_control_metadata(cancel_event=cancel_event)
        sql = f"""
UPDATE {CONTROL_TABLE_DELETE_AUDIT}
SET table_name = {sql_literal(archived_table_name)}
WHERE database_name = {sql_literal(database_name)}
  AND schema_name = {sql_literal(schema_name)}
  AND table_name = {sql_literal(table_name)};
"""
        command = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(sql)}"
        )
        self.run_remote_command(command, cancel_event=cancel_event)

    def archive_legacy_table_lineage(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        cancel_event=None,
    ):
        version_count = self.get_table_version_count(
            database_name,
            schema_name,
            table_name,
            cancel_event=cancel_event,
        )
        audit_count = self.get_table_audit_count(
            database_name,
            schema_name,
            table_name,
            cancel_event=cancel_event,
        )

        if version_count <= 0 and audit_count <= 0:
            return None

        archived_table_name = self.get_deleted_table_archive_name(
            database_name,
            schema_name,
            table_name,
            cancel_event=cancel_event,
        )

        if version_count > 0:
            self.archive_deleted_table_versions(
                database_name,
                schema_name,
                table_name,
                archived_table_name,
                cancel_event=cancel_event,
            )

        if audit_count > 0:
            self.archive_deleted_table_audit_entries(
                database_name,
                schema_name,
                table_name,
                archived_table_name,
                cancel_event=cancel_event,
            )

        return archived_table_name

    def delete_database(self, db_name: str, info: dict):
        safe_db = slugify(db_name)
        base_dir = f"{self.remote_storage_root}/deletions/database/{safe_db}"
        dump_path = f"{base_dir}/{safe_db}.dump"

        dump_cmd = (
            f"mkdir -p {shlex.quote(base_dir)} && "
            f"pg_dump -h localhost -U {shlex.quote(self.sql_username)} -Fc "
            f"-d {shlex.quote(db_name)} -f {shlex.quote(dump_path)}"
        )
        self.run_remote_command(dump_cmd)

        executed_sql = f"DROP DATABASE {sql_ident(db_name)} WITH (FORCE);"
        drop_cmd = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(CONTROL_DB)} -X -c {shlex.quote(executed_sql)}"
        )
        self.run_remote_command(drop_cmd)

        self.register_delete_audit(
            action_type="drop_database",
            database_name=db_name,
            schema_name=None,
            table_name=None,
            object_name=db_name,
            requested_by=info["requested_by"],
            workstation_name=info["workstation_name"],
            justification=info["justification"],
            backup_payload_path=dump_path,
            executed_sql=executed_sql,
        )

    def delete_table(self, database_name: str, full_table_name: str, info: dict, cancel_event=None, progress_callback=None):
        schema_name, table_name = split_table_name(full_table_name)
        self.ensure_data_database_metadata(database_name, cancel_event=cancel_event)
        archived_table_name = self.get_deleted_table_archive_name(
            database_name,
            schema_name,
            table_name,
            cancel_event=cancel_event,
        )

        def report(stage_key: str, progress: float, message: str):
            if progress_callback:
                progress_callback(stage_key, progress, message)

        self._raise_if_cancelled(cancel_event)
        report("dump", 0, "Validando historico versionado para recuperacao...")
        version_count = self.get_table_version_count(
            database_name,
            schema_name,
            table_name,
            cancel_event=cancel_event,
        )

        if version_count > 0:
            recovery_reference = self.build_deleted_table_recovery_reference(
                database_name,
                schema_name,
                archived_table_name,
            )
            recovery_message = (
                f"Recuperacao preparada via historico de {version_count} versao(oes): "
                f"{recovery_reference}"
            )
        else:
            recovery_reference = self.build_deleted_table_no_recovery_reference(
                database_name,
                schema_name,
                archived_table_name,
            )
            recovery_message = (
                "Nenhum historico versionado foi encontrado. "
                f"Exclusao seguira sem referencia de recuperacao: {recovery_reference}"
            )

        self._raise_if_cancelled(cancel_event)
        report("dump", 100, recovery_message)

        drop_table_sql = f"DROP TABLE {sql_ident(schema_name)}.{sql_ident(table_name)} CASCADE;"
        delete_counter_sql = self._build_table_row_count_delete_sql(schema_name, table_name)
        executed_sql = f"BEGIN;\n{drop_table_sql}\n{delete_counter_sql}\nCOMMIT;\n"
        report("drop", 0, "Executando exclusao da tabela no banco...")
        drop_cmd = (
            f"psql -h localhost -U {shlex.quote(self.sql_username)} "
            f"-d {shlex.quote(database_name)} -X -q -v ON_ERROR_STOP=1"
        )
        self.run_remote_command(drop_cmd, stdin_text=executed_sql, cancel_event=cancel_event)
        self._raise_if_cancelled(cancel_event)
        report("drop", 100, "Tabela excluida no banco.")

        report("audit", 0, "Registrando auditoria da exclusao...")
        self.archive_deleted_table_versions(
            database_name,
            schema_name,
            table_name,
            archived_table_name,
            cancel_event=cancel_event,
        )
        self.register_delete_audit(
            action_type="drop_table",
            database_name=database_name,
            schema_name=schema_name,
            table_name=archived_table_name,
            object_name=full_table_name,
            requested_by=info["requested_by"],
            workstation_name=info["workstation_name"],
            justification=info["justification"],
            backup_payload_path=recovery_reference,
            executed_sql=executed_sql,
            cancel_event=cancel_event,
        )
        self._raise_if_cancelled(cancel_event)
        report("audit", 100, "Auditoria registrada.")

    def restore_table_version(
        self,
        database_name: str,
        version: dict,
        cancel_event=None,
        progress_callback=None,
    ):
        schema_name = version["schema_name"]
        table_name = version["table_name"]
        full_table_name = f"{schema_name}.{table_name}"
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            20,
            f"Buscando historico de versoes de {full_table_name}...",
        )
        versions = self._get_table_versions_for_replay(
            database_name,
            schema_name,
            table_name,
            cancel_event=cancel_event,
        )
        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            70,
            f"Montando plano de restauracao ate {version['version_code']}...",
        )
        replay_plan = self._build_replay_plan(versions, version["version_code"])
        self._preflight_replay_dependencies(
            database_name,
            full_table_name,
            replay_plan,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        self._notify_progress(
            progress_callback,
            "prepare",
            100,
            f"Plano pronto. {len(replay_plan)} versoes serao reaplicadas.",
        )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "drop",
            0,
            f"Removendo estado atual de {full_table_name}...",
        )
        self.drop_table_with_row_counter(
            database_name,
            full_table_name,
            if_exists=True,
            cancel_event=cancel_event,
        )
        self._notify_progress(
            progress_callback,
            "drop",
            100,
            "Estado atual removido. Iniciando reaplicacao das versoes.",
        )

        self._notify_progress(
            progress_callback,
            "replay",
            0,
            f"Reaplicando historico ate {version['version_code']}...",
        )
        self._replay_table_versions(
            database_name,
            full_table_name,
            replay_plan,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if self.table_exists(database_name, full_table_name, cancel_event=cancel_event):
            self.reconcile_table_row_count(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )


    def restore_table_version_to_new_table(
        self,
        database_name: str,
        version: dict,
        target_full_table_name: str,
        cancel_event=None,
        progress_callback=None,
    ):
        source_schema_name = version["schema_name"]
        source_table_name = version["table_name"]
        source_full_table_name = f"{source_schema_name}.{source_table_name}"
        target_schema_name, target_table_name = split_table_name(target_full_table_name)
        if target_schema_name != "public":
            raise RuntimeError("A restauracao em nova tabela suporta apenas o schema public.")

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            10,
            f"Validando tabela de destino {target_full_table_name}...",
        )
        if not self.database_exists(database_name, cancel_event=cancel_event):
            raise RuntimeError(f"A base {database_name} nao existe para restauracao.")
        if self.table_exists(database_name, target_full_table_name, cancel_event=cancel_event):
            raise RuntimeError(f"A tabela {target_full_table_name} ja existe na base {database_name}.")

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            35,
            f"Buscando historico de versoes de {source_full_table_name}...",
        )
        replay_versions = self._get_table_versions_for_replay(
            database_name,
            source_schema_name,
            source_table_name,
            cancel_event=cancel_event,
        )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            60,
            f"Montando plano de restauracao ate {version['version_code']}...",
        )
        replay_plan = self._build_replay_plan(replay_versions, version["version_code"])
        recipe_source_table_name = self.resolve_original_table_name(source_table_name)
        self._preflight_replay_dependencies(
            database_name,
            target_full_table_name,
            replay_plan,
            recipe_transform=lambda recipe: self._rewrite_recipe_for_target_table(
                recipe,
                source_schema_name,
                [source_table_name, recipe_source_table_name],
                target_schema_name,
                target_table_name,
            ),
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "prepare",
            85,
            f"Clonando historico para {target_full_table_name}...",
        )
        self.clone_table_version_lineage(
            database_name,
            source_schema_name,
            source_table_name,
            recipe_source_table_name,
            target_schema_name,
            target_table_name,
            version["version_code"],
            cancel_event=cancel_event,
        )
        self._notify_progress(
            progress_callback,
            "prepare",
            100,
            f"Plano pronto. {len(replay_plan)} versoes serao reaplicadas na nova tabela.",
        )

        self._raise_if_cancelled(cancel_event)
        self._notify_progress(
            progress_callback,
            "drop",
            0,
            f"Preparando tabela de destino {target_full_table_name}...",
        )
        self.drop_table_with_row_counter(
            database_name,
            target_full_table_name,
            if_exists=True,
            cancel_event=cancel_event,
        )
        self._notify_progress(
            progress_callback,
            "drop",
            100,
            "Destino preparado. Iniciando reaplicacao das versoes.",
        )

        self._notify_progress(
            progress_callback,
            "replay",
            0,
            f"Reaplicando historico em {target_full_table_name}...",
        )
        self._replay_table_versions(
            database_name,
            target_full_table_name,
            replay_plan,
            recipe_transform=lambda recipe: self._rewrite_recipe_for_target_table(
                recipe,
                source_schema_name,
                [source_table_name, recipe_source_table_name],
                target_schema_name,
                target_table_name,
            ),
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if self.table_exists(database_name, target_full_table_name, cancel_event=cancel_event):
            self.reconcile_table_row_count(
                database_name,
                target_full_table_name,
                cancel_event=cancel_event,
            )
