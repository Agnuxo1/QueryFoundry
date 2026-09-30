"""Optional GUI integration; all upstream integrity checks remain active."""
import os
from .recipes import ROOT, MODES, rewrite
from services.postgres_service import PostgresAdminService

class QueryFoundryService(PostgresAdminService):
    @classmethod
    def _prepare_cross_table_expansion(cls, source_full_table_name, raw_schemas, destinations):
        mode = os.environ.get('QF_RECIPE_MODE', 'baseline')
        if mode not in MODES:
            raise ValueError('Invalid QF_RECIPE_MODE')
        originals = {p.read_text(encoding='utf-8').strip() for p in (ROOT / 'app/SQL files').glob('*.sql')}
        converted = []
        for destination in destinations or []:
            item = dict(destination)
            sql = str(item.get('sql') or '')
            # Unknown user recipes use the untouched upstream path.
            if mode != 'baseline' and sql.strip() in originals:
                item['sql'] = rewrite(sql, mode)
            converted.append(item)
        return super()._prepare_cross_table_expansion(source_full_table_name, raw_schemas, converted)
