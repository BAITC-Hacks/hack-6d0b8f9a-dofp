"""Explicitly synthetic UI fixture. Not a result of analysing the supplied data."""


def demo_snapshot() -> dict:
    roles = ['peripheral', 'peripheral', 'consolidator', 'transit', 'coordinator', 'distributor', 'terminal', 'peripheral']
    nodes, edges, transactions, clusters = [], [], [], []
    ids = [[str(104201 + group * 1100 + index * 37) for index in range(8)] for group in range(3)]
    for group in range(3):
        for index, gid in enumerate(ids[group]):
            priority = [0.28, 0.24, 0.88, 0.69, 0.96, 0.78, 0.54, 0.19][index] - group * 0.035
            nodes.append({
                'gid': gid, 'role': roles[index], 'role_score': [0.2, 0.2, 0.82, 0.55, 0.76, 0.8, 0.5, 0.2][index],
                'priority_score': round(priority, 3), 'cluster_id': group + 1,
                'depth': [0, 0, 1, 2, 2, 3, 3, 4][index], 'is_seed': index < 2,
                'evidence': 'Синтетический пример для проверки интерфейса. Роль и приоритет заданы вручную, не являются результатом анализа.',
                'warnings': ['depth_boundary'] if index == 7 else ['seed_inflow_incomplete'] if index < 2 else [],
                'metrics': {'reachable_seeds': 2 if index > 1 else 0}, 'rule_id': 'demo.manual.v1',
                'contributions': [{'key': 'seed_context', 'label': 'Связи с исходными узлами', 'value': round(priority * .35, 5)}, {'key': 'role_support', 'label': 'Признаки роли', 'value': round(priority * .25, 5)}, {'key': 'network_position', 'label': 'Положение в сети', 'value': round(priority * .25, 5)}, {'key': 'volume', 'label': 'Объём переводов', 'value': round(priority * .15, 5)}],
                'paths': [[ids[group][0], ids[group][2], gid]] if index > 2 else [],
            })
        for edge_index, (src, dst) in enumerate([(0, 2), (1, 2), (2, 3), (3, 4), (2, 4), (4, 5), (5, 6), (5, 7), (3, 2)]):
            amount = (230000 + edge_index * 85000 + group * 40000) * 100
            edges.append({'src': ids[group][src], 'dst': ids[group][dst], 'sum_minor': str(amount), 'n_tx': 2})
    for group in range(2):
        edges.append({'src': ids[group][4], 'dst': ids[group + 1][2], 'sum_minor': '147000000', 'n_tx': 2})
    for i, edge in enumerate(edges):
        for j in range(2):
            transactions.append({'src': edge['src'], 'dst': edge['dst'], 'sum_minor': str(int(edge['sum_minor']) // 2), 'date': f'2026-07-{10 + (i % 15) + j:02d}', 'tx_ref': f'demo-{i}-{j}'})
    for node in nodes:
        inbound = [e for e in edges if e['dst'] == node['gid']]
        outbound = [e for e in edges if e['src'] == node['gid']]
        node['metrics'].update({'in_minor': str(sum(int(e['sum_minor']) for e in inbound)), 'out_minor': str(sum(int(e['sum_minor']) for e in outbound)), 'in_deg': len(inbound), 'out_deg': len(outbound), 'in_tx': sum(e['n_tx'] for e in inbound), 'out_tx': sum(e['n_tx'] for e in outbound)})
    for group, members in enumerate(ids):
        clusters.append({'cluster_id': group + 1, 'n_nodes': 8, 'n_seed': 2, 'sum_minor_internal': str(sum(int(e['sum_minor']) for e in edges if e['src'] in members and e['dst'] in members)), 'top_gids': [members[4], members[2]], 'hypothesis': 'Демонстрационная группа: два исходных узла, сходимость и последующее распределение переводов.'})
    return {'manifest': {'run_id': 'demo-v1', 'schema_version': '1.0', 'rules_version': 'demo.manual.v1', 'demo': True, 'period': {'start': '2026-07-01', 'end': '2026-07-31'}, 'warnings': ['synthetic_demo']}, 'nodes': nodes, 'edges': edges, 'clusters': clusters, 'transactions': transactions}
