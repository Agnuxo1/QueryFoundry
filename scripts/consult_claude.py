"""Bounded read-only Claude consultation; no shell/edit tools or MCP servers."""
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
def main():
    prompt=(ROOT/'coordinacion/entregas/T-001-request.txt').read_text(encoding='utf-8')
    executable=shutil.which('claude')
    if not executable: raise RuntimeError('Claude executable unavailable')
    result=subprocess.run([executable,'-p','--tools','Read,Glob,Grep',
        '--permission-mode','dontAsk','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
        '--disable-slash-commands','--no-session-persistence','--output-format','json',prompt],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=600)
    try: payload=json.loads(result.stdout)
    except ValueError:
        payload={'is_error':True,'result':'Non-JSON Claude response; no audit accepted.'}
    accepted=result.returncode==0 and not payload.get('is_error',False)
    output=ROOT/'coordinacion/entregas/T-001-claude-cli.md'
    output.write_text('# Claude independent audit\n\n'+str(payload.get('result',''))+'\n',encoding='utf-8')
    receipt={'exit_code':result.returncode,'accepted':accepted,'session_id':payload.get('session_id'),
        'duration_ms':payload.get('duration_ms'),'usage':payload.get('usage'),
        'output':str(output.relative_to(ROOT))}
    (ROOT/'reports/claude-consultation.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))
    if not accepted: raise RuntimeError('Claude consultation did not produce an accepted audit')

if __name__=='__main__': main()
