"""Real Linux disk sampler calibration, wholly inside a disposable database.

The SQL session reports its own TEMP table/index/TOAST size; another observer
cannot see these uncommitted relations in pg_class. A sorted cursor keeps an
executor spill alive for filesystem sampling. No official database writes.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import sql, docker, CONTAINER, PROJECT
from resource_sampling import RESOURCE_COMMAND, parse_sample, summarize_samples


def sample():
    started=time.monotonic()
    raw=docker('exec',CONTAINER,'sh','-c',RESOURCE_COMMAND,text=True,timeout=10).stdout
    return parse_sample(raw,started,time.monotonic())


def main():
    label=docker('inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',CONTAINER,text=True).stdout.strip()
    if CONTAINER!='queryfoundry-database-1' or label!=PROJECT:
        raise RuntimeError('Unsupported laboratory')
    identifier=uuid.uuid4().hex
    database='qf_disk_probe_'+identifier
    tablespace='qf_disk_space_'+identifier
    location='/var/lib/postgresql/'+tablespace
    output=ROOT/'reports/disk-sampler-linux-calibration.json'
    evidence={'timestamp_utc':datetime.now(timezone.utc).isoformat(),
              'scope':'synthetic instrument calibration; not a competition benchmark',
              'official_database_writes':False,'postgres_version':sql('SELECT version();','postgres'),
              'base_git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'tested_sampler_sha256':hashlib.sha256((ROOT/'scripts/resource_sampling.py').read_bytes()).hexdigest(),
              'stages':{},'passed':False,'global_intermediate_disk_peak_bytes':None,
              'instrumentation_overhead_validated':False}
    process=None; created_database=False; created_tablespace=False
    try:
        docker('exec',CONTAINER,'mkdir',location)
        docker('exec',CONTAINER,'chown','postgres:postgres',location)
        sql("CREATE TABLESPACE "+tablespace+" OWNER postgres LOCATION '"+location+"';",'postgres')
        created_tablespace=True
        sql('CREATE DATABASE '+database+' OWNER postgres;','postgres')
        created_database=True
        baseline=sample()
        assert baseline['scan_complete'] and baseline['ephemeral_apparent_bytes']==0,'Other ephemeral work is active'
        evidence['baseline']=baseline
        process=subprocess.Popen(['docker','exec','-i','-u','postgres',CONTAINER,
            'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-d',database,'-f','-'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        # The first table has externally stored TOAST; the second exercises the
        # tablespace symlink, including its independently reported index size.
        statement=f"""SET statement_timeout='45s'; SET temp_buffers='1MB';
BEGIN;
CREATE TEMP TABLE qf_disk_temp (id int, payload text);
ALTER TABLE qf_disk_temp ALTER COLUMN payload SET STORAGE EXTERNAL;
INSERT INTO qf_disk_temp SELECT x,repeat(md5(x::text),256) FROM generate_series(1,1000) x;
CREATE INDEX ON qf_disk_temp(id);
CREATE TEMP TABLE qf_disk_space (id int) TABLESPACE {tablespace};
INSERT INTO qf_disk_space SELECT generate_series(1,100000);
CREATE INDEX ON qf_disk_space(id) TABLESPACE {tablespace};
SELECT json_build_object('stage','sql_temp','expected_apparent_bytes',
 pg_total_relation_size('pg_temp.qf_disk_temp')+pg_total_relation_size('pg_temp.qf_disk_space'),
 'toast_bytes',(SELECT pg_total_relation_size(reltoastrelid) FROM pg_class WHERE oid='pg_temp.qf_disk_temp'::regclass),
 'tablespace_bytes',pg_total_relation_size('pg_temp.qf_disk_space'));
SELECT pg_sleep(8);
ROLLBACK;
BEGIN; SET LOCAL work_mem='64kB';
DECLARE qf_disk_cursor NO SCROLL CURSOR FOR
 SELECT x FROM generate_series(1,150000) x ORDER BY md5(x::text);
FETCH 1 FROM qf_disk_cursor;
SELECT json_build_object('stage','executor_spill','backend_pid',pg_backend_pid());
SELECT pg_sleep(8);
CLOSE qf_disk_cursor; COMMIT;
SELECT pg_stat_force_next_flush();
"""
        process.stdin.write(statement); process.stdin.close()
        lines=queue.Queue()
        def read_lines():
            for line in process.stdout: lines.put(line.strip())
            lines.put(None)
        reader=threading.Thread(target=read_lines,daemon=True); reader.start()
        deadline=time.monotonic()+65
        while time.monotonic()<deadline:
            try: line=lines.get(timeout=1)
            except queue.Empty: continue
            if line is None: break
            if not line.startswith('{'): continue
            metadata=json.loads(line); stage=metadata['stage']
            observed=[sample() for _ in range(3)]
            assert all(s['scan_complete'] for s in observed),'Incomplete calibration scan'
            evidence['stages'][stage]={'oracle':metadata,'samples':observed,'summary':summarize_samples(observed)}
            if stage=='sql_temp':
                assert metadata['toast_bytes']>0 and metadata['tablespace_bytes']>0
                assert all(s['categories']['sql_temp_relations']['apparent_bytes']==metadata['expected_apparent_bytes'] for s in observed),'TEMP filesystem/oracle mismatch'
                evidence['stages'][stage]['other_session_catalog_visible_count']=int(sql("SELECT count(*) FROM pg_class WHERE relname IN ('qf_disk_temp','qf_disk_space');",database))
                assert evidence['stages'][stage]['other_session_catalog_visible_count']==0
            else:
                assert all(s['categories']['executor_work_files']['apparent_bytes']>0 for s in observed),'Cursor spill was not observed'
                assert all(s['categories']['sql_temp_relations']['apparent_bytes']==0 for s in observed),'Rolled-back TEMP files remain'
            output.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
            print('Calibrated',stage,flush=True)
        process.wait(timeout=10)
        if process.returncode: raise RuntimeError('Calibration SQL failed: '+process.stderr.read()[-500:])
        assert set(evidence['stages'])=={'sql_temp','executor_spill'},'Missing calibration stages'
        after=sample(); evidence['after_session']=after
        assert after['scan_complete'] and after['ephemeral_apparent_bytes']==0,'Ephemeral files leaked'
        evidence['temporary_bytes_written']=int(sql("SELECT temp_bytes FROM pg_stat_database WHERE datname='"+database+"';",'postgres'))
        assert evidence['temporary_bytes_written']>0
        evidence['passed']=True
    finally:
        if process and process.poll() is None:
            process.kill(); process.wait(timeout=10)
        if created_database: sql('DROP DATABASE '+database+' WITH (FORCE);','postgres')
        if created_tablespace: sql('DROP TABLESPACE '+tablespace+';','postgres')
        # Only remove empty directories within this exact, unique location.
        assert location.startswith('/var/lib/postgresql/qf_disk_space_') and identifier.isalnum()
        docker('exec',CONTAINER,'find',location,'-depth','-type','d','-empty','-delete')
        evidence['disposable_resources_removed']=True
        output.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__': main()
