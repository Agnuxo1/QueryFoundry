import unittest
from scripts.resource_sampling import category, parse_sample, summarize_samples


class ResourceSamplingTests(unittest.TestCase):
    def test_temp_relations_including_toast_forks_and_segments(self):
        for filename in ('t3_123', 't3_123.1', 't3_124_fsm', 't3_124_vm.2'):
            self.assertEqual(category('/data/base/16384/' + filename), 'sql_temp_relations')
            self.assertEqual(category('/data/pg_tblspc/321/PG_17_202406281/16384/' + filename), 'sql_temp_relations')
        for filename in ('123', '123.1', '123_fsm', 't3_123junk', 't_123', 't3_123.tmp'):
            self.assertIsNone(category('/data/base/16384/' + filename))
        self.assertIsNone(category('/data/base/not_a_database/t3_123'))

    def test_work_files_and_block_units(self):
        self.assertEqual(category('/data/base/pgsql_tmp/pgsql_tmp32.0.sharedfileset/0.0'), 'executor_work_files')
        sample = parse_sample('1000\ninactive_file 200\nQF_FILES\n'
                              '/data/base/pgsql_tmp/pgsql_tmp32.0\t4097\t16\n'
                              '/data/base/16384/t3_123\t1\t8\n'
                              '/data/base/16384/123\t99999\t200\nQF_SCAN_EXIT:0\n', 1, 2)
        self.assertEqual(sample['working_set_bytes'], 800)
        self.assertEqual(sample['ephemeral_apparent_bytes'], 4098)
        self.assertEqual(sample['ephemeral_allocated_bytes'], 12288)

    def test_noncoincident_maxima_and_failed_scans(self):
        def sample(a, b, start, exit_code=0):
            return parse_sample(f'100\nQF_FILES\n/data/base/pgsql_tmp/pgsql_tmp32.0\t{a}\t0\n'
                                f'/data/base/16384/t3_123\t{b}\t0\nQF_SCAN_EXIT:{exit_code}', start, start+.2)
        result = summarize_samples([sample(100, 1, 1), sample(1, 200, 2), sample(999, 999, 4, 1)], errors=1)
        self.assertEqual(result['sampled_peak_ephemeral_apparent_bytes'], 201)
        self.assertEqual(result['failed_scan_count'], 2)
        self.assertEqual(result['max_observed_start_gap_seconds'], 2)
        self.assertIsNone(result['global_intermediate_disk_peak_bytes'])
        with self.assertRaises(ValueError):
            parse_sample('100\nQF_FILES\n', 1, 2)


if __name__ == '__main__':
    unittest.main()
