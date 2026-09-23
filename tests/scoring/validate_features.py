"""Validate scoring on a prepared feature snapshot; never builds a graph.

Input JSON: {"period_end": "YYYY-MM-DD", "features": [NodeFeatures records]}.
Or features.parquet with explicit --period-end; pandas is then required.
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


def compare_capped_role_support(scored):
    """Audit only: replace role support with min(support, role_score), same weights.

    No production score changes. Peripheral's fallback score 0.2 is not evidence
    and must not become a new positive role contribution in this comparison.
    """
    baseline = sorted(scored, key=lambda n: (-n.priority_score, n.features.gid))
    alternative = {}
    for n in baseline:
        capped = min(n.assignment.support, n.assignment.role_score)
        alternative[n.features.gid] = sum(
            0.25*capped if key == 'role_support' else value
            for key, value in n.contributions.items())
    changed = sorted(scored, key=lambda n: (-alternative[n.features.gid], n.features.gid))
    original_ranks = {n.features.gid: rank for rank, n in enumerate(baseline, 1)}
    changed_ranks = {n.features.gid: rank for rank, n in enumerate(changed, 1)}
    top = baseline[:20]
    changes = [abs(changed_ranks[n.features.gid]-original_ranks[n.features.gid]) for n in top]
    reductions = [max(0.0, n.priority_score-alternative[n.features.gid]) for n in scored]
    return {'variant': 'M=min(assignment.support, role_score); other contributions unchanged',
            'top_size': len(top),
            'top20_overlap': len({n.features.gid for n in top} & {n.features.gid for n in changed[:20]}),
            'nodes_with_capped_support': sum(n.assignment.support > n.assignment.role_score for n in scored),
            'top20_with_caps': sum(n.assignment.support > n.assignment.role_score for n in top),
            'top20_depth_boundary': sum(n.features.depth_boundary for n in top),
            'top20_rank_changes': sum(change > 0 for change in changes),
            'top20_max_rank_shift': max(changes, default=0),
            'max_priority_reduction': max(reductions, default=0),
            'mean_priority_reduction': sum(reductions)/len(reductions) if reductions else 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--sensitivity', action='store_true')
    parser.add_argument('--compare-caps', action='store_true')
    parser.add_argument('--period-end', type=date.fromisoformat, help='Required for a Parquet feature table')
    args = parser.parse_args()
    if args.snapshot.suffix == '.parquet':
        if args.period_end is None:
            parser.error('--period-end is required for Parquet; do not infer the extraction window')
        import pandas as pd
        records = pd.read_parquet(args.snapshot).to_dict(orient='records')
        period_end = args.period_end
    else:
        payload = json.loads(args.snapshot.read_text(encoding='utf-8'))
        records = payload['features']
        period_end = date.fromisoformat(payload['period_end'])
        if args.period_end is not None and args.period_end != period_end:
            parser.error('--period-end disagrees with the JSON snapshot')
    features = []
    for record in records:
        row = dict(record)
        if isinstance(row.get('last_in_date'), str):
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
    if args.compare_caps:
        report['caps_comparison'] = compare_capped_role_support(scored)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
