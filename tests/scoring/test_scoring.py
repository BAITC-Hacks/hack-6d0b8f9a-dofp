"""Behavioral acceptance tests for scoring, independent of NetworkX and pandas."""
import json
import random
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path

from moneygraph.analytics.roles import NodeFeatures, RolePolicy, from_feature_record
from moneygraph.analytics.ranking import positive_percentiles, score_nodes, top_records

END = date(2026, 7, 31)


def node(**updates):
    values = dict(gid=1, depth=1, is_seed=False, cluster_id=0,
                  in_degree=0, out_degree=0, in_minor=0, out_minor=0,
                  in_tx=0, out_tx=0, reachable_seeds=0, betweenness=0.0,
                  other_neighbor_clusters=0, active_in_days=0)
    values.update(updates)
    return NodeFeatures(**values)


def incoming(**updates):
    values = dict(in_degree=1, in_minor=100000, in_tx=5,
                  active_in_days=3, last_in_date=date(2026, 7, 20))
    values.update(updates)
    return node(**values)


def score(n):
    return score_nodes([n], period_end=END)[0]


class ScoringTests(unittest.TestCase):
    def test_isolate_preserved(self):
        result = score(node())
        self.assertEqual((result.assignment.role, result.assignment.role_score, result.priority_score),
                         ('peripheral', 0, 0))
        self.assertIn('isolated', result.assignment.limitations)

    def test_boundary_not_terminal(self):
        result = score(incoming(depth=4))
        self.assertNotEqual(result.assignment.role, 'terminal')
        self.assertIn('дальнейшие переводы не наблюдаются', result.evidence)

    def test_boundary_can_be_consolidator(self):
        result = score(incoming(depth=4, in_degree=12, in_tx=12, reachable_seeds=3))
        self.assertEqual(result.assignment.role, 'consolidator')
        self.assertLessEqual(result.assignment.role_score, .6)
        self.assertGreater(result.priority_score, .5)

    def test_seed_never_terminal_or_transit_via_balance(self):
        for kwargs in ({}, dict(out_degree=1, out_minor=100000, out_tx=2)):
            result = score(incoming(depth=0, is_seed=True, **kwargs))
            self.assertNotIn(result.assignment.role, ('terminal', 'transit'))

    def test_transit_and_temporal_cap(self):
        n = incoming(in_degree=3, out_degree=3, out_minor=100000, out_tx=3)
        a, b = score(n), score(replace(n, temporal_transit_confirmed=True))
        self.assertEqual(a.assignment.role, 'transit')
        self.assertEqual(a.assignment.role_score, .55)
        self.assertEqual(b.assignment.role_score, .75)
        self.assertEqual(a.priority_score, b.priority_score)

    def test_terminal_requires_date_window(self):
        self.assertEqual(score(incoming()).assignment.role, 'terminal')
        self.assertEqual(score(incoming()).assignment.role_score, .5)
        for last in (None, date(2026, 7, 30), date(2026, 7, 31)):
            self.assertNotEqual(score(incoming(last_in_date=last)).assignment.role, 'terminal')
        self.assertEqual(score(incoming(last_in_date=date(2026, 7, 29))).assignment.role, 'terminal')

    def test_terminal_window_not_hardcoded_to_july(self):
        result = score_nodes([incoming(last_in_date=date(2026, 8, 30))], period_end=date(2026, 8, 31))[0]
        self.assertNotEqual(result.assignment.role, 'terminal')

    def test_distributor(self):
        result = score(node(out_degree=15, out_minor=900000, out_tx=15))
        self.assertEqual(result.assignment.role, 'distributor')
        self.assertIn('out_exceeds_observed_in', result.assignment.limitations)

    def test_coordinator_requires_multiple_communities(self):
        n = incoming(in_degree=3, out_degree=3, out_tx=3, out_minor=200000,
                     reachable_seeds=3, betweenness=.1, other_neighbor_clusters=3)
        self.assertEqual(score(n).assignment.role, 'coordinator')
        self.assertNotEqual(score(replace(n, other_neighbor_clusters=1)).assignment.role, 'coordinator')

    def test_ambiguity_and_tie_order(self):
        n = incoming(in_degree=9, in_tx=9, out_degree=3, out_tx=3, out_minor=100000,
                     reachable_seeds=3, betweenness=.1, other_neighbor_clusters=3)
        result = score(n)
        self.assertEqual(result.assignment.role, 'coordinator')
        self.assertEqual(result.assignment.role_score, .6)
        self.assertIn('ambiguous_roles', result.assignment.caps)
        self.assertGreaterEqual(sum(c.accepted for c in result.assignment.candidates), 2)

    def test_percentiles_with_ties_zeros_and_exact_large_integers(self):
        self.assertEqual(positive_percentiles([0, 10, 10, 20]), [0, .5, .5, 1])
        self.assertEqual(positive_percentiles([2**60, 2**60+1]), [.5, 1])
        self.assertEqual(positive_percentiles([0, 0]), [0, 0])

    def test_permutation_invariance_and_rank_ties(self):
        nodes = [node(gid=i) for i in range(40)]
        expected = score_nodes(nodes, period_end=END)
        random.Random(42).shuffle(nodes)
        self.assertEqual(score_nodes(nodes, period_end=END), expected)
        top = top_records(expected)
        self.assertEqual(len(top), 30)
        self.assertEqual([r['gid'] for r in top], list(range(30)))
        self.assertEqual([r['rank'] for r in top], list(range(1, 31)))

    def test_priority_decomposes_and_json_serializes(self):
        result = score(incoming(in_degree=5, in_tx=5, reachable_seeds=3))
        self.assertAlmostEqual(sum(result.contributions.values()), result.priority_score)
        json.dumps(result.explanation_record(), allow_nan=False)
        self.assertLessEqual(len(result.evidence), 200)
        self.assertEqual(set(result.csv_record()), {'gid','role','role_score','cluster_id','priority_score','evidence'})

    def test_reject_bad_input(self):
        for updates in ({'gid': True}, {'gid': 1.2}, {'cluster_id': -1}, {'depth': 0},
                        {'betweenness': float('nan')}, {'in_minor': -1}, {'in_minor': 1.5},
                        {'is_seed': 'false'}, {'in_degree': 1}, {'active_in_days': 1},
                        {'temporal_transit_confirmed': 1}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                node(**updates)
        with self.assertRaises(ValueError):
            score_nodes([node(), node()], period_end=END)
        with self.assertRaises(ValueError):
            score(incoming(last_in_date=date(2026, 8, 1)))

    def test_config_validation(self):
        for updates in ({'minimum_support': 2}, {'flow_tolerance': 0},
                        {'terminal_window_days': 1.5}, {'fan_ratio': float('inf')}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                RolePolicy(**updates)

    def test_empty_input_and_small_fixture(self):
        self.assertEqual(score_nodes([], period_end=END), [])
        self.assertEqual(top_records([]), [])
        self.assertEqual(len(top_records([score(node())])), 1)
        with self.assertRaises(ValueError):
            top_records([], limit=19)

    def test_no_division_by_zero(self):
        result = score(node(out_degree=1, out_tx=1, out_minor=500000))
        json.dumps(result.explanation_record(), allow_nan=False)
        self.assertNotEqual(result.assignment.role, 'transit')

    def test_missing_active_days_does_not_invent_terminal(self):
        self.assertNotEqual(score(incoming(active_in_days=None)).assignment.role, 'terminal')

    def test_team_boundary_with_known_outgoing_is_still_incomplete(self):
        result = score(incoming(depth=4, in_degree=3, out_degree=3,
                                out_minor=100000, out_tx=3))
        self.assertNotEqual(result.assignment.role, 'transit')
        self.assertIn('depth_boundary', result.assignment.limitations)
        self.assertIn('исходящие видны не полностью', result.evidence)

    def test_team_feature_adapter_preserves_flags_and_calendar_dates(self):
        record = asdict(incoming())
        record.update(last_in_date=datetime(2026, 7, 20),
                      observation_flags=['date_only', 'self_transfer'],
                      pagerank=.1, seed_paths=[[99, 1]], depth_boundary=False)
        n = from_feature_record(record)
        self.assertEqual(n.last_in_date, date(2026, 7, 20))
        self.assertIn('self_transfer', score(n).assignment.limitations)
        self.assertEqual(score(n).assignment.limitations.count('date_only'), 1)
        record['last_in_date'] = float('nan')
        self.assertIsNone(from_feature_record(record).last_in_date)
        record['depth_boundary'] = True
        with self.assertRaises(ValueError):
            from_feature_record(record)

    def test_self_transfer_is_not_terminal(self):
        result = score(incoming(out_degree=0, out_tx=1, out_minor=50000))
        self.assertNotEqual(result.assignment.role, 'terminal')
        terminal = next(c for c in result.assignment.candidates if c.role == 'terminal')
        self.assertFalse(terminal.conditions['incoming_only'])

    def test_large_money_window_uses_exact_comparison(self):
        # Both outside values round to the same floats as the window limits.
        amount = 5 * 10**17
        for outgoing, expected in ((4 * 10**17-1, False), (4 * 10**17, True),
                                   (6 * 10**17, True), (6 * 10**17+1, False)):
            with self.subTest(outgoing=outgoing):
                result = score(incoming(in_minor=amount, out_minor=outgoing,
                                        out_degree=3, out_tx=3))
                transit = next(c for c in result.assignment.candidates if c.role == 'transit')
                self.assertEqual(transit.conditions['flow_window'], expected)

    def test_json_preserves_int64_ids_and_amounts(self):
        result = score(incoming(gid=2**63-1, in_minor=2**60+1))
        record = json.loads(json.dumps(result.explanation_record(), allow_nan=False))
        self.assertEqual(record['features']['gid'], str(2**63-1))
        self.assertEqual(record['features']['in_minor'], str(2**60+1))
        self.assertEqual(result.csv_record()['gid'], 2**63-1)

    def test_rankdata_matches_pairwise_definition(self):
        values = [random.Random(seed).randint(0, 25) for seed in range(150)]
        positive = [v for v in values if v > 0]
        expected = [0 if v == 0 else
                    (sum(x < v for x in positive) + (sum(x == v for x in positive)+1)/2)
                    / len(positive) for v in values]
        self.assertEqual(positive_percentiles(values), expected)

    def test_prepared_features_cli_without_graph_dependency(self):
        root = Path(__file__).resolve().parents[2]
        snapshot = {'period_end': END.isoformat(),
                    'features': [asdict(node(gid=i)) for i in range(25)]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'features.json'
            path.write_text(json.dumps(snapshot), encoding='utf-8')
            result = subprocess.run(
                [sys.executable, '-B', str(Path(__file__).with_name('validate_features.py')),
                 str(path), '--sensitivity', '--compare-caps'], cwd=root,
                env={**os.environ, 'PYTHONPATH': str(root)},
                text=True, capture_output=True, check=False, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['nodes'], 25)
        self.assertEqual(len(report['sensitivity']), 16)
        self.assertTrue(all(r['top20_overlap'] == 20 for r in report['sensitivity']))
        caps = report['caps_comparison']
        self.assertEqual(caps['top_size'], 20)
        self.assertEqual(caps['top20_overlap'], 20)  # equal fixtures retain gid tie-breaking
        self.assertGreaterEqual(caps['max_priority_reduction'], 0)


if __name__ == '__main__':
    unittest.main()
