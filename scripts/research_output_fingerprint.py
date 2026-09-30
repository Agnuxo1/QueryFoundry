"""Measure diagnostic output-checksum costs; probe only a SQL TEMP copy."""
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import sql, ORDER

def query(table,method):
    relation='public.'+table
    if method=='count': return 'SELECT count(*) FROM '+relation
    if method=='record_hash_sums':
        return "SELECT json_build_object('rows',count(*),'h0',sum(hash_record_extended(t,0)::numeric)::text,'h1',sum(hash_record_extended(t,1)::numeric)::text) FROM "+relation+' t'
    return "SELECT json_build_object('rows',count(*),'md5',md5(COALESCE(string_agg(h,'' ORDER BY h),''))) FROM (SELECT md5(to_jsonb(t)::text) h FROM "+relation+' t) hashes'

def main():
    evidence={'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'SQL-only diagnostic; server Execution Time from EXPLAIN ANALYZE, no GUI score',
        'server':sql('SHOW server_version;'),'methods':{}}
    methods=('count','record_hash_sums','sorted_json_md5')
    measurements={method:[] for method in methods}
    for repeat in range(3):
        for method in methods if repeat%2==0 else tuple(reversed(methods)):
            by_table={}
            for table in ORDER:
                plan=json.loads(sql('EXPLAIN (ANALYZE,FORMAT JSON) '+query(table,method)))[0]
                by_table[table]=float(plan['Execution Time'])/1000
            measurements[method].append({'repeat':repeat,'table_seconds':by_table,'total_seconds':sum(by_table.values())})
    for method,runs in measurements.items():
        evidence['methods'][method]={'runs':runs,'median_total_server_seconds':statistics.median(r['total_seconds'] for r in runs)}
    # Keep all original destination rows intact; mutate only an unindexed temporary copy.
    record="SELECT json_build_object('rows',count(*),'h0',sum(hash_record_extended(t,0)::numeric)::text,'h1',sum(hash_record_extended(t,1)::numeric)::text) FROM qf_fingerprint_probe t;"
    statements='CREATE TEMP TABLE qf_fingerprint_probe AS SELECT * FROM public.table2 LIMIT64;\n'.replace('LIMIT64','LIMIT 64')+record+'\nUPDATE qf_fingerprint_probe SET integer_column_1=integer_column_1+1 WHERE ctid=(SELECT ctid FROM qf_fingerprint_probe LIMIT 1);\n'+record
    before,after=[json.loads(line) for line in sql(statements).splitlines()]
    evidence['same_count_mutation']={'before':before,'after':after,'counts_equal':before['rows']==after['rows'],
        'record_checksum_changed':before!=after,'original_destinations_untouched':True}
    evidence['limitations']='Record hashes are noncryptographic and version/type dependent. Sorted MD5 is a diagnostic multiset checksum, not adversarial integrity proof. Neither is integrated into GUI recovery.'
    (ROOT/'reports/output-fingerprint-costs-100k.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps({method:evidence['methods'][method]['median_total_server_seconds'] for method in methods},indent=2))
    assert evidence['same_count_mutation']['counts_equal'] and evidence['same_count_mutation']['record_checksum_changed']

if __name__=='__main__': main()
