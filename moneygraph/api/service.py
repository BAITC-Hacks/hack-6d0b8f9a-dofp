"""Immutable snapshot adapter and bounded queries. No role/score computation."""
from collections import defaultdict, deque
from copy import deepcopy
import csv
from dataclasses import asdict, is_dataclass
from decimal import Decimal
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from .models import Edge, Node, Transaction, gid_string, minor_units, exact_integer
from .snapshot import SCHEMA, read_snapshot
from .presentation import explain_node, describe_clusters

EXPORT_COLUMNS = {
    'nodes_roles.csv': ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence'],
    'clusters.csv': ['cluster_id', 'n_nodes', 'n_seed', 'sum_kzt_internal', 'top_gids', 'hypothesis'],
    'top_nodes.csv': ['rank', 'gid', 'role', 'priority_score', 'why'],
}


def clean_metrics(value: Any) -> Any:
    """Missing feature values are null, never invalid JSON NaN/Infinity."""
    if isinstance(value, dict):
        return {key: clean_metrics(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_metrics(item) for item in value]
    if hasattr(value, 'tolist'):
        return clean_metrics(value.tolist())
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    return value


def normalize_node_record(original: Any) -> dict:
    """Adapt role A FeatureTable and role B ScoredNode without changing their modules."""
    original = asdict(original) if is_dataclass(original) else original.model_dump() if hasattr(original, 'model_dump') else dict(original)
    row = {**original.get('features', {}), **original}
    assignment = row.get('assignment', {})
    for key in ('role', 'role_score'):
        if key not in row and key in assignment:
            row[key] = assignment[key]
    for source, target in (('in_degree', 'in_deg'), ('out_degree', 'out_deg')):
        if source in row and target not in row:
            row[target] = row[source]
    flags = list(clean_metrics(row.get('warnings', [])))
    flags.extend(clean_metrics(row.get('observation_flags', [])))
    flags.extend(assignment.get('limitations', []))
    row['warnings'] = list(dict.fromkeys(flags))
    if 'paths' not in row and 'seed_paths' in row:
        row['paths'] = clean_metrics(row['seed_paths'])
    contributions = row.get('contributions', [])
    if isinstance(contributions, dict):
        labels = {'seed_reach': 'Связи с исходными узлами', 'role_support': 'Признаки роли', 'betweenness': 'Положение в сети', 'volume': 'Объём переводов'}
        row['contributions'] = [{'key': key, 'label': labels.get(key, key), 'value': value} for key, value in contributions.items()]
    if assignment:
        candidates = assignment.get('candidates', [])
        primary = next((c for c in candidates if c.get('role') == row.get('role')), None)
        row.setdefault('rule_id', primary.get('rule_id') if primary else None)
        row['alternatives'] = [candidate for candidate in candidates if candidate.get('accepted') and candidate.get('role') != row.get('role')]
        row['rule_details'] = clean_metrics({'assignment': assignment, 'factors': row.get('factors', {}), 'policy': row.get('policy', {}), 'rules_version': row.get('rules_version')})
    return row


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def read_parquet(path: Path) -> list[dict]:
    import pandas as pd
    frame = pd.read_parquet(path)
    # to_json would silently round int64 gids through JSON number handling downstream.
    return frame.to_dict('records')


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})
    return stream.getvalue().encode('utf-8-sig')


