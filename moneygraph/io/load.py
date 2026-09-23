"""Role-A loader. Shared DTO adaptation is owned by the pipeline integrator."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pandas as pd

from .validate import validate_tables


@dataclass(frozen=True)
class LoadedTables:
    nodes: pd.DataFrame
    edges: pd.DataFrame
    transactions: pd.DataFrame
    quality: dict


def load_dataset(input_dir, *, period_start, period_end, max_depth=4,
                 min_amount_minor=500_000):
    """Read/hash exactly the same bytes; do not write or publish input data."""
    tables, hashes = {}, {}
    for name in ("nodes", "edges", "transactions"):
        filename = f"{name}.parquet"
        payload = (Path(input_dir) / filename).read_bytes()
        hashes[filename] = sha256(payload).hexdigest()
        tables[name] = pd.read_parquet(BytesIO(payload))
    n, e, tx, quality = validate_tables(
        **tables, period_start=period_start, period_end=period_end,
        max_depth=max_depth, min_amount_minor=min_amount_minor)
    quality["input_sha256"] = hashes
    return LoadedTables(n, e, tx, quality)
