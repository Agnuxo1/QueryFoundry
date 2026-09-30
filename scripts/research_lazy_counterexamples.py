"""Repeat the eight sparse-rewrite counterexamples for lazy_keys at current scale.

Read-only 64-row CTE mutations; the populated table1 retains full lab scale so
planning differs from the earlier 100k fixture. No official inputs are edited.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from benchmark_gui import sql
from test_sparse_semantics import outcome


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
    evidence={'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Read-only 64-row CTE probes; table1 at full lab scale; not benchmark times',
        'source_rows':int(sql('SELECT count(*) FROM public.raw_data;')),'cases':{}}
    for name,mutation in cases.items():
        original=outcome('baseline',mutation); candidate=outcome('lazy_keys',mutation)
        evidence['cases'][name]={'original':original,'candidate':candidate,'same':original==candidate}
    evidence['passed']=all(c['same'] for c in evidence['cases'].values())
    output=ROOT/'reports'/os.environ.get('QF_REPORT_SUBDIR','')/'lazy-counterexamples.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(evidence,indent=2),encoding='utf8')
    print('Lazy counterexamples:',len(cases),'cases; passed',evidence['passed'],flush=True)
    if not evidence['passed']: raise RuntimeError('Known counterexample gate failed; keep candidate unpromoted')


if __name__=='__main__': main()
