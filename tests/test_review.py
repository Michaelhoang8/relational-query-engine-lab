"""Independent hash-collision and valid-plan boundary checks."""
from collections import Counter
import unittest

from relab.engine import Table, execute, optimize


class ReviewTests(unittest.TestCase):
    def test_distinct_colliding_integer_keys_keep_bag_and_outer_join_semantics(self):
        prime = 2**61 - 1
        self.assertEqual(hash(0), hash(prime))
        self.assertEqual(hash(prime), hash(2 * prime))
        tables = {
            'left': Table((('l.k', 'integer'), ('l.label', 'text')),
                          ((0, 'zero'), (prime, 'prime'), (None, 'null'), (2 * prime, 'double'))),
            'right': Table((('r.k', 'integer'), ('r.v', 'text')),
                           ((0, 'a'), (0, 'b'), (prime, 'c'), (None, 'd'))),
        }
        expected = Counter([
            (0, 'zero', 0, 'a'), (0, 'zero', 0, 'b'),
            (prime, 'prime', prime, 'c'), (None, 'null', None, None),
            (2 * prime, 'double', None, None),
        ])
        for algorithm in ['nested', 'hash']:
            plan = {'op': 'join', 'left': {'op': 'scan', 'table': 'left'},
                    'right': {'op': 'scan', 'table': 'right'},
                    'keys': [['l.k', 'r.k']], 'kind': 'left', 'algorithm': algorithm}
            result = execute(plan, tables)
            self.assertEqual(Counter(map(tuple, result['rows'])), expected)
            self.assertEqual(result['stats']['root']['candidate_pairs'], 16 if algorithm == 'nested' else 3)
            if algorithm == 'hash':
                self.assertEqual(result['stats']['root']['distinct_build_keys'], 2)
                self.assertEqual(result['stats']['root']['largest_key_group'], 2)
            projected = execute({'op': 'project', 'input': plan, 'columns': []}, tables)
            self.assertEqual(projected['columns'], [])
            self.assertEqual(projected['rows'], [[], [], [], [], []])

    def test_optimizer_retains_valid_selections_when_combining_exceeds_predicate_depth(self):
        tables = {'data': Table((('value', 'integer'),), ((0,), (1,), (None,)))}
        predicate = {'op': 'eq', 'args': [{'column': 'value'}, {'literal': 1}]}
        for _ in range(12):
            predicate = {'op': 'not', 'args': [predicate]}
        original = {'op': 'select', 'predicate': {'op': 'true'}, 'input': {
            'op': 'select', 'predicate': predicate, 'input': {'op': 'scan', 'table': 'data'},
        }}
        self.assertEqual(execute(original, tables)['rows'], [[1]])
        optimized, changes = optimize(original, tables)
        self.assertEqual(optimized, original)
        self.assertNotIn('combine_selections', changes)
        self.assertEqual(execute(optimized, tables)['rows'], [[1]])


if __name__ == '__main__':
    unittest.main()
