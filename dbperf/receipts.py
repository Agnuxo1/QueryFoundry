"""Durable transaction receipts for trusted server-side jobs.

The GUI extension in durable_service.py uses this schema for separate atomic
data and control-database receipts; this primitive alone is not a coordinator.
"""
import re
import uuid

INSTALL_SQL = '''CREATE SCHEMA IF NOT EXISTS qf_recovery;
CREATE TABLE IF NOT EXISTS qf_recovery.receipts (
    job_id uuid PRIMARY KEY,
    request_sha256 text NOT NULL CHECK (length(request_sha256)=64),
    result jsonb NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);'''

def literal(text):
    return "'" + str(text).replace("'", "''") + "'"

def transaction(job_id, request_sha256, trusted_work_sql, result_expression="'{}'::jsonb"):
    """Caller supplies validated internal SQL, never an unvalidated user recipe.

The request hash must bind source content/version, schema, recipes and options.
Receipt and effects commit together; a reused ID with different inputs fails.
"""
    job_id = str(uuid.UUID(str(job_id)))
    if not re.fullmatch('[0-9a-f]{64}', request_sha256):
        raise ValueError('Request requires a lowercase SHA-256 digest')
    if '$qf_receipt$' in trusted_work_sql:
        raise ValueError('Reserved dollar delimiter in SQL')
    job, digest = literal(job_id), literal(request_sha256)
    return f'''BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended({job},0));
DO $qf_receipt$
DECLARE previous text;
BEGIN
    SELECT request_sha256 INTO previous FROM qf_recovery.receipts WHERE job_id={job}::uuid;
    IF FOUND THEN
        IF previous <> {digest} THEN
            RAISE EXCEPTION 'Job ID was reused with different inputs';
        END IF;
    ELSE
        {trusted_work_sql.strip().rstrip(';')};
        INSERT INTO qf_recovery.receipts(job_id,request_sha256,result)
        VALUES ({job}::uuid,{digest},{result_expression});
    END IF;
END $qf_receipt$;
COMMIT;
SELECT result::text FROM qf_recovery.receipts WHERE job_id={job}::uuid;
'''
