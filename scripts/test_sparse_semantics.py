"""Compare SELECT multisets and cast failures on synthetic CTEs, no input edits."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import CONTAINER
from dbperf.recipes import destinations
from experiments.rejected_sparse_positions import destinations as experimental_destinations

def outcome(mode,mutation):
    recipe=(experimental_destinations(mode) if mode=='sparse_positions' else destinations(mode))[-1]['sql']
    selected=recipe[recipe.index('\nSELECT\n')+1:].strip().rstrip(';').replace('{{source}}','source_sample')
    statement="WITH sample_base AS MATERIALIZED (SELECT raw_id,raw FROM public.raw_data ORDER BY raw_id LIMIT 64), source_sample AS (SELECT raw_id,"+mutation+" AS raw FROM sample_base) SELECT to_jsonb(r)::text FROM ("+selected+") r;"
    result=subprocess.run(['docker','exec','-i','-u','postgres',CONTAINER,'psql','-X','-qAt','-v','ON_ERROR_STOP=1',
        '-v','VERBOSITY=sqlstate','-d','kaggle_challenge','-f','-'],input=statement,text=True,capture_output=True)
    if result.returncode:
        state=re.search(r'ERROR:\s+([A-Z0-9]{5})',result.stderr)
        return {'status':'error','sqlstate':state[1] if state else 'unclassified'}
    rows=sorted(result.stdout.splitlines())
    return {'status':'ok','rows':len(rows),'sha256_multiset':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}

def main():
    def set_value(base,field,value):
        return "jsonb_set("+base+",'{values,"+field+"}',to_jsonb('"+value+"'::text))"
    cases={'official_sample':'raw',
        'blank_declaration_bad_weight':set_value(set_value('raw','integer_column_2',''),'numeric_column_3','bad'),
        'valid_declaration_bad_weight':set_value(set_value('raw','integer_column_2','1'),'numeric_column_3','bad'),
        'wrong_declaration_bad_weight':set_value(set_value('raw','integer_column_2','2'),'numeric_column_3','bad'),
        'bad_declaration':set_value('raw','integer_column_2','bad'),
        'overflow_declaration':set_value('raw','integer_column_2','40000'),
        'invalid_type_bad_weight':set_value(set_value('raw','text_column_4','INVALID'),'numeric_column_3','bad'),
        'negative_weight_bad_gap':set_value(set_value('raw','numeric_column_3','-1'),'numeric_column_4','bad')}
    evidence={}
    for name,mutation in cases.items():
        original=outcome('baseline',mutation); candidate=outcome('sparse_positions',mutation)
        evidence[name]={'original':original,'candidate':candidate,'same':original==candidate}
    report={'scope':'synthetic read-only CTEs over64 source rows; no official input mutation',
        'cases':evidence,'passed':all(case['same'] for case in evidence.values())}
    (ROOT/'reports/sparse-semantics.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if not report['passed']: raise RuntimeError('Sparse rewrite changes tested SQL semantics; reject before benchmark')

if __name__=='__main__': main()
