from .sqlite_cache import SQLiteFilterCache
from utils import split_table_name


class FilterFacetService:
    def __init__(self, postgres_service, cache: SQLiteFilterCache | None = None):
        self.postgres_service = postgres_service
        self.cache = cache or SQLiteFilterCache()

    def close(self):
        return None

    def ensure_cache(
        self,
        database_name: str,
        full_table_name: str,
        progress_callback=None,
        cancel_event=None,
    ):
        schema_name, table_name = split_table_name(full_table_name)
        source_version_code = self._get_source_version_code(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        source_change_token = self._get_source_change_token(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        if self.cache.facets_match_source_state(
            database_name,
            schema_name,
            table_name,
            source_version_code,
            source_change_token,
        ):
            return

        self.rebuild_cache(
            database_name,
            full_table_name,
            source_version_code=source_version_code,
            source_change_token=source_change_token,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

    def rebuild_cache(
        self,
        database_name: str,
        full_table_name: str,
        source_version_code: str | None = None,
        source_change_token: str | None = None,
        progress_callback=None,
        cancel_event=None,
    ):
        schema_name, table_name = split_table_name(full_table_name)
        if source_version_code is None:
            source_version_code = self._get_source_version_code(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        if source_change_token is None:
            source_change_token = self._get_source_change_token(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        column_infos = self.postgres_service.get_table_columns_info(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )

        self.cache.begin_table_rebuild(database_name, schema_name, table_name)
        try:
            total_columns = len(column_infos)
            for index, column_info in enumerate(column_infos, start=1):
                self.postgres_service._raise_if_cancelled(cancel_event)
                column_name = column_info["column_name"]
                self._notify(
                    progress_callback,
                    "refresh",
                    45 + (35 * index / max(total_columns, 1)),
                    f"Atualizando cache local de filtro: {column_name} ({index}/{total_columns}).",
                )
                rows = self.postgres_service.get_table_column_facet_rows(
                    database_name,
                    full_table_name,
                    column_info,
                    cancel_event=cancel_event,
                )
                self.cache.replace_column_rows(
                    database_name,
                    schema_name,
                    table_name,
                    column_name,
                    rows,
                )

            self.cache.finish_table_rebuild(
                database_name,
                schema_name,
                table_name,
                column_count=total_columns,
                source_version_code=source_version_code,
                source_change_token=source_change_token,
            )
        except Exception:
            self.cache.clear_table(database_name, schema_name, table_name)
            raise

    def clear_table(self, database_name: str, full_table_name: str):
        schema_name, table_name = split_table_name(full_table_name)
        self.cache.clear_table(database_name, schema_name, table_name)

    def mark_table_stale(self, database_name: str, full_table_name: str):
        schema_name, table_name = split_table_name(full_table_name)
        self.cache.mark_table_stale(database_name, schema_name, table_name)

    def clear_database(self, database_name: str):
        self.cache.clear_database(database_name)

    def checkpoint_cache(self):
        self.cache.checkpoint()

    def get_values(
        self,
        database_name: str,
        full_table_name: str,
        column_name: str,
        filters=None,
        search_text: str | None = None,
        is_numeric: bool = False,
        limit: int = 1000,
    ) -> list[dict]:
        schema_name, table_name = split_table_name(full_table_name)
        return self.cache.get_values(
            database_name,
            schema_name,
            table_name,
            column_name,
            filters=filters,
            search_text=search_text,
            is_numeric=is_numeric,
            limit=limit,
        )

    def _get_source_version_code(self, database_name: str, full_table_name: str, cancel_event=None):
        if hasattr(self.postgres_service, "get_latest_table_version_code"):
            return self.postgres_service.get_latest_table_version_code(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )

        versions = self.postgres_service.list_table_versions(
            database_name,
            full_table_name,
            cancel_event=cancel_event,
        )
        if not versions:
            return None
        return versions[0].get("version_code")

    def _get_source_change_token(self, database_name: str, full_table_name: str, cancel_event=None):
        if hasattr(self.postgres_service, "get_table_change_token"):
            return self.postgres_service.get_table_change_token(
                database_name,
                full_table_name,
                cancel_event=cancel_event,
            )
        return None

    @staticmethod
    def _notify(progress_callback, stage_key: str, progress: float, message: str):
        if progress_callback:
            progress_callback(stage_key, progress, message)
