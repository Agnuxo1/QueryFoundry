"""Secondary receipt outages must preserve the actual initiating failure."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app')]
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.service import QueryFoundryService


class RecoveryDiagnosticsTests(unittest.TestCase):
    def test_probe_failure_keeps_original_exception_and_secondary_cause(self):
        service=DurableQueryFoundryService()
        original=RuntimeError('SSH connection interrupted during expansion')
        secondary=ConnectionError('Receipt server unavailable')
        with patch.object(service,'_read_receipt',side_effect=secondary):
            with self.assertRaises(RuntimeError) as raised:
                service._read_receipt_after_error('data','job','signature',original)
        self.assertIs(raised.exception,original)
        self.assertIs(raised.exception.__cause__,secondary)

    def test_receipt_success_and_absence_remain_distinguishable(self):
        service=DurableQueryFoundryService()
        for receipt in ({'versions':['0001']},None):
            with patch.object(service,'_read_receipt',return_value=receipt):
                self.assertIs(service._read_receipt_after_error('control','job','signature',RuntimeError('lost reply')),receipt)

    def test_version_finalization_preserves_primary_failure_when_probe_fails(self):
        service=DurableQueryFoundryService()
        result={'qf_job_id':'job','qf_request_sha256':'signature'}
        original=RuntimeError('SSH lost while committing versions')
        secondary=ConnectionError('Server restarting during probe')
        with patch.object(service,'_command',return_value=''), \
             patch.object(service,'_read_receipt',side_effect=[None,secondary]), \
             patch.object(QueryFoundryService,'register_cross_table_expansion_versions',side_effect=original):
            with self.assertRaises(RuntimeError) as raised:
                service.register_cross_table_expansion_versions(result,'research','lab')
        self.assertIs(raised.exception,original)
        self.assertIs(raised.exception.__cause__,secondary)
        self.assertIsNone(service._qf_context)


if __name__=='__main__': unittest.main()
