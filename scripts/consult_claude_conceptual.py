"""Conceptual consultation with no repository context, tools, hooks or discovery."""
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PROMPT='''Analyze a generic PostgreSQL workflow, without accessing files or private project data.
Database A commits row expansion plus a durable receipt (UUID and request digest).
Database B later commits six metadata versions plus another receipt. On retry,
a client checks receipts and resumes missing metadata. A transaction-scoped
advisory lock is acquired after BEGIN REPEATABLE READ. Propose minimal experiments
for simultaneous identical requests, restart between commits, dump/restore of
both databases and externally deleted output rows. Explain snapshot/lock caveats.
Return a concise Spanish answer with hypotheses and expected invariants, not
claims of tests performed. This is a conceptual discussion, not a repository audit.'''
def main():
    directory=ROOT/'.runtime/claude-conceptual'; directory.mkdir(parents=True,exist_ok=True)
    result=subprocess.run([shutil.which('claude'), '--bare','-p','--tools','',
        '--permission-mode','dontAsk','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
        '--disable-slash-commands','--no-session-persistence','--output-format','json',PROMPT],
        cwd=directory,capture_output=True,text=True,encoding='utf-8',timeout=300)
    try: payload=json.loads(result.stdout)
    except ValueError: payload={'is_error':True,'result':'No authenticated conceptual response received.'}
    accepted=result.returncode==0 and not payload.get('is_error',False)
    report={'accepted':accepted,'exit_code':result.returncode,'scope':'generic conceptual input; bare mode; no file tools',
        'result':payload.get('result'),'duration_ms':payload.get('duration_ms')}
    (ROOT/'reports/claude-conceptual.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='result'},indent=2))

if __name__=='__main__': main()