def load_directory(path: Path, data_dir: Path | None = None) -> dict:
    path = path.resolve()
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8-sig'))
    manifest_file = path / 'manifest.json'
    if not manifest_file.exists():
        raise ValueError('Snapshot requires manifest.json; pass a completed run directory')
    manifest, blobs = read_snapshot(path)
    def text_file(name):
        return blobs[name].decode('utf-8-sig')
    def csv_file(name):
        return list(csv.DictReader(io.StringIO(text_file(name), newline='')))
    def parquet_file(name):
        import pandas as pd
        return pd.read_parquet(io.BytesIO(blobs[name])).to_dict('records')
    result = {'manifest': manifest}
    for filename in EXPORT_COLUMNS:
        if filename not in blobs:
            raise ValueError(f'Incomplete snapshot: missing {filename}')
    result['nodes'] = csv_file('nodes_roles.csv')
    result['clusters'] = csv_file('clusters.csv')
    result['top_nodes'] = csv_file('top_nodes.csv')
    features = {}
    if 'features.parquet' in blobs:
        features = {gid_string(row['gid']): row for row in parquet_file('features.parquet')}
    if 'explanations.jsonl' in blobs:
        for line in text_file('explanations.jsonl').splitlines():
            if line.strip():
                row = normalize_node_record(json.loads(line))
                features.setdefault(gid_string(row['gid']), {}).update(row)
    result['nodes'] = [{**features.get(gid_string(row['gid']), {}), **row} for row in result['nodes']]
    graph_path = path / 'graph.json'
    if 'graph.json' in blobs:
        graph = json.loads(text_file('graph.json'))
        graph = graph.get('elements', graph)
        result['edges'] = graph.get('edges', graph.get('links', []))
        graph_nodes = {gid_string(row.get('data', row).get('gid', row.get('data', row).get('id'))): row.get('data', row) for row in graph.get('nodes', [])}
        result['nodes'] = [{**graph_nodes.get(gid_string(row['gid']), {}), **row} for row in result['nodes']]
    elif data_dir and (data_dir / 'edges.parquet').exists():
        result['edges'] = read_parquet(data_dir / 'edges.parquet')
    else:
        raise ValueError('Snapshot requires graph.json or --data containing edges.parquet')
    for base in (path, data_dir):
        if base and ('transactions.parquet' in blobs if base == path else (base / 'transactions.parquet').exists()):
            result['transactions'] = parquet_file('transactions.parquet') if base == path else read_parquet(base / 'transactions.parquet')
            break
    if data_dir and (data_dir / 'nodes.parquet').exists():
        raw = {gid_string(row['gid']): row for row in read_parquet(data_dir / 'nodes.parquet')}
        result['nodes'] = [{**raw.get(gid_string(row['gid']), {}), **row} for row in result['nodes']]
    if 'quality.json' in blobs:
        result['quality'] = json.loads(text_file('quality.json'))
    # Freeze source exports at startup; later file changes cannot mix runs.
    result['_export_bytes'] = {name: blobs[name] for name in EXPORT_COLUMNS}
    return result


