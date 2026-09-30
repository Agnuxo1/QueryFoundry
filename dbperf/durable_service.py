"""Recover committed expansions and version finalization across lost SSH replies.

Data effects and their receipt share a data-database transaction. The six
version records and a second receipt share the control-database transaction.
No cross-database atomicity is claimed: the durable data receipt lets a retry
finish missing versions without rerunning the expansion.
"""
import hashlib
import json
import os
import shlex
import uuid
from pathlib import Path

from config import CONTROL_DB
from utils import sql_literal, sql_ident
from .service import QueryFoundryService
from .receipts import INSTALL_SQL

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

class DurableQueryFoundryService(QueryFoundryService):
    @staticmethod
    def persist_job_record(jobs,job,metadata):
        jobs.mkdir(parents=True,exist_ok=True)
        record=jobs/(job+'.json')
        if not record.exists():
            # Two clients may register the same UUID before either rename.
            temporary=jobs/(job+'.'+uuid.uuid4().hex+'.tmp')
            temporary.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
            os.replace(temporary,record)

    def _command(self,database,sql):
        command=f'psql -h localhost -U {shlex.quote(self.sql_username)} -d {shlex.quote(database)} -X -qAt -v ON_ERROR_STOP=1 -f -'
        return super().run_remote_command(command,stdin_text='SET client_min_messages=warning;\n'+sql)

    def _read_receipt(self,database,job,signature):
        output=self._command(database,"SELECT jsonb_build_object('signature',request_sha256,'payload',result)::text FROM qf_recovery.receipts WHERE job_id="+sql_literal(job)+'::uuid;')
        if not output.strip(): return None
        receipt=json.loads(output.splitlines()[-1])
        if receipt['signature']!=signature:
            raise RuntimeError('Recovery job ID was reused with different source, recipes or version metadata')
        return receipt['payload']

    def _read_receipt_after_error(self,database,job,signature,original_error):
        try:
            return self._read_receipt(database,job,signature)
        except Exception as recovery_error:
            # A second failed connection must not replace the triggering failure.
            # Keep the probe failure as an explicit cause for diagnosis.
            raise original_error from recovery_error

    def _guard(self,job):
        return "SELECT pg_advisory_xact_lock(hashtextextended("+sql_literal(job)+",0));\nDO $qf_guard$ BEGIN IF EXISTS (SELECT 1 FROM qf_recovery.receipts WHERE job_id="+sql_literal(job)+"::uuid) THEN RAISE EXCEPTION 'QueryFoundry job already committed'; END IF; END $qf_guard$;\n"

    @staticmethod
    def _snapshot_query(destinations):
        pairs=[]
        for destination in destinations:
            relation=sql_ident(destination['schema_name'])+'.'+sql_ident(destination['pure_table_name'])
            columns="(SELECT jsonb_agg(jsonb_build_object('name',attname,'type',format_type(atttypid,atttypmod),'not_null',attnotnull,'collation',attcollation::regcollation::text) ORDER BY attnum) FROM pg_attribute WHERE attrelid="+sql_literal(relation)+"::regclass AND attnum>0 AND NOT attisdropped)"
            checksum="(SELECT jsonb_build_object('rows',count(*),'h0',COALESCE(sum(hash_record_extended(t,0)::numeric),0)::text,'h1',COALESCE(sum(hash_record_extended(t,1)::numeric),0)::text,'columns',"+columns+") FROM "+relation+' t)'
            pairs.extend([sql_literal(destination['table_name']),checksum])
        locale="(SELECT jsonb_build_object('collate',datcollate,'ctype',datctype,'provider',datlocprovider,'version',datcollversion) FROM pg_database WHERE datname=current_database())"
        return "SELECT jsonb_build_object('algorithm','record_hash_sums_v1','server_version',current_setting('server_version_num'),'encoding',current_setting('server_encoding'),'locale',"+locale+",'destinations',jsonb_build_object("+','.join(pairs)+'))'

    def _journal(self,context,result_expression):
        job=sql_literal(context['job']); signature=sql_literal(context['signature'])
        return """INSERT INTO pg_temp.pgdm_expansion_timings VALUES
            (8500, 'recovery', 'Commit durable recovery receipt',clock_timestamp(),NULL,NULL,NULL,NULL);
            INSERT INTO qf_recovery.receipts(job_id,request_sha256,result)
            VALUES ("""+job+'::uuid,'+signature+','+result_expression+""" );
            UPDATE pg_temp.pgdm_expansion_timings SET finished_at=clock_timestamp()
            WHERE sequence_number=8500;
            """

    def run_remote_command(self,command,**kwargs):
        context=getattr(self,'_qf_context',None)
        text=kwargs.get('stdin_text')
        if context and text and 'pgdm_expansion_timings' in text:
            if context['kind']=='data' and 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in text:
                manifest=context['manifest_query']
                metadata=sql_literal(json.dumps(context['metadata'],sort_keys=True,ensure_ascii=False))+'::jsonb'
                timings="""(SELECT COALESCE(jsonb_agg(jsonb_build_object(
                    'stage',stage_key,'name',item_name,'seconds',EXTRACT(EPOCH FROM finished_at-started_at),
                    'rows',rows_affected,'destination',destination_name) ORDER BY sequence_number), '[]'::jsonb)
                    FROM pg_temp.pgdm_expansion_timings WHERE finished_at IS NOT NULL)"""
                snapshot=self._snapshot_query(context['metadata']['destinations'])
                payload="jsonb_build_object('result',"+metadata+" || jsonb_build_object('dependency_manifest',("+manifest+")::jsonb),'timings',"+timings+",'destination_snapshot',("+snapshot+"))"
                lock_key='hashtextextended('+sql_literal(context['job'])+',0)'
                # Acquire outside REPEATABLE READ so a waiter takes a fresh snapshot.
                session_lock="INSERT INTO pg_temp.pgdm_expansion_timings VALUES (0,'recovery','Acquire job session lock before snapshot',clock_timestamp(),NULL,NULL,NULL,NULL);\n"+'DO $qf_lock$ BEGIN PERFORM pg_advisory_lock('+lock_key+'); END $qf_lock$;\n'+"UPDATE pg_temp.pgdm_expansion_timings SET finished_at=clock_timestamp() WHERE sequence_number=0;\n"
                text=text.replace('BEGIN ISOLATION LEVEL REPEATABLE READ;',session_lock+'BEGIN ISOLATION LEVEL REPEATABLE READ;\n'+self._guard(context['job']),1)
                unlock="\nINSERT INTO pg_temp.pgdm_expansion_timings VALUES (9100,'recovery','Release job session lock',clock_timestamp(),NULL,NULL,NULL,NULL);\nDO $qf_unlock$ BEGIN PERFORM pg_advisory_unlock("+lock_key+"); END $qf_unlock$;\nUPDATE pg_temp.pgdm_expansion_timings SET finished_at=clock_timestamp() WHERE sequence_number=9100;"
                text=text.replace('COMMIT;','COMMIT;'+unlock,1)
                # Verify the source footprint again in the expansion snapshot.
                expected=sql_literal(json.dumps(context['source_footprint'],sort_keys=True))+'::jsonb'
                footprint="jsonb_build_object('raw_hash_manifest',m->'raw_hash_manifest','source_columns',m->'source_columns','expected_row_count',m->'expected_row_count')"
                check="\nDO $qf_source$ DECLARE m jsonb:= ("+manifest+")::jsonb; BEGIN IF "+footprint+' <> '+expected+" THEN RAISE EXCEPTION 'Source manifest changed before expansion'; END IF; END $qf_source$;\n"
                boundary="clock_timestamp() WHERE sequence_number = 3;"
                if text.count(boundary)!=1: raise RuntimeError('Unsupported upstream manifest boundary')
                text=text.replace(boundary,boundary+check,1)
                marker="INSERT INTO pg_temp.pgdm_expansion_timings VALUES (9000, 'execute'"
                if text.count(marker)!=1: raise RuntimeError('Unsupported upstream commit boundary')
                text=text.replace(marker,self._journal(context,payload)+marker,1)
            elif context['kind']=='versions' and 'Commit atomic version batch' in text:
                prefix="SELECT json_build_object(\n    'table_name'"
                suffix=")::text\nFROM inserted_version;"
                if text.count(prefix)!=6 or text.count(suffix)!=6:
                    raise RuntimeError('Unsupported upstream version registration SQL')
                text=text.replace(prefix,"INSERT INTO pg_temp.qf_registered_versions(result)\nSELECT json_build_object(\n    'table_name'")
                text=text.replace(suffix,")::jsonb\nFROM inserted_version RETURNING result::text;")
                text=text.replace('BEGIN;','CREATE TEMP TABLE qf_registered_versions(ordinal bigint GENERATED ALWAYS AS IDENTITY,result jsonb) ON COMMIT PRESERVE ROWS;\nBEGIN;\n'+self._guard(context['job']),1)
                marker="INSERT INTO pg_temp.pgdm_expansion_timings VALUES (\n    9000,"
                if text.count(marker)!=1: raise RuntimeError('Unsupported upstream version commit boundary')
                payload="jsonb_build_object('versions',(SELECT jsonb_agg(result ORDER BY ordinal) FROM pg_temp.qf_registered_versions))"
                text=text.replace(marker,self._journal(context,payload)+marker,1)
            kwargs['stdin_text']=text
        return super().run_remote_command(command,**kwargs)

    def _recovered_result(self,payload,report,job,signature):
        result=dict(payload['result'])
        with self.expansion_timing_scope(report,'recovery','Verify committed destination counts and content fingerprints before replay'):
            expected=payload.get('destination_snapshot')
            if expected is None:
                raise RuntimeError('Older recovery receipt lacks destination counts; explicit validation is required')
            if expected.get('algorithm')!='record_hash_sums_v1':
                raise RuntimeError('Older count-only receipt lacks content fingerprints; explicit validation is required')
            current=json.loads(self._command(result['source_database_name'],self._snapshot_query(result['destinations'])).splitlines()[-1])
            if current!=expected:
                raise RuntimeError('Committed destination fingerprint or PostgreSQL version changed; recovery refused, investigate restore or external modifications')
        result.update(qf_job_id=job,qf_request_sha256=signature,timing_report=report)
        for item in payload.get('timings',[]):
            self._append_expansion_timing(report,item['stage'],item['name']+' (previous committed attempt)',
                float(item.get('seconds') or 0),'previous_attempt_diagnostic',rows=item.get('rows'),
                destination=item.get('destination'),include_in_total=False)
        inserted={}
        for item in payload.get('timings',[]):
            if item['stage']=='execute' and item['name'].startswith('Insert into ') and item.get('destination'):
                inserted[item['destination']]=inserted.get(item['destination'],0)+int(item.get('rows') or 0)
        report['destination_rows']=[{'table_name':name,'rows':rows} for name,rows in inserted.items()]
        report['total_rows_inserted']=sum(inserted.values())
        self._append_expansion_timing(report,'recovery','Reused rows from previous commit; no new inserts this attempt',
            0,'diagnostic',rows=report['total_rows_inserted'],include_in_total=False)
        report['resumed_committed_job']=job
        report['source_rows']=int(result['dependency_manifest'].get('expected_row_count') or 0)
        report['recovery_metric_notice']='Previous attempt timings are diagnostic; this report counts current recovery work only.'
        return result

    def execute_cross_table_expansion(self,database_name,source_full_table_name,raw_schemas,destinations,
        cancel_event=None,progress_callback=None,post_commit_callback=None,timing_report=None):
        report=timing_report or self.create_cross_table_expansion_timing_report(database_name,source_full_table_name,raw_schemas,[d['table_name'] for d in destinations])
        job=str(uuid.UUID(os.environ['QF_JOB_ID'])) if os.environ.get('QF_JOB_ID') else str(uuid.uuid4())
        with self.expansion_timing_scope(report,'recovery','Bind durable job to source versions, manifest and recipes'):
            prepared=self._prepare_cross_table_expansion(source_full_table_name,raw_schemas,destinations)
            versions=self.get_raw_schema_version_references(database_name,source_full_table_name,prepared['raw_schemas'],cancel_event=cancel_event)
            manifest=self._capture_expansion_source_manifest(database_name,prepared['source_schema_name'],prepared['source_table_name'],prepared['raw_schemas'],cancel_event=cancel_event)
            footprint={k:manifest[k] for k in ('raw_hash_manifest','source_columns','expected_row_count')}
            metadata={'source_database_name':database_name,'source_schema_name':prepared['source_schema_name'],
                'source_table_name':prepared['source_table_name'],'raw_schemas':prepared['raw_schemas'],
                'source_versions':versions,'destinations':prepared['destinations']}
            signature=digest({'metadata':metadata,'source_footprint':footprint})
            if not os.environ.get('QF_JOB_ID'):
                pending=getattr(self,'_qf_pending_job',None)
                if pending and pending['signature']==signature:
                    job=pending['job']
                else:
                    self._qf_pending_job={'signature':signature,'job':job}
            # Keep the identifier needed after a GUI/process restart, never credentials.
            jobs=Path(__file__).resolve().parents[1]/'.runtime/jobs'
            self.persist_job_record(jobs,job,{'job_id':job,'request_sha256':signature,
                    'database':database_name,'source':source_full_table_name,
                    'raw_schemas':raw_schemas,'recipe_mode':os.environ.get('QF_RECIPE_MODE','baseline')})
            self._command(database_name,INSTALL_SQL)
            saved=self._read_receipt(database_name,job,signature)
        if saved:
            if post_commit_callback: post_commit_callback()
            return self._recovered_result(saved,report,job,signature)
        # Original preflight and DML guards still run on every new expansion.
        query='SELECT json_build_object('+self._build_expansion_manifest_sql(prepared['source_schema_name'],prepared['source_table_name'],prepared['raw_schemas']).split('SELECT json_build_object(',1)[1]
        query=query.strip().rstrip(';')
        self._qf_context={'kind':'data','job':job,'signature':signature,'metadata':metadata,
            'source_footprint':footprint,'manifest_query':query}
        try:
            try:
                result=super().execute_cross_table_expansion(database_name,source_full_table_name,raw_schemas,destinations,
                    cancel_event,progress_callback,post_commit_callback,report)
            except Exception as original_error:
                saved=self._read_receipt_after_error(database_name,job,signature,original_error)
                if saved:
                    if post_commit_callback: post_commit_callback()
                    return self._recovered_result(saved,report,job,signature)
                raise
            result.update(qf_job_id=job,qf_request_sha256=signature)
            return result
        finally:
            self._qf_context=None

    def register_cross_table_expansion_versions(self,expansion_result,requested_by,workstation_name,
        progress_callback=None,cancel_event=None,timing_report=None):
        job=expansion_result.get('qf_job_id')
        if not job:
            return super().register_cross_table_expansion_versions(expansion_result,requested_by,workstation_name,progress_callback,cancel_event,timing_report)
        report=timing_report or expansion_result.get('timing_report')
        signature=digest({'data_request':expansion_result['qf_request_sha256'],'requested_by':requested_by,'workstation':workstation_name})
        with self.expansion_timing_scope(report,'recovery','Check atomic version-finalization receipt'):
            self._command(CONTROL_DB,INSTALL_SQL)
            saved=self._read_receipt(CONTROL_DB,job,signature)
        if saved: return saved['versions']
        self._qf_context={'kind':'versions','job':job,'signature':signature}
        try:
            try:
                return super().register_cross_table_expansion_versions(expansion_result,requested_by,workstation_name,progress_callback,cancel_event,report)
            except Exception as original_error:
                saved=self._read_receipt_after_error(CONTROL_DB,job,signature,original_error)
                if saved: return saved['versions']
                raise
        finally:
            self._qf_context=None
