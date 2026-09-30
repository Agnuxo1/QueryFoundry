"""Force overlap of two job registrations without a shared database."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app')]
from dbperf.durable_service import DurableQueryFoundryService

class JobRecordTests(unittest.TestCase):
    def test_two_concurrent_registrations_preserve_valid_record(self):
        parent=ROOT/'.runtime/test-job-record'; parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            jobs=Path(directory); barrier=threading.Barrier(2); rename_lock=threading.Lock()
            replace=os.replace
            def overlap(source,target):
                barrier.wait(timeout=10)
                with rename_lock:
                    return replace(source,target)
            value={'job_id':'shared-job','request_sha256':'same request'}
            with patch('dbperf.durable_service.os.replace',side_effect=overlap):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results=[pool.submit(DurableQueryFoundryService.persist_job_record,jobs,'shared-job',value) for _ in range(2)]
                    for result in results: result.result()
            self.assertEqual(json.loads((jobs/'shared-job.json').read_text()),value)
            self.assertFalse(list(jobs.glob('*.tmp')))

if __name__=='__main__': unittest.main()
