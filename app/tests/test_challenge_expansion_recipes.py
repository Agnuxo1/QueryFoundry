import unittest
from pathlib import Path

from services.postgres_service import PostgresAdminService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECIPE_DIR = PROJECT_ROOT / "SQL files"
RECIPE_DESTINATIONS = (
    ("01_table3.sql", "public.table3"),
    ("02_table4.sql", "public.table4"),
    ("03_table1.sql", "public.table1"),
    ("04_table5.sql", "public.table5"),
    ("05_table6.sql", "public.table6"),
    ("06_table2.sql", "public.table2"),
)


class ChallengeExpansionRecipeTests(unittest.TestCase):
    def test_all_recipes_are_accepted_in_dependency_order(self):
        missing_files = [
            file_name
            for file_name, _table_name in RECIPE_DESTINATIONS
            if not (RECIPE_DIR / file_name).is_file()
        ]
        if missing_files:
            self.fail("Missing required expansion recipes: " + ", ".join(missing_files))
        destinations = [
            {
                "table_name": table_name,
                "version_title": "Synthetic expansion",
                "sql": (RECIPE_DIR / file_name).read_text(encoding="utf-8"),
            }
            for file_name, table_name in RECIPE_DESTINATIONS
        ]

        prepared = PostgresAdminService._prepare_cross_table_expansion(
            "public.raw_data",
            ["group001"],
            destinations,
        )

        self.assertEqual(
            [item["table_name"] for item in prepared["destinations"]],
            [table_name for _file_name, table_name in RECIPE_DESTINATIONS],
        )
        self.assertEqual(
            {
                item["table_name"]: item["read_relations"]
                for item in prepared["destinations"]
            },
            {
                "public.table3": [],
                "public.table4": [],
                "public.table1": [("public", "table3")],
                "public.table5": [("public", "table1")],
                "public.table6": [("public", "table1"), ("public", "table4")],
                "public.table2": [("public", "table1")],
            },
        )
        self.assertEqual(
            {
                function_name
                for item in prepared["destinations"]
                for _schema_name, function_name in item["function_references"]
            },
            {"ascii", "btrim", "left", "right", "round", "upper"},
        )


if __name__ == "__main__":
    unittest.main()
