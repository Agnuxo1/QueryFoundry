import unittest
from unittest.mock import patch

from services.postgres_service import PostgresAdminService
from ui.dialogs import ExpansionTimingReportDialog


class ExpansionTimingTests(unittest.TestCase):
    def test_managed_insert_can_emit_postgresql_timing_and_row_count(self):
        sql = PostgresAdminService._build_managed_dml_sql(
            "INSERT INTO public.target_table (value) SELECT 1",
            "public",
            "target_table",
            "add",
            timing_sequence=12,
            timing_stage="execute",
            timing_name="Insert target rows",
            timing_destination="public.target_table",
            timing_statement_index=2,
        )

        self.assertIn("pgdm_timing_started_at", sql)
        self.assertIn("GET DIAGNOSTICS pgdm_affected_rows = ROW_COUNT", sql)
        self.assertIn("pg_temp.pgdm_expansion_timings", sql)
        self.assertIn("'Insert target rows'", sql)
        self.assertIn("'public.target_table'", sql)

    def test_txt_report_contains_rows_subtotals_and_measured_total(self):
        report = PostgresAdminService.create_cross_table_expansion_timing_report(
            "challenge",
            "public.raw_data",
            ["group001"],
            ["public.table1"],
        )
        report.update(
            {
                "completed_at": "2026-09-02T10:01:00-03:00",
                "source_rows": 100,
                "total_rows_inserted": 240,
                "destination_rows": [
                    {"table_name": "public.table1", "rows": 100},
                    {"table_name": "public.table2", "rows": 140},
                ],
            }
        )
        PostgresAdminService._append_expansion_timing(
            report,
            "prepare",
            "Parse recipes",
            0.25,
            "local_python_cpu",
        )
        PostgresAdminService._append_expansion_timing(
            report,
            "execute",
            "Insert rows",
            1.5,
            "postgresql_backend_wall",
            rows=100,
            destination="public.table1",
        )
        PostgresAdminService._append_expansion_timing(
            report,
            "execute",
            "Diagnostic envelope",
            9.0,
            "remote_server_wall",
            include_in_total=False,
        )

        text = ExpansionTimingReportDialog.build_report_text(report)

        self.assertIn("Source rows:   100", text)
        self.assertIn("Inserted rows: 240", text)
        self.assertIn("public.table2: 140", text)
        self.assertIn("Stage subtotal: 1.500 s", text)
        self.assertIn("MEASURED PROCESSING TOTAL: 1.750 s", text)
        self.assertIn("diagnostic only; excluded from totals", text)

    def test_expansion_batch_contains_server_side_substep_instrumentation(self):
        service = PostgresAdminService()
        captured = {}
        manifest = {
            "raw_schemas": ["group001"],
            "expected_row_count": 2,
            "expected_distinct_raw_hash_count": 1,
            "raw_hash_manifest": [
                {"raw_schema": "group001", "raw_hash": "hash", "row_count": 2}
            ],
            "source_columns": [],
        }
        telemetry = {
            "pgdm_expansion_timings": [
                {
                    "sequence": 10,
                    "stage": "execute",
                    "name": "Insert into public.table1 (statement 1)",
                    "duration_seconds": 1.25,
                    "rows": 2,
                    "destination": "public.table1",
                    "statement_index": 1,
                }
            ]
        }

        def fake_remote_command(_command, **kwargs):
            captured["sql"] = kwargs.get("stdin_text") or ""
            return f"{__import__('json').dumps(manifest)}\n{__import__('json').dumps(telemetry)}"

        report = service.create_cross_table_expansion_timing_report(
            "challenge",
            "public.raw_data",
            ["group001"],
            ["public.table1"],
        )
        destinations = [
            {
                "table_name": "public.table1",
                "version_title": "Expansion",
                "sql": (
                    "INSERT INTO public.table1 (id_column_2) "
                    "SELECT source.raw_id FROM {{source}} AS source"
                ),
            }
        ]

        with (
            patch.object(service, "table_exists", return_value=True),
            patch.object(
                service,
                "get_table_column_definitions",
                return_value=[
                    {"name": "raw_schema", "type": "text"},
                    {"name": "raw_hash", "type": "text"},
                ],
            ),
            patch.object(
                service,
                "get_raw_schema_version_references",
                return_value=[{"raw_schema": "group001"}],
            ),
            patch.object(service, "_validate_cross_table_expansion_functions"),
            patch.object(service, "ensure_data_database_metadata"),
            patch.object(service, "get_table_row_count", return_value=0),
            patch.object(service, "_validate_managed_sql_dependencies"),
            patch.object(service, "run_remote_command", side_effect=fake_remote_command),
        ):
            result = service.execute_cross_table_expansion(
                "challenge",
                "public.raw_data",
                ["group001"],
                destinations,
                timing_report=report,
            )

        self.assertIn("CREATE TEMP TABLE pgdm_expansion_timings", captured["sql"])
        self.assertIn("Acquire destination table locks", captured["sql"])
        self.assertIn("Scan source and build dependency manifest", captured["sql"])
        self.assertIn("GET DIAGNOSTICS pgdm_affected_rows = ROW_COUNT", captured["sql"])
        self.assertIn("Commit expansion transaction", captured["sql"])
        self.assertEqual(result["timing_report"]["source_rows"], 2)
        self.assertEqual(result["timing_report"]["total_rows_inserted"], 2)
        self.assertEqual(
            result["timing_report"]["destination_rows"],
            [{"table_name": "public.table1", "rows": 2}],
        )


if __name__ == "__main__":
    unittest.main()
