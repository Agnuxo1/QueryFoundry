"""I-005 candidate `lazy_keys` for the organizer's table2 recipe (Claude, 2026-09-30).

Idea: keep the 14-position matrix but make it hold only the *key names*; each
JSON value is extracted where it is used (WHERE / SELECT). PostgreSQL then only
evaluates the type extraction for every position and the weight/declaration
extractions for positions whose earlier quals pass, instead of 70 eager ->>
lookups per source row. Casts, quals, join, and outputs are untouched, so the
original qual order (type -> weight -> declaration) and its errors are kept.
"""
import re

MARKER = 'AS position_data(position_number, declared_number_text, type_text, weight_text, gap_text)'
ANCHOR = 'FROM {{source}} AS source_row'
EXTRACT = re.compile(r"source_row\.raw\s*->\s*'values'\s*->>\s*'([a-z_]+[0-9]+)'")
FIELDS = {'declared_number_text': 'declared_key', 'type_text': 'type_key',
          'weight_text': 'weight_key', 'gap_text': 'gap_key'}

def lazy_keys(sql: str) -> str:
    if sql.count(ANCHOR) != 1 or sql.count(MARKER) != 1:
        raise ValueError('Unsupported position recipe structure')
    m = re.search(r'CROSS JOIN LATERAL \(\s*VALUES\s*(.*?)\s*\) ' + re.escape(MARKER), sql, re.S)
    if not m:
        raise ValueError('Unsupported position matrix')
    rows = re.findall(r'\((\d+)::smallint,\s*(.*?)\)(?=,\s*\(|\s*$)', m[1], re.S)
    if [int(r[0]) for r in rows] != list(range(1, 15)):
        raise ValueError('Unsupported position range')
    tuples = []
    for position, text in rows:
        keys = EXTRACT.findall(text)
        n = int(position)
        if keys != [f'integer_column_{n+1}', f'text_column_{n+3}', f'numeric_column_{2*n+1}', f'numeric_column_{2*n+2}']:
            raise ValueError('Unsupported position keys')
        tuples.append('(' + position + '::smallint, ' + ', '.join("'" + k + "'" for k in keys) + ')')
    replacement = ("CROSS JOIN LATERAL (\n    VALUES\n        " + ',\n        '.join(tuples) +
                   "\n) AS position_keys(position_number, declared_key, type_key, weight_key, gap_key)")
    result = sql[:m.start()] + replacement + sql[m.end():]
    for column, key in FIELDS.items():
        result = result.replace('position_data.' + column, '(qf_payload.payload ->> position_keys.' + key + ')')
    result = result.replace('position_data.position_number', 'position_keys.position_number')
    if 'position_data' in result:
        raise ValueError('Unexpected position_data reference')
    payload = "\nCROSS JOIN LATERAL (\n    SELECT source_row.raw -> 'values' AS payload OFFSET 0\n) AS qf_payload"
    return result.replace(ANCHOR, ANCHOR + payload)
