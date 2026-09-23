"""Pin verified artifact bytes before parsing, independent of pipeline imports."""
from hashlib import sha256
import json
from pathlib import Path

SCHEMA = 'moneygraph.snapshot.v1'
ARTIFACTS = frozenset({'nodes_roles.csv', 'clusters.csv', 'top_nodes.csv',
    'features.parquet', 'transactions.parquet', 'explanations.jsonl',
    'graph.json', 'quality.json'})


def read_snapshot(path: Path) -> tuple[dict, dict[str, bytes]]:
    manifest = json.loads((path / 'manifest.json').read_bytes().decode('utf-8-sig'))
    names = ARTIFACTS
    native = manifest.get('schema_version') == SCHEMA
    if native:
        identity = manifest.get('identity')
        canonical = (json.dumps(identity, ensure_ascii=False, sort_keys=True,
            allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
        if not isinstance(identity, dict) or sha256(canonical).hexdigest() != manifest.get('run_id'):
            raise ValueError('Snapshot identity mismatch')
        if set(manifest.get('artifacts', {})) != ARTIFACTS:
            raise ValueError('Incomplete snapshot artifact set')
    blobs = {}
    for name in names:
        target = path / name
        if native and target.is_symlink():
            raise ValueError(f'Snapshot artifact must not be a symlink: {name}')
        if target.is_file():
            blobs[name] = target.read_bytes()
        if native and (name not in blobs or sha256(blobs[name]).hexdigest() != manifest['artifacts'][name]):
            raise ValueError(f'Snapshot checksum mismatch: {name}')
    # These are the same bytes later parsed and exported; no second disk read.
    return manifest, blobs
