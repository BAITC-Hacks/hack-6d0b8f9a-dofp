"""Validate scoring on a prepared feature snapshot; never builds a graph.

Input JSON: {"period_end": "YYYY-MM-DD", "features": [NodeFeatures records]}.
Run: PYTHONPATH=. python -B tests/scoring/validate_features.py features.json --sensitivity
"""
import argparse
import json
from collections import Counter
from dataclasses import fields, replace
from datetime import date
from decimal import Decimal
from math import ceil
from pathlib import Path
from time import perf_counter

from moneygraph.analytics.roles import RolePolicy, from_feature_record
from moneygraph.analytics.ranking import score_nodes, top_records


def sensitivity(features, period_end, baseline):
    """One threshold at a time; feature extraction is outside this module."""
    policy = RolePolicy()
    baseline_top = {r['gid'] for r in top_records(baseline, limit=20)}
    scenarios = []
    for field in fields(policy):
        original = getattr(policy, field.name)
        for factor in (Decimal('0.8'), Decimal('1.2')):
            adjusted = Decimal(str(original)) * factor
            adjusted = ceil(adjusted) if isinstance(original, int) else float(adjusted)
            if field.name in ('minimum_support', 'coordinator_percentile', 'ambiguity_gap', 'flow_tolerance'):
                adjusted = min(adjusted, 1.0)
            if adjusted == original:
                continue
            variant = score_nodes(features, period_end=period_end,
                                  policy=replace(policy, **{field.name: adjusted}))
            changed = sum(a.assignment.role != b.assignment.role for a, b in zip(baseline, variant))
            overlap = len(baseline_top & {r['gid'] for r in top_records(variant, limit=20)})
            scenarios.append({'parameter': field.name, 'value': adjusted,
                              'roles_changed': changed, 'top20_overlap': overlap,
                              'top_size': len(baseline_top)})
    return scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--sensitivity', action='store_true')
    args = parser.parse_args()
    payload = json.loads(args.snapshot.read_text(encoding='utf-8'))
    period_end = date.fromisoformat(payload['period_end'])
    features = []
    for record in payload['features']:
        row = dict(record)
        if row.get('last_in_date') is not None:
            row['last_in_date'] = date.fromisoformat(row['last_in_date'])
        features.append(from_feature_record(row))
    start = perf_counter()
    scored = score_nodes(features, period_end=period_end)
    duration = perf_counter()-start
    assert {r.features.gid for r in scored} == {n.gid for n in features}
    assert all(0 <= r.priority_score <= 1 and 0 <= r.assignment.role_score <= 1 for r in scored)
    assert all(r.evidence and len(r.evidence) <= 200 for r in scored)
    assert not any(r.features.depth_boundary and r.assignment.role == 'terminal' for r in scored)
    assert not any(r.features.is_seed and r.assignment.role in ('transit', 'terminal') for r in scored)
    assert all(r.assignment.role_score == 0 for r in scored if r.features.isolated)
    assert score_nodes(reversed(features), period_end=period_end) == scored
    for row in scored:
        json.dumps(row.explanation_record(), allow_nan=False)
    assert len(top_records(scored)) == min(30, len(features))
    report = {'nodes': len(scored), 'roles': dict(Counter(r.assignment.role for r in scored)),
              'max_evidence_length': max((len(r.evidence) for r in scored), default=0),
              'scoring_seconds': round(duration, 3),
              'checks': 'passed; includes input-order invariance'}
    if args.sensitivity:
        report['sensitivity'] = sensitivity(features, period_end, scored)
        report['sensitivity_note'] = ('One parameter at a time, +/-20%; integer cutoffs use ceil; '
                                      'bounded fractions clipped to 1. No-op variations skipped. '
                                      'Graph features are fixed. This is not a test of graph methods.')
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
