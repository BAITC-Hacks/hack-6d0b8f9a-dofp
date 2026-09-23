"""Plain-language views of existing results; never assign roles or change scores."""
from collections import Counter, defaultdict

ROLE_LABELS = {'consolidator': 'Сбор денег', 'distributor': 'Распределение денег',
    'transit': 'Передача дальше', 'terminal': 'Возможный конец цепочки',
    'coordinator': 'Связующий участник', 'peripheral': 'Роль не определена'}


def investigation_insight(node):
    """Describe existing features/contributions without inventing another score."""
    m = node.metrics
    incoming, outgoing = m.get('in_deg'), m.get('out_deg')
    seeds = m.get('reachable_seeds')
    reason = (f'Плательщиков: {incoming} → получателей: {outgoing}'
              if incoming is not None and outgoing is not None else 'Показателей переводов недостаточно')
    if 'isolated' in node.warnings:
        reason = 'В выборке нет переводов'
    elif node.role == 'coordinator' and seeds is not None:
        reason = f'Связан с исходными клиентами: {seeds}'
    labels = {'seed_reach': 'связи с исходными клиентами', 'role_support': 'совпадение признаков предполагаемой роли',
              'betweenness': 'положение между цепочками переводов', 'volume': 'объём переводов',
              'seed_context': 'связи с исходными клиентами', 'network_position': 'положение между цепочками переводов'}
    contributions = sorted((item for item in node.contributions if isinstance(item.get('value'), (int, float)) and item['value'] > 0),
                           key=lambda item: -item['value'])
    factors = [labels[item['key']] for item in contributions if item['key'] in labels][:2]
    priority_reason = ('На место в очереди больше всего влияют: ' + '; '.join(factors) + '.'
                       if factors else 'Подробные причины места в очереди не переданы в этом расчёте.')
    if 'isolated' in node.warnings and node.priority_score == 0:
        priority_reason = 'Клиент сохранён из исходного списка. В этой выборке нет переводов и сигналов для высокого приоритета.'
    return {'queue_reason': reason, 'priority_reason': priority_reason}


def explain_node(node):
    m = node.metrics
    incoming, outgoing = m.get('in_deg'), m.get('out_deg')
    if incoming is None or outgoing is None:
        return 'Для простого объяснения не хватает показателей переводов. Откройте подробности расчёта.'
    if 'isolated' in node.warnings:
        return 'Клиент есть в исходном списке, но в загруженных данных нет ни одного его перевода. По этим данным определить его роль нельзя.'
    facts = f'Отправителей денег: {incoming}. Получателей: {outgoing}. '
    meanings = {
        'consolidator': 'К нему сходятся переводы от нескольких людей — это похоже на сбор денег.',
        'distributor': 'Он переводит деньги многим получателям — это похоже на распределение.',
        'transit': 'Полученные и отправленные суммы близки. Возможно, он передаёт деньги дальше; это не доказывает, что пересылаются те же деньги.',
        'terminal': 'В этой выборке у него есть поступления, но нет исходящих переводов. Дальнейшее движение денег неизвестно.',
        'coordinator': 'Он связывает разные части сети и несколько исходных клиентов. Стоит проверить его место в цепочках переводов.',
        'peripheral': 'Наблюдаемых связей недостаточно, чтобы уверенно описать его роль.',
    }
    return facts + meanings[node.role]


def describe_clusters(clusters, nodes, edges):
    groups = defaultdict(list)
    flows = defaultdict(lambda: {'incoming_minor': 0, 'outgoing_minor': 0, 'n_operations': 0})
    for node in nodes.values():
        groups[node.cluster_id].append(node)
    for edge in edges:
        a, b = nodes[edge.src].cluster_id, nodes[edge.dst].cluster_id
        for cid in {a, b}:
            flows[cid]['n_operations'] += edge.n_tx
        if a != b:
            flows[a]['outgoing_minor'] += int(edge.sum_minor)
            flows[b]['incoming_minor'] += int(edge.sum_minor)
    for group in clusters:
        members = groups[group['cluster_id']]
        counts = Counter(n.role for n in members)
        flow = flows[group['cluster_id']]
        if flow['n_operations'] == 0:
            description = ('Клиент сохранён из исходного списка, но его переводов в этих данных нет.'
                if len(members) == 1 else 'В этой группе нет наблюдаемых переводов.')
            zero = '0 ₸ означает отсутствие переводов в этой выборке, а не нулевой баланс счёта.'
        else:
            active_roles = [f'{ROLE_LABELS[role]} — {count}' for role, count in sorted(counts.items()) if role != 'peripheral']
            description = ('В группе есть участники с такими признаками: ' + '; '.join(active_roles) + '.'
                if active_roles else 'Клиенты связаны переводами, но данных для определения их ролей недостаточно.')
            zero = ('Внутри группы переводов нет. Связи с другими группами учитываются отдельно.'
                if int(group['sum_minor_internal']) == 0 else '')
        group.update(description=description, zero_explanation=zero, role_counts=dict(counts),
            incoming_minor=str(flow['incoming_minor']), outgoing_minor=str(flow['outgoing_minor']),
            n_operations=flow['n_operations'])
