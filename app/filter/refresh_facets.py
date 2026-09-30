from .facet_service import FilterFacetService


def refresh_table_facets(
    postgres_service,
    database_name: str,
    full_table_name: str,
    progress_callback=None,
    cancel_event=None,
):
    service = FilterFacetService(postgres_service)
    service.rebuild_cache(
        database_name,
        full_table_name,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )

