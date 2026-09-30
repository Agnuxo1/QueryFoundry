"""R-003: short clone-only source race probe, never a benchmark workload.

Use 64 official rows with copied schemas/control metadata. SSH commands retain
the original application's database identities but route to unique clones.
No original database rename, truncate, update or drop is permitted.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'app'), str(ROOT/'scripts')]
from benchmark_gui import connect, sql, docker, CONTAINER, PROJECT
from config import CONTROL_DB
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.recipes import destinations, ORDER


def memory_floor(gib=1.5):
    if psutil.virtual_memory().available < gib*2**30:
        raise RuntimeError('Memory floor reached; stop the bounded probe')


def source_digest(database):
    return json.loads(sql("SELECT json_build_object('rows',count(*),'h0',"
        "COALESCE(sum(hash_record_extended(t,0)::numeric),0)::text,'h1',"
        "COALESCE(sum(hash_record_extended(t,1)::numeric),0)::text) FROM public.raw_data t;", database))


def main():
    memory_floor(2)
    label = docker('inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',
        CONTAINER,text=True).stdout.strip()
    if label != PROJECT or PROJECT != 'queryfoundry':
        raise RuntimeError('This probe requires the explicitly labelled 100k lab')
    tag = uuid.uuid4().hex[:12]
    clone_data, clone_control = 'qf_source_data_'+tag, 'qf_source_control_'+tag
    routes = {'kaggle_challenge':clone_data, CONTROL_DB:clone_control}
    evidence = {'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'64-row clone-only functional probe; Windows SSH / Linux PostgreSQL',
        'benchmark':False,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'clones':routes,'cases':{},'source_generator_modified':False}
    output = ROOT/'reports/source-mutation.json'
    created = []
    prior_mode, prior_job = os.environ.get('QF_RECIPE_MODE'), os.environ.get('QF_JOB_ID')
    original_digest = source_digest('kaggle_challenge')

    class ProbeService(DurableQueryFoundryService):
        mutation = None
        mutation_injected = False
        def _command(self,database,statement):
            # Durable _command deliberately bypasses run_remote_command, so its
            # connection target must also be redirected explicitly.
            if database not in routes:
                raise RuntimeError('Unexpected database in durable clone probe')
            return super()._command(routes[database],statement)
        def run_remote_command(self,command,**kwargs):
            memory_floor()
            text = kwargs.get('stdin_text') or ''
            context = getattr(self,'_qf_context',None)
            if self.mutation and context and context['kind']=='data' and 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in text:
                # A separate SQL connection commits after the captured footprint,
                # immediately before the expansion's snapshot is acquired.
                sql(self.mutation, clone_data)
                self.mutation = None
                self.mutation_injected = True
            for original, clone in routes.items():
                command = re.sub(r'(?<=-d )'+re.escape(original)+r'(?=\s|$)',clone,command)
            if re.search(r'-d\s+[\"\']?(?:kaggle_challenge|'+re.escape(CONTROL_DB)+r')[\"\']?(?=\s|$)',command):
                raise RuntimeError('Refusing an SSH command still targeting an original database')
            return super().run_remote_command(command,**kwargs)

    try:
        # Schema-only data clone; full small control metadata clone. Archives stay
        # ignored on D:, and are never part of the public research report.
        directory = ROOT/'.runtime/source-mutation'/tag
        directory.mkdir(parents=True)
        for original,clone in routes.items():
            archive = directory/(clone+'.dump')
            args = ['docker','exec','-u','postgres',CONTAINER,'pg_dump','-Fc','-d',original]
            if original=='kaggle_challenge': args += ['--schema-only','--schema=public']
            with archive.open('wb') as stream: subprocess.run(args,stdout=stream,check=True)
            docker('exec','-u','postgres',CONTAINER,'createdb','-O','challenge',clone)
            created.append(clone)
            sql('DROP SCHEMA public;',clone)
            with archive.open('rb') as stream:
                subprocess.run(['docker','exec','-i','-u','postgres',CONTAINER,'pg_restore',
                    '--exit-on-error','-d',clone],stdin=stream,check=True)
        data = docker('exec','-u','postgres',CONTAINER,'psql','-X','-qAt','-v','ON_ERROR_STOP=1',
            '-d','kaggle_challenge','-c','COPY (SELECT * FROM public.raw_data ORDER BY raw_id LIMIT 64) TO STDOUT WITH (FORMAT csv);').stdout
        docker('exec','-i','-u','postgres',CONTAINER,'psql','-X','-qAt','-v','ON_ERROR_STOP=1',
            '-d',clone_data,'-c','COPY public.raw_data FROM STDIN WITH (FORMAT csv);',input=data)
        assert int(sql('SELECT count(*) FROM public.raw_data;',clone_data))==64
        sql('CREATE TABLE public.qf_original_raw AS SELECT * FROM public.raw_data;',clone_data)
        os.environ['QF_RECIPE_MODE']='payload_once'
        mutations = {'control':None,
            'stored_hash_changed':"UPDATE public.raw_data SET raw_hash='qf_test_changed_hash' WHERE raw_id=1;",
            'json_changed_stored_hash_preserved':"UPDATE public.raw_data SET raw=jsonb_set(raw,'{values,numeric_column_1}',to_jsonb('42.123'::text)) WHERE raw_id=1;"}
        for name,mutation in mutations.items():
            memory_floor()
            sql('TRUNCATE '+','.join('public.'+t for t in ORDER)+' RESTART IDENTITY; '
                'UPDATE public.raw_data t SET raw=o.raw,raw_hash=o.raw_hash FROM public.qf_original_raw o WHERE t.raw_id=o.raw_id; '
                "DO $qf_reset$ BEGIN IF to_regclass('public.pgdm_table_row_counts') IS NOT NULL THEN "
                "UPDATE public.pgdm_table_row_counts SET row_count=0 WHERE table_name IN ("+
                ','.join("'"+t+"'" for t in ORDER)+'); END IF; END $qf_reset$;',clone_data)
            job = str(uuid.uuid4()); os.environ['QF_JOB_ID']=job
            value=ProbeService(); connect(value); value.mutation=mutation
            try:
                before=source_digest(clone_data)
                try:
                    result=value.execute_cross_table_expansion('kaggle_challenge','public.raw_data',
                        ['group001'],destinations('baseline'))
                    accepted=True; error=None
                except RuntimeError as exc:
                    accepted=False
                    # Record only the specific known guard, no raw remote errors.
                    error='source_manifest_changed' if 'Source manifest changed before expansion' in str(exc) else 'other_runtime_error'
                rows={t:int(sql('SELECT count(*) FROM public.'+t,clone_data)) for t in ORDER}
                receipts=int(sql("SELECT count(*) FROM qf_recovery.receipts WHERE job_id='"+job+"';",clone_data))
                after=source_digest(clone_data)
                case={'accepted':accepted,'guard':error,'mutation_injected_after_capture':value.mutation_injected,
                    'source_content_digest_changed':before!=after,'destination_rows':rows,'receipts':receipts}
                if name=='json_changed_stored_hash_preserved' and accepted:
                    case['altered_value_reached_output']=sql("SELECT count(*) FROM public.table1 WHERE id_column_3=1 AND integer_column_7=42123;",clone_data)=='1'
                evidence['cases'][name]=case
                output.write_text(json.dumps(evidence,indent=2),encoding='utf8')
                print(name, 'accepted' if accepted else error,flush=True)
                if name=='control': assert accepted and rows['table1']==64 and receipts==1
                if name=='stored_hash_changed': assert not accepted and error=='source_manifest_changed' and not any(rows.values()) and receipts==0
                if name=='json_changed_stored_hash_preserved': assert accepted and case['altered_value_reached_output'] and before!=after
            finally: value.close()
        evidence['expected_observations_verified']=True
        evidence['limitation_confirmed']='Stored raw_hash manifest detects metadata changes, not arbitrary JSON edits that preserve stored hashes. This is a functional probe, not an allowed benchmark dataset or an integrity improvement.'
    finally:
        for clone in reversed(created): docker('exec','-u','postgres',CONTAINER,'dropdb',clone)
        evidence['clones_removed']=True
        evidence['original_source_unchanged']=source_digest('kaggle_challenge')==original_digest
        output.write_text(json.dumps(evidence,indent=2),encoding='utf8')
        for key,previous in [('QF_RECIPE_MODE',prior_mode),('QF_JOB_ID',prior_job)]:
            if previous is None: os.environ.pop(key,None)
            else: os.environ[key]=previous
    assert evidence['original_source_unchanged']
    print('Bounded probe complete; original source unchanged and clones removed.',flush=True)


if __name__=='__main__': main()
