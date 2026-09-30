"""Reproduce Claude's differential fuzz on the labelled Linux lab, using TEMP rows.

The delivery remains unmodified. Test production lazy_keys, retain the rejected
sparse rewrite as a positive control, and save only synthetic probe evidence.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import docker, CONTAINER, PROJECT
from dbperf.recipes import rewrite


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--trials',type=int,default=400)
    parser.add_argument('--payload-trials',type=int,default=150)
    args=parser.parse_args()
    if not 1<=args.trials<=400 or not 1<=args.payload_trials<=150:
        parser.error('Bounded trial limits are 400/150')
    label=docker('inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',CONTAINER,text=True).stdout.strip()
    if label!=PROJECT: raise RuntimeError('Not a labelled laboratory container')
    path=ROOT/'coordinacion/entregas/i005/fuzz_semantics.py'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('claude_fuzz_delivery',path)
    fuzz=importlib.util.module_from_spec(spec); spec.loader.exec_module(fuzz)
    fuzz.PSQL=['docker','exec','-i','-u','postgres',CONTAINER,'psql','-X','-qAt',
        '-v','ON_ERROR_STOP=1','-d','kaggle_challenge','-f','-']
    # Test the integrated code, not merely the independent delivery function.
    fuzz.VARIANTS['lazy_keys']=rewrite(fuzz.RECIPE,'lazy_keys')
    evidence={'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Linux PG17 differential SELECT fuzz; session TEMP mutations only; not GUI timings',
        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'suite':[],'seeds_independent_from_claude_windows_run':True}
    output=ROOT/'reports/lazy-keys-linux-semantics.json'
    saved_heavy=os.environ.get('FUZZ_HEAVY')
    try:
        for mode,trials,seed,heavy,expected in [
            ('sparse_control',1,11,False,1),
            ('lazy_keys',args.trials,31,False,0),
            ('lazy_keys',args.trials,41,True,0),
            ('payload_once',args.payload_trials,51,False,0)]:
            if heavy: os.environ['FUZZ_HEAVY']='1'
            else: os.environ.pop('FUZZ_HEAVY',None)
            capture=io.StringIO()
            print('Starting',mode,'trials',trials,'seed',seed,'heavy',heavy,flush=True)
            with redirect_stdout(capture): result=fuzz.main(mode,trials,seed)
            record={'mode':mode,'trials':trials,'seed':seed,'heavy':heavy,
                'return_code':result,'expected_return_code':expected,
                'summary':capture.getvalue().strip(),'gate_passed':result==expected}
            evidence['suite'].append(record)
            evidence['completed']=len(evidence['suite'])==4
            evidence['passed']=evidence['completed'] and all(x['gate_passed'] for x in evidence['suite'])
            evidence['matching_trials']=sum(x['trials'] for x in evidence['suite'] if x['expected_return_code']==0)
            output.write_text(json.dumps(evidence,indent=2),encoding='utf8')
            print('Finished',mode,'seed',seed,'gate',record['gate_passed'],flush=True)
            if result!=expected: raise RuntimeError('Differential semantics gate failed; do not benchmark or promote')
    finally:
        if saved_heavy is None: os.environ.pop('FUZZ_HEAVY',None)
        else: os.environ['FUZZ_HEAVY']=saved_heavy


if __name__=='__main__': main()
