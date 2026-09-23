"""Opt-in A -> scoring acceptance on the supplied case data.

Use the team's existing graph modules, from a combined checkout or a second
checkout on PYTHONPATH. Never reimplement them here. Without MONEYGRAPH_DATA_DIR,
these tests are skipped so the core scoring suite needs no graph dependencies.
"""
import csv
import io
import json
import os
import platform
import unittest
from collections import Counter
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from time import perf_counter

from moneygraph.analytics.roles import from_feature_record
from moneygraph.analytics.ranking import score_nodes, top_records


class GraphScoringContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data_dir = os.environ.get('MONEYGRAPH_DATA_DIR')
        if not data_dir:
            raise unittest.SkipTest('Set MONEYGRAPH_DATA_DIR to run the team integration')
        # Once explicitly enabled, missing dependencies/modules must fail visibly.
        import networkx
        import pandas
        import pyarrow
        import scipy
        from moneygraph.io.load import load_dataset
        from moneygraph.analytics.graph import build_graph
        from moneygraph.analytics.communities import detect_communities
        from moneygraph.analytics.features import compute_features

        start = perf_counter()
        cls.data = load_dataset(Path(data_dir), period_start='2026-07-01', period_end='2026-07-31')
        cls.period_end = date.fromisoformat(cls.data.quality['period_end'])
        graph = build_graph(cls.data.nodes, cls.data.edges, max_depth=cls.data.quality['max_depth'])
        assignment, cls.clusters, cls.community_meta = detect_communities(graph, resolution=1.0, seed=42)
        cls.table, cls.feature_meta = compute_features(
            graph, cls.data.transactions, assignment, period_end=cls.data.quality['period_end'],
            max_hops=4, betweenness_k=128, seed=42)
        cls.before = cls.table.copy(deep=True)
        cls.features = [from_feature_record(r) for r in cls.table.to_dict(orient='records')]
        scoring_start = perf_counter()
        cls.scored = score_nodes(cls.features, period_end=cls.period_end)
        cls.scoring_seconds = perf_counter()-scoring_start
        cls.total_seconds = perf_counter()-start
        cls.versions = {'python': platform.python_version(), 'pandas': pandas.__version__,
                        'pyarrow': pyarrow.__version__, 'networkx': networkx.__version__,
                        'scipy': scipy.__version__}

    def test_complete_team_feature_table_is_consumed_without_mutation(self):
        from pandas.testing import assert_frame_equal
        self.assertEqual(len(self.scored), 2248)
        self.assertEqual({r.features.gid for r in self.scored}, set(self.data.nodes.gid))
        self.assertEqual(sum(n.isolated for n in self.features), 19)
        self.assertEqual(sum(n.depth_boundary for n in self.features), 444)
        self.assertEqual(self.data.quality['duplicate_transaction_rows_preserved'], 97)
        self.assertEqual(sum(n.in_minor for n in self.features), 36589001201)
        self.assertEqual(sum(n.out_minor for n in self.features), 36589001201)
        source = {row['gid']: row for row in self.table.to_dict(orient='records')}
        for result in self.scored:
            row = source[result.features.gid]
            self.assertEqual(result.features.cluster_id, row['cluster_id'])
            self.assertTrue(set(row['observation_flags']) <= set(result.assignment.limitations))
        assert_frame_equal(self.table, self.before)

    def test_real_roles_explanations_and_export_records_agree(self):
        allowed = {'consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral'}
        by_gid = {r.features.gid: r for r in self.scored}
        for result in self.scored:
            n, role = result.features, result.assignment.role
            self.assertIn(role, allowed)
            self.assertTrue(isfinite(result.priority_score) and 0 <= result.priority_score <= 1)
            self.assertTrue(isfinite(result.assignment.role_score) and 0 <= result.assignment.role_score <= 1)
            self.assertTrue(0 < len(result.evidence) <= 200)
            self.assertAlmostEqual(sum(result.contributions.values()), result.priority_score)
            json.dumps(result.explanation_record(), allow_nan=False)
            if n.depth_boundary or n.is_seed:
                self.assertNotIn(role, ('transit', 'terminal'))
            if n.isolated:
                self.assertEqual((role, result.assignment.role_score, result.priority_score), ('peripheral', 0, 0))
            if role == 'terminal':
                self.assertEqual(n.out_tx, 0)
                self.assertIsNotNone(n.last_in_date)
                self.assertLessEqual(n.last_in_date, self.period_end-timedelta(days=2))
        top = top_records(self.scored)
        self.assertEqual(len(top), 30)
        self.assertEqual(len({r['gid'] for r in top}), 30)
        self.assertEqual([r['rank'] for r in top], list(range(1, 31)))
        self.assertEqual(top, sorted(top, key=lambda r: (-r['priority_score'], r['gid'])))
        for r in top:
            self.assertEqual(r['role'], by_gid[r['gid']].assignment.role)
            self.assertEqual(r['priority_score'], by_gid[r['gid']].priority_score)
        records = [r.csv_record() for r in self.scored]
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
        buffer.seek(0)
        restored = list(csv.DictReader(buffer))
        self.assertEqual({int(r['gid']) for r in restored}, set(by_gid))
        self.assertTrue(all(r['evidence'] for r in restored))

    def test_real_input_order_does_not_change_scoring(self):
        self.assertEqual(score_nodes(reversed(self.features), period_end=self.period_end), self.scored)

    @classmethod
    def tearDownClass(cls):
        print(json.dumps({'integration': 'A features -> scoring', 'versions': cls.versions,
                          'nodes': len(cls.scored), 'clusters': len(cls.clusters),
                          'roles': dict(Counter(r.assignment.role for r in cls.scored)),
                          'scoring_seconds': round(cls.scoring_seconds, 3),
                          'load_to_scored_seconds': round(cls.total_seconds, 3)}, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
