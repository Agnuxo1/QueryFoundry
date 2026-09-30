import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path


DEFAULT_CACHE_PATH = Path(__file__).with_name("filter_cache.sqlite")


class SQLiteFilterCache:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_CACHE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def connect(self, timeout: float = 60):
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def ensure_schema(self):
        with self.connect() as conn:
            conn.executescript(
                """
CREATE TABLE IF NOT EXISTS facet_sources (
    database_name TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    column_count INTEGER NOT NULL DEFAULT 0,
    source_version_code TEXT,
    source_change_token TEXT,
    rebuilt_at TEXT NOT NULL,
    PRIMARY KEY (database_name, schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS facet_rows (
    database_name TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    row_key TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    value_text TEXT NOT NULL,
    display_value TEXT NOT NULL,
    is_null INTEGER NOT NULL,
    numeric_value REAL,
    search_text TEXT NOT NULL,
    PRIMARY KEY (database_name, schema_name, table_name, column_name, row_key)
);

CREATE TABLE IF NOT EXISTS facet_values (
    database_name TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    value_text TEXT NOT NULL,
    display_value TEXT NOT NULL,
    is_null INTEGER NOT NULL,
    numeric_value REAL,
    row_count INTEGER NOT NULL,
    search_text TEXT NOT NULL,
    PRIMARY KEY (database_name, schema_name, table_name, column_name, is_null, value_hash)
);

CREATE INDEX IF NOT EXISTS idx_facet_values_lookup
ON facet_values (database_name, schema_name, table_name, column_name, is_null, display_value);

CREATE INDEX IF NOT EXISTS idx_facet_values_search
ON facet_values (database_name, schema_name, table_name, column_name, search_text);

CREATE INDEX IF NOT EXISTS idx_facet_values_numeric
ON facet_values (database_name, schema_name, table_name, column_name, numeric_value)
WHERE numeric_value IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_facet_rows_value
ON facet_rows (database_name, schema_name, table_name, column_name, value_hash, row_key);

CREATE INDEX IF NOT EXISTS idx_facet_rows_text
ON facet_rows (database_name, schema_name, table_name, column_name, value_text, row_key);

CREATE INDEX IF NOT EXISTS idx_facet_rows_row
ON facet_rows (database_name, schema_name, table_name, row_key);

CREATE INDEX IF NOT EXISTS idx_facet_rows_numeric
ON facet_rows (database_name, schema_name, table_name, column_name, numeric_value)
WHERE numeric_value IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_facet_rows_search
ON facet_rows (database_name, schema_name, table_name, column_name, search_text);
"""
            )
            self._ensure_column(conn, "facet_sources", "source_version_code", "TEXT")
            self._ensure_column(conn, "facet_sources", "source_change_token", "TEXT")

    @staticmethod
    def _ensure_column(conn, table_name: str, column_name: str, column_definition: str):
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def facets_exist(self, database_name: str, schema_name: str, table_name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
SELECT 1
FROM facet_sources
WHERE database_name = ?
  AND schema_name = ?
  AND table_name = ?
LIMIT 1
""",
                (database_name, schema_name, table_name),
            ).fetchone()
            return row is not None

    def facets_match_source_state(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        source_version_code: str | None,
        source_change_token: str | None = None,
    ) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
SELECT source_version_code, source_change_token
FROM facet_sources
WHERE database_name = ?
  AND schema_name = ?
  AND table_name = ?
LIMIT 1
""",
                (database_name, schema_name, table_name),
            ).fetchone()
            if row is None:
                return False
            return (
                self._normalize_source_version(row["source_version_code"]) == self._normalize_source_version(
                    source_version_code
                )
                and self._normalize_source_token(row["source_change_token"]) == self._normalize_source_token(
                    source_change_token
                )
            )

    def clear_table(self, database_name: str, schema_name: str, table_name: str):
        with self.connect() as conn:
            self._clear_table(conn, database_name, schema_name, table_name)

    def mark_table_stale(self, database_name: str, schema_name: str, table_name: str):
        with self.connect(timeout=2) as conn:
            conn.execute(
                """
DELETE FROM facet_sources
WHERE database_name = ?
  AND schema_name = ?
  AND table_name = ?
""",
                (database_name, schema_name, table_name),
            )

    def clear_database(self, database_name: str):
        with self.connect() as conn:
            for table in ("facet_values", "facet_rows", "facet_sources"):
                conn.execute(f"DELETE FROM {table} WHERE database_name = ?", (database_name,))

    def checkpoint(self):
        with self.connect(timeout=2) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def begin_table_rebuild(self, database_name: str, schema_name: str, table_name: str):
        with self.connect() as conn:
            self._clear_table(conn, database_name, schema_name, table_name)

    def replace_column_rows(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        column_name: str,
        rows,
    ):
        with self.connect() as conn:
            conn.execute(
                """
DELETE FROM facet_rows
WHERE database_name = ?
  AND schema_name = ?
  AND table_name = ?
  AND column_name = ?
""",
                (database_name, schema_name, table_name, column_name),
            )
            conn.execute(
                """
DELETE FROM facet_values
WHERE database_name = ?
  AND schema_name = ?
  AND table_name = ?
  AND column_name = ?
""",
                (database_name, schema_name, table_name, column_name),
            )
            conn.executemany(
                """
INSERT INTO facet_rows (
    database_name,
    schema_name,
    table_name,
    column_name,
    row_key,
    value_hash,
    value_text,
    display_value,
    is_null,
    numeric_value,
    search_text
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                (
                    self._prepare_row(database_name, schema_name, table_name, column_name, row)
                    for row in rows
                ),
            )
            conn.execute(
                """
INSERT INTO facet_values (
    database_name,
    schema_name,
    table_name,
    column_name,
    value_hash,
    value_text,
    display_value,
    is_null,
    numeric_value,
    row_count,
    search_text
)
SELECT
    database_name,
    schema_name,
    table_name,
    column_name,
    value_hash,
    value_text,
    display_value,
    is_null,
    MIN(numeric_value),
    COUNT(*),
    MIN(search_text)
FROM facet_rows
WHERE database_name = ?
  AND schema_name = ?
  AND table_name = ?
  AND column_name = ?
GROUP BY
    database_name,
    schema_name,
    table_name,
    column_name,
    value_hash,
    value_text,
    display_value,
    is_null
""",
                (database_name, schema_name, table_name, column_name),
            )

    def finish_table_rebuild(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        column_count: int,
        source_version_code: str | None = None,
        source_change_token: str | None = None,
    ):
        with self.connect() as conn:
            conn.execute(
                """
INSERT INTO facet_sources (
    database_name,
    schema_name,
    table_name,
    column_count,
    source_version_code,
    source_change_token,
    rebuilt_at
)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (database_name, schema_name, table_name)
DO UPDATE SET
    column_count = excluded.column_count,
    source_version_code = excluded.source_version_code,
    source_change_token = excluded.source_change_token,
    rebuilt_at = excluded.rebuilt_at
""",
                (
                    database_name,
                    schema_name,
                    table_name,
                    int(column_count or 0),
                    self._normalize_source_version(source_version_code),
                    self._normalize_source_token(source_change_token),
                    datetime.utcnow().isoformat(timespec="seconds"),
                ),
            )

    def get_values(
        self,
        database_name: str,
        schema_name: str,
        table_name: str,
        column_name: str,
        filters=None,
        search_text: str | None = None,
        is_numeric: bool = False,
        limit: int = 1000,
    ) -> list[dict]:
        if not self.facets_exist(database_name, schema_name, table_name):
            return []

        filter_rows = self._normalize_filter_rows(filters, excluded_column=column_name)
        for filter_column, selected_values in (filters or {}).items():
            if filter_column != column_name and selected_values == []:
                return []

        limit_value = max(1, min(int(limit or 1000), 10000))
        search_sql, search_params = self._build_search_condition(
            search_text,
            is_numeric=is_numeric,
            table_alias="source_values",
        )

        with self.connect() as conn:
            if not filter_rows:
                rows = conn.execute(
                    f"""
SELECT is_null, value_text, row_count
FROM facet_values AS source_values
WHERE database_name = ?
  AND schema_name = ?
  AND table_name = ?
  AND column_name = ?
  {search_sql}
ORDER BY is_null DESC, display_value
LIMIT ?
""",
                    (
                        database_name,
                        schema_name,
                        table_name,
                        column_name,
                        *search_params,
                        limit_value,
                    ),
                ).fetchall()
            else:
                self._load_filter_rows(conn, filter_rows)
                required_columns = len({row["column_name"] for row in filter_rows})
                rows = conn.execute(
                    f"""
WITH matched_rows AS (
    SELECT facet_row.row_key
    FROM facet_rows AS facet_row
    JOIN temp_filter_values AS fv
      ON fv.column_name = facet_row.column_name
    WHERE facet_row.database_name = ?
      AND facet_row.schema_name = ?
      AND facet_row.table_name = ?
      AND (
          (
              COALESCE(fv.operator, 'eq') = 'eq'
              AND (
                  (fv.is_null = 1 AND facet_row.is_null = 1)
                  OR (fv.is_null = 0 AND facet_row.value_text = fv.value_text)
              )
          )
          OR (
              fv.operator = 'eq_numeric'
              AND facet_row.numeric_value = CAST(fv.numeric_value AS REAL)
          )
          OR (
              fv.operator = 'gt'
              AND facet_row.numeric_value > CAST(fv.numeric_value AS REAL)
          )
          OR (
              fv.operator = 'lt'
              AND facet_row.numeric_value < CAST(fv.numeric_value AS REAL)
          )
          OR (
              fv.operator = 'between'
              AND facet_row.numeric_value BETWEEN CAST(fv.lower_value AS REAL) AND CAST(fv.upper_value AS REAL)
          )
      )
    GROUP BY facet_row.row_key
    HAVING COUNT(DISTINCT facet_row.column_name) = ?
)
SELECT source_values.is_null, source_values.value_text, COUNT(*) AS row_count
FROM facet_rows AS source_values
JOIN matched_rows
  ON matched_rows.row_key = source_values.row_key
WHERE source_values.database_name = ?
  AND source_values.schema_name = ?
  AND source_values.table_name = ?
  AND source_values.column_name = ?
  {search_sql}
GROUP BY
    source_values.is_null,
    source_values.value_hash,
    source_values.value_text,
    source_values.display_value
ORDER BY source_values.is_null DESC, source_values.display_value
LIMIT ?
""",
                    (
                        database_name,
                        schema_name,
                        table_name,
                        required_columns,
                        database_name,
                        schema_name,
                        table_name,
                        column_name,
                        *search_params,
                        limit_value,
                    ),
                ).fetchall()

        return [
            {
                "is_null": bool(row["is_null"]),
                "value": row["value_text"],
                "count": int(row["row_count"] or 0),
            }
            for row in rows
        ]

    @staticmethod
    def _clear_table(conn, database_name: str, schema_name: str, table_name: str):
        params = (database_name, schema_name, table_name)
        conn.execute(
            "DELETE FROM facet_values WHERE database_name = ? AND schema_name = ? AND table_name = ?",
            params,
        )
        conn.execute(
            "DELETE FROM facet_rows WHERE database_name = ? AND schema_name = ? AND table_name = ?",
            params,
        )
        conn.execute(
            "DELETE FROM facet_sources WHERE database_name = ? AND schema_name = ? AND table_name = ?",
            params,
        )

    @staticmethod
    def _prepare_row(
        database_name: str,
        schema_name: str,
        table_name: str,
        column_name: str,
        row: dict,
    ) -> tuple:
        is_null = 1 if row.get("is_null") else 0
        value_text = "" if is_null else str(row.get("value", ""))
        numeric_value = SQLiteFilterCache._coerce_float(row.get("numeric_value"))
        value_hash_source = "<NULL>" if is_null else value_text
        value_hash = hashlib.md5(value_hash_source.encode("utf-8")).hexdigest()

        return (
            database_name,
            schema_name,
            table_name,
            column_name,
            str(row.get("row_key", "")),
            value_hash,
            value_text,
            value_text,
            is_null,
            numeric_value,
            value_text[:1000].lower(),
        )

    @staticmethod
    def _coerce_float(value):
        if value is None or value == "":
            return None
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_source_version(value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_source_token(value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @classmethod
    def _normalize_filter_rows(cls, filters=None, excluded_column: str | None = None) -> list[dict]:
        rows = []
        if not filters:
            return rows

        for column_name, selected_values in filters.items():
            if column_name == excluded_column or selected_values is None:
                continue

            for item in selected_values:
                operator = item.get("operator", "eq")
                rows.append(
                    {
                        "column_name": column_name,
                        "operator": operator,
                        "is_null": 1 if item.get("is_null") else 0,
                        "value_text": "" if item.get("is_null") else str(item.get("value", "")),
                        "numeric_value": item.get("numeric_value"),
                        "lower_value": item.get("lower_value"),
                        "upper_value": item.get("upper_value"),
                    }
                )
        return rows

    @staticmethod
    def _load_filter_rows(conn, filter_rows: list[dict]):
        conn.execute("DROP TABLE IF EXISTS temp_filter_values")
        conn.execute(
            """
CREATE TEMP TABLE temp_filter_values (
    column_name TEXT NOT NULL,
    operator TEXT,
    is_null INTEGER NOT NULL,
    value_text TEXT NOT NULL,
    numeric_value TEXT,
    lower_value TEXT,
    upper_value TEXT
)
"""
        )
        conn.executemany(
            """
INSERT INTO temp_filter_values (
    column_name,
    operator,
    is_null,
    value_text,
    numeric_value,
    lower_value,
    upper_value
)
VALUES (?, ?, ?, ?, ?, ?, ?)
""",
            [
                (
                    row["column_name"],
                    row["operator"],
                    row["is_null"],
                    row["value_text"],
                    row.get("numeric_value"),
                    row.get("lower_value"),
                    row.get("upper_value"),
                )
                for row in filter_rows
            ],
        )
        conn.execute("CREATE INDEX idx_temp_filter_values_column ON temp_filter_values (column_name)")

    @classmethod
    def _build_search_condition(
        cls,
        search_text: str | None,
        is_numeric: bool,
        table_alias: str,
    ) -> tuple[str, list]:
        normalized_search = str(search_text or "").strip()
        if not normalized_search:
            return "", []

        numeric_expression = cls.parse_numeric_filter_expression(normalized_search) if is_numeric else None
        if numeric_expression:
            operator = numeric_expression["operator"]
            if operator == "eq_numeric":
                return f"AND {table_alias}.numeric_value = ?", [
                    cls._coerce_float(numeric_expression["numeric_value"])
                ]
            if operator == "gt":
                return f"AND {table_alias}.numeric_value > ?", [
                    cls._coerce_float(numeric_expression["numeric_value"])
                ]
            if operator == "lt":
                return f"AND {table_alias}.numeric_value < ?", [
                    cls._coerce_float(numeric_expression["numeric_value"])
                ]
            if operator == "between":
                return f"AND {table_alias}.numeric_value BETWEEN ? AND ?", [
                    cls._coerce_float(numeric_expression["lower_value"]),
                    cls._coerce_float(numeric_expression["upper_value"]),
                ]

        return f"AND {table_alias}.search_text LIKE ? ESCAPE '\\'", [
            f"%{cls._escape_like(normalized_search.lower())}%"
        ]

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _normalize_numeric_text(value: str) -> str:
        return str(value).strip().replace(",", ".")

    @classmethod
    def parse_numeric_filter_expression(cls, text: str):
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
            return {
                "operator": "between",
                "lower_value": cls._normalize_numeric_text(match.group(1)),
                "upper_value": cls._normalize_numeric_text(match.group(2)),
            }

        if re.fullmatch(number_pattern, normalized):
            return {
                "operator": "eq_numeric",
                "numeric_value": cls._normalize_numeric_text(normalized),
            }

        return None
