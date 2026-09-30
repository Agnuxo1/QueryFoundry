# REJECTED EXPERIMENT: changes malformed-input SQL error behavior. Not a production mode.
"""Narrow, reviewable rewrites of the exact six organizer recipes.

Only JSON text extraction changes. Casts, filters, joins, ordering and output
columns stay intact. OFFSET 0 is a per-row planner barrier, not a disk stage.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ORDER = ('table3', 'table4', 'table1', 'table5', 'table6', 'table2')
MODES = ('baseline', 'payload_once', 'fields_once', 'sparse_positions')
EXTRACTION = re.compile(r"source_row\.raw\s*->\s*'values'\s*->>\s*'([a-z_]+[0-9]+)'")
ANCHOR = 'FROM {{source}} AS source_row'

def rewrite(sql: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError('Unknown experiment mode')
    if mode == 'baseline':
        return sql
    if mode == 'sparse_positions':
        marker='AS position_data(position_number, declared_number_text, type_text, weight_text, gap_text)'
        if marker not in sql:
            return rewrite(sql,'payload_once')
        if sql.count(ANCHOR)!=1 or sql.count(marker)!=1:
            raise ValueError('Unsupported position recipe structure')
        matrix=re.search(r'CROSS JOIN LATERAL \(\s*VALUES\s*(.*?)\s*\) '+re.escape(marker),sql,re.S)
        if not matrix: raise ValueError('Unsupported position matrix')
        rows=re.findall(r'\((\d+)::smallint,\s*(.*?)\)(?=,\s*\(|\s*$)',matrix[1],re.S)
        if [int(row[0]) for row in rows]!=list(range(1,15)):
            raise ValueError('Unsupported official position range')
        descriptors=[]
        for position,text in rows:
            keys=EXTRACTION.findall(text)
            number=int(position)
            expected=[f'integer_column_{number+1}',f'text_column_{number+3}',f'numeric_column_{2*number+1}',f'numeric_column_{2*number+2}']
            if keys!=expected: raise ValueError('Unsupported official position keys')
            descriptors.append('('+position+'::smallint, '+', '.join("'"+key+"'" for key in keys)+')')
        replacement="""CROSS JOIN LATERAL (
    SELECT position_keys.position_number,
        qf_payload.payload ->> position_keys.declared_key AS declared_number_text,
        qf_payload.payload ->> position_keys.type_key AS type_text,
        qf_payload.payload ->> position_keys.weight_key AS weight_text,
        qf_payload.payload ->> position_keys.gap_key AS gap_text
    FROM (VALUES
        """+',\n        '.join(descriptors)+"""
    ) AS position_keys(position_number, declared_key, type_key, weight_key, gap_key)
    WHERE NULLIF(BTRIM(qf_payload.payload ->> position_keys.declared_key), '')::smallint
        = position_keys.position_number
    OFFSET 0
) """+marker
        result=sql[:matrix.start()]+replacement+sql[matrix.end():]
        if EXTRACTION.search(result): raise ValueError('Additional position recipe extractions are unsupported')
        return result.replace(ANCHOR,ANCHOR+"\nCROSS JOIN LATERAL (SELECT source_row.raw -> 'values' AS payload OFFSET 0) AS qf_payload")
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
