"""Narrow, reviewable rewrites of the exact six organizer recipes.

Only JSON text extraction changes. Casts, filters, joins, ordering and output
columns stay intact. OFFSET 0 is a per-row planner barrier, not a disk stage.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ORDER = ('table3', 'table4', 'table1', 'table5', 'table6', 'table2')
MODES = ('baseline', 'payload_once', 'fields_once')
EXTRACTION = re.compile(r"source_row\.raw\s*->\s*'values'\s*->>\s*'([a-z_]+[0-9]+)'")
ANCHOR = 'FROM {{source}} AS source_row'

def rewrite(sql: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError('Unknown experiment mode')
    if mode == 'baseline':
        return sql
    fields = sorted(set(EXTRACTION.findall(sql)))
    if not fields or sql.count(ANCHOR) != 1:
        raise ValueError('Unsupported recipe structure; refusing rewrite')
    payload = "\nCROSS JOIN LATERAL (\n    SELECT source_row.raw -> 'values' AS payload OFFSET 0\n) AS qf_payload"
    if mode == 'payload_once':
        result = EXTRACTION.sub(lambda m: "qf_payload.payload ->> '" + m[1] + "'", sql)
        return result.replace(ANCHOR, ANCHOR + payload)
    definitions = ',\n        '.join("qf_payload.payload ->> '" + f + "' AS " + f for f in fields)
    extracted = '\nCROSS JOIN LATERAL (\n    SELECT ' + definitions + '\n    OFFSET 0\n) AS qf_fields'
    result = EXTRACTION.sub(lambda m: 'qf_fields.' + m[1], sql)
    return result.replace(ANCHOR, ANCHOR + payload + extracted)

def destinations(mode='baseline'):
    files = sorted((ROOT / 'app/SQL files').glob('*.sql'))
    if len(files) != 6:
        raise RuntimeError('Expected exactly six official recipes')
    return [{'table_name':'public.' + table, 'version_title':'QueryFoundry ' + mode,
             'sql':rewrite(path.read_text(encoding='utf-8'), mode)}
            for table, path in zip(ORDER, files)]