class QueryService:
    def __init__(self, snapshot: Mapping | str | Path | Any, data_dir: Path | None = None):
        locator_run = None
        if is_dataclass(snapshot) and hasattr(snapshot, 'directory') and hasattr(snapshot, 'run_id'):
            locator_run = snapshot.run_id
            payload = load_directory(Path(snapshot.directory), data_dir)
        elif isinstance(snapshot, (str, Path)):
            payload = load_directory(Path(snapshot), data_dir)
        elif hasattr(snapshot, 'model_dump'):
            payload = snapshot.model_dump(mode='python')
        elif is_dataclass(snapshot):
            payload = asdict(snapshot)
        else:
            payload = deepcopy(dict(snapshot))
        self.manifest = dict(payload.get('manifest', {}))
        self.run_id = self.manifest.get('run_id')
        if locator_run is not None and locator_run != self.run_id:
            raise ValueError('Snapshot locator and manifest disagree')
        if not isinstance(self.run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', self.run_id):
            raise ValueError('manifest.run_id must be a nonempty URL-safe string')
        self.schema_version = str(self.manifest.get('schema_version', '1.0'))
        if self.schema_version != SCHEMA and self.schema_version.split('.')[0] != '1':
            raise ValueError(f'Unsupported snapshot schema_version: {self.schema_version}')
        if self.schema_version == SCHEMA:
            config = self.manifest.get('identity', {}).get('config', {})
            self.manifest.setdefault('period', {'start': config.get('period_start'), 'end': config.get('period_end')})
            self.manifest.setdefault('demo', False)
        self.warnings = list(self.manifest.get('warnings', []))
        self.quality = payload.get('quality', {})
        self.nodes: dict[str, Node] = {}
        for original in payload.get('nodes', []):
            row = normalize_node_record(original)
            metrics = dict(row.get('metrics') or {})
            for key in ('in_deg', 'out_deg', 'in_tx', 'out_tx', 'pagerank', 'betweenness', 'reachable_seeds', 'active_days', 'observed_flow_ratio', 'pass_through'):
                if key in row and row[key] is not None:
                    metrics[key] = row[key]
            for direction in ('in', 'out'):
                minor_key = f'{direction}_minor'
                if minor_key in row:
                    metrics[minor_key] = str(row[minor_key])
                elif f'{direction}_kzt' in row:
                    metrics[minor_key] = minor_units(row[f'{direction}_kzt'])
            row['metrics'] = clean_metrics(metrics)
            node = Node.model_validate(row)
            if node.gid in self.nodes:
                raise ValueError(f'Duplicate gid: {node.gid}')
            if node.depth == 4 and 'depth_boundary' not in node.warnings:
                node.warnings.append('depth_boundary')
            self.nodes[node.gid] = node
        if not self.nodes:
            raise ValueError('Snapshot contains no nodes')
        self.edges: list[Edge] = []
        self.neighbors: dict[str, set[str]] = defaultdict(set)
        pairs = set()
        for original in payload.get('edges', []):
            row = dict(original.get('data', original))
            row['src'] = row.get('src', row.get('source'))
            row['dst'] = row.get('dst', row.get('target'))
            if 'sum_minor' not in row:
                row['sum_minor'] = minor_units(row['sum_kzt'])
            edge = Edge.model_validate(row)
            if edge.src not in self.nodes or edge.dst not in self.nodes:
                raise ValueError('Edge endpoint missing from nodes')
            if (edge.src, edge.dst) in pairs:
                raise ValueError('Duplicate aggregated edge')
            pairs.add((edge.src, edge.dst))
            self.edges.append(edge)
            self.neighbors[edge.src].add(edge.dst)
            self.neighbors[edge.dst].add(edge.src)
        self.ranked = sorted(self.nodes.values(), key=lambda node: (-node.priority_score, int(node.gid)))
        self.rank = {node.gid: index + 1 for index, node in enumerate(self.ranked)}
        self.clusters = []
        cluster_ids = set()
        for original in payload.get('clusters', []):
            row = dict(original)
            cid = exact_integer(row['cluster_id'])
            if cid in cluster_ids:
                raise ValueError('Duplicate cluster_id')
            cluster_ids.add(cid)
            row['cluster_id'] = cid
            row['n_nodes'], row['n_seed'] = exact_integer(row['n_nodes']), exact_integer(row['n_seed'])
            if not str(row.get('hypothesis', '')).strip():
                raise ValueError('Cluster hypothesis is required')
            if 'sum_minor_internal' not in row:
                row['sum_minor_internal'] = minor_units(row['sum_kzt_internal'])
            internal = exact_integer(row['sum_minor_internal'])
            if internal < 0:
                raise ValueError('Cluster money must be nonnegative')
            row['sum_minor_internal'] = str(internal)
            if 'sum_kzt_internal' not in row:
                row['sum_kzt_internal'] = str(Decimal(row['sum_minor_internal']) / 100)
            top = row.get('top_gids', [])
            if isinstance(top, str):
                try:
                    top = json.loads(top)
                except json.JSONDecodeError:
                    top = [part.strip() for part in top.split(',') if part.strip()]
            row['top_gids'] = [gid_string(gid) for gid in top]
            members = [node for node in self.nodes.values() if node.cluster_id == cid]
            if any(gid not in {node.gid for node in members} for gid in row['top_gids']):
                raise ValueError('Cluster top_gids must belong to that cluster')
            if len(members) != row['n_nodes'] or sum(node.is_seed for node in members) != row['n_seed']:
                raise ValueError(f'Cluster {cid} counts do not match nodes')
            self.clusters.append(row)
        if {node.cluster_id for node in self.nodes.values()} != cluster_ids:
            raise ValueError('Every node must have a corresponding cluster')
        self.clusters.sort(key=lambda row: row['cluster_id'])
        describe_clusters(self.clusters, self.nodes, self.edges)
        self.transactions_available = 'transactions' in payload
        self.transactions: dict[str, list[dict]] = defaultdict(list)
        tx_totals = defaultdict(lambda: [0, 0])
        for index, original in enumerate(payload.get('transactions', [])):
            row = dict(original)
            row['sum_minor'] = str(row['sum_minor']) if 'sum_minor' in row else minor_units(row['sum_kzt'])
            row['tx_ref'] = str(row.get('tx_ref', index))
            tx = Transaction.model_validate(row).model_dump()
            if tx['src'] not in self.nodes or tx['dst'] not in self.nodes:
                raise ValueError('Transaction endpoint missing from nodes')
            totals = tx_totals[(tx['src'], tx['dst'])]
            totals[0] += int(tx['sum_minor'])
            totals[1] += 1
            for gid in {tx['src'], tx['dst']}:
                self.transactions[gid].append(tx)
        for rows in self.transactions.values():
            rows.sort(key=lambda row: (row['date'], row['tx_ref']), reverse=True)
        if not self.transactions_available:
            self.warnings.append('transactions_not_loaded')
        if self.schema_version == SCHEMA:
            internal = defaultdict(int)
            expected_tx = {}
            for edge in self.edges:
                src_cluster = self.nodes[edge.src].cluster_id
                if src_cluster == self.nodes[edge.dst].cluster_id:
                    internal[src_cluster] += int(edge.sum_minor)
                expected_tx[(edge.src, edge.dst)] = [int(edge.sum_minor), edge.n_tx]
            if not self.transactions_available or dict(tx_totals) != expected_tx:
                raise ValueError('Snapshot transactions do not reconcile with edges')
            if any(int(row['sum_minor_internal']) != internal[row['cluster_id']] for row in self.clusters):
                raise ValueError('Snapshot cluster amounts do not reconcile with edges')
        self.top_rows = payload.get('top_nodes')
        if self.top_rows is None:
            self.top_rows = [{'rank': self.rank[node.gid], **node.model_dump(), 'why': node.evidence} for node in self.ranked[:30]]
        self._validate_top()
        self.exports = payload.get('_export_bytes') or {
            'nodes_roles.csv': csv_bytes([node.model_dump() for node in self.nodes.values()], EXPORT_COLUMNS['nodes_roles.csv']),
            'clusters.csv': csv_bytes(self.clusters, EXPORT_COLUMNS['clusters.csv']),
            'top_nodes.csv': csv_bytes(self.top_rows, EXPORT_COLUMNS['top_nodes.csv']),
        }

    def _validate_top(self):
        seen = set()
        previous = float('inf')
        for index, row in enumerate(self.top_rows):
            gid = gid_string(row['gid'])
            node = self.nodes.get(gid)
            if gid in seen or node is None or exact_integer(row['rank']) != index + 1:
                raise ValueError('Invalid top_nodes ranks or gids')
            score = float(row['priority_score'])
            if score != node.priority_score or row['role'] != node.role or score > previous or not str(row['why']).strip():
                raise ValueError('top_nodes conflicts with nodes_roles')
            seen.add(gid)
            previous = score

    def envelope(self, data: Any) -> dict:
        return {'schema_version': self.schema_version, 'run_id': self.run_id, 'data': data, 'warnings': self.warnings}

    def summary(self) -> dict:
        roles = {role: sum(node.role == role for node in self.nodes.values()) for role in sorted({n.role for n in self.nodes.values()})}
        return {**self.manifest, 'quality': self.quality, 'stats': {
            'n_nodes': len(self.nodes), 'n_edges': len(self.edges), 'n_seed': sum(n.is_seed for n in self.nodes.values()),
            'n_clusters': len(self.clusters), 'sum_minor': str(sum(int(e.sum_minor) for e in self.edges)),
            'depth_boundary': sum('depth_boundary' in n.warnings for n in self.nodes.values()),
            'role_counts': roles,
        }, 'currency': 'KZT', 'scale': 2}

    def node(self, gid: str) -> dict:
        row = self.nodes[gid].model_dump()
        return {**row, 'rank': self.rank[gid], 'explanation': explain_node(self.nodes[gid])}

    def list_nodes(self, role=None, cluster_id=None, seed_only=False, q=None, limit=50, offset=0) -> dict:
        rows = [node for node in self.ranked if (not role or node.role == role) and (cluster_id is None or node.cluster_id == cluster_id) and (not seed_only or node.is_seed) and (not q or q in node.gid)]
        return {'items': [self.node(node.gid) for node in rows[offset:offset + limit]], 'total': len(rows), 'limit': limit, 'offset': offset}

    def graph(self, gid=None, hops=1, limit=250, cluster_id=None) -> dict:
        # Undirected neighborhood navigation; returned edges keep original directions.
        if gid:
            order = [gid]
            seen = {gid}
            queue = deque([(gid, 0)])
            while queue:
                current, depth = queue.popleft()
                if depth >= hops:
                    continue
                neighbors = sorted(self.neighbors[current], key=lambda item: (-self.nodes[item].priority_score, int(item)))
                for other in neighbors:
                    if other not in seen:
                        seen.add(other)
                        order.append(other)
                        queue.append((other, depth + 1))
        else:
            order = [node.gid for node in self.ranked]
        if cluster_id is not None:
            order = [item for item in order if self.nodes[item].cluster_id == cluster_id]
        all_ids = set(order)
        selected = set(order[:limit])
        eligible_edges = [edge for edge in self.edges if edge.src in all_ids and edge.dst in all_ids]
        edges = [edge.model_dump() for edge in eligible_edges if edge.src in selected and edge.dst in selected]
        return {'nodes': [self.node(item) for item in order[:limit]], 'edges': edges, 'total_nodes': len(order), 'total_edges': len(eligible_edges), 'hidden_nodes': len(order) - len(selected), 'hidden_edges': len(eligible_edges) - len(edges), 'truncated': len(selected) < len(order), 'focus_gid': gid, 'hops': hops, 'neighborhood_direction': 'both'}

    def node_transactions(self, gid: str, limit=50, offset=0) -> dict:
        rows = self.transactions.get(gid, [])
        return {'items': rows[offset:offset + limit], 'total': len(rows), 'limit': limit, 'offset': offset, 'available': self.transactions_available, 'date_precision': 'day'}
