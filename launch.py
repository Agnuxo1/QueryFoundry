"""Start the original Windows GUI with measured recipes and optional recovery."""
import argparse
import os
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'app'))
from dbperf.service import QueryFoundryService
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.recipes import MODES
import services.postgres_service as postgres_service
parser=argparse.ArgumentParser()
parser.add_argument('--baseline',action='store_true')
parser.add_argument('--recovery',action='store_true')
parser.add_argument('--recipe-mode',choices=MODES,default='payload_once',help='Opt-in recipe experiment; default remains payload_once')
parser.add_argument('--resume-job',help='UUID from .runtime/jobs; select the same source and recipes in the GUI')
args=parser.parse_args()
os.environ['QF_RECIPE_MODE']='baseline' if args.baseline else args.recipe_mode
if args.resume_job:
    import uuid
    os.environ['QF_JOB_ID']=str(uuid.UUID(args.resume_job))
    args.recovery=True
postgres_service.PostgresAdminService = DurableQueryFoundryService if args.recovery else QueryFoundryService
sys.argv=[str(root/'app/main.py')]
runpy.run_path(str(root / 'app/main.py'), run_name='__main__')
