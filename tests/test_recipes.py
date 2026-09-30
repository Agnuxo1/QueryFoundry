import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from services.postgres_service import PostgresAdminService
from dbperf.recipes import destinations, rewrite, MODES, ORDER

class RecipeTests(unittest.TestCase):
    def test_all_variants_pass_original_guards(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                result = PostgresAdminService._prepare_cross_table_expansion('public.raw_data',['group001'],destinations(mode))
                self.assertEqual([d['table_name'] for d in result['destinations']], ['public.'+t for t in ORDER])
                self.assertEqual(result['destinations'][2]['read_relations'], [('public','table3')])
    def test_reject_unsupported_rewrite(self):
        with self.assertRaises(ValueError):
            rewrite('SELECT 1','fields_once')
    def test_source_scope_and_dangerous_commands_still_rejected(self):
        for mode in MODES:
            recipes = destinations(mode)
            recipes[0]['sql'] += '\nDROP TABLE public.raw_data;'
            with self.assertRaises(RuntimeError):
                PostgresAdminService._prepare_cross_table_expansion('public.raw_data',['group001'],recipes)
    def test_existing_casts_and_filters_unchanged(self):
        for mode in MODES[1:]:
            for old, new in zip(destinations(),destinations(mode)):
                for token in ('::smallint','::numeric','::integer','NULLIF','BTRIM','ROUND','GREATEST'):
                    self.assertEqual(old['sql'].count(token), new['sql'].count(token))

if __name__ == '__main__': unittest.main()
