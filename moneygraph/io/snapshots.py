"""Publish complete local snapshots; readers pin one run for their lifetime."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Callable

SCHEMA_VERSION = "moneygraph.snapshot.v1"
ARTIFACTS = frozenset({"nodes_roles.csv", "clusters.csv", "top_nodes.csv", "features.parquet",
                       "transactions.parquet", "explanations.jsonl", "graph.json", "quality.json"})


def canonical_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def identity_hash(identity: dict) -> str:
    return sha256(canonical_json(identity)).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        digest = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class AnalysisSnapshot:
    """Stable locator, not a mutable DataFrame or a changing current pointer."""
    directory: Path
    run_id: str

    def artifact(self, name: str) -> Path:
        if name not in ARTIFACTS | {"manifest.json"}:
            raise ValueError("Unknown snapshot artifact")
        return self.directory / name


def open_snapshot(directory: Path) -> AnalysisSnapshot:
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported snapshot schema")
    if manifest["run_id"] != identity_hash(manifest["identity"]):
        raise ValueError("Snapshot identity mismatch")
    if set(manifest["artifacts"]) != ARTIFACTS:
        raise ValueError("Incomplete snapshot artifact set")
    for name, expected in manifest["artifacts"].items():
        path = directory / name
        if path.is_symlink() or file_hash(path) != expected:
            raise ValueError(f"Snapshot checksum mismatch: {name}")
    return AnalysisSnapshot(directory, manifest["run_id"])


def open_current(out_dir: Path) -> AnalysisSnapshot:
    out_dir = Path(out_dir).resolve()
    current = json.loads((out_dir / "current.json").read_text(encoding="utf-8"))
    run_id = current["run_id"]
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{64}", run_id):
        raise ValueError("Invalid current run_id")
    snapshot = open_snapshot(out_dir / "runs" / run_id)
    if snapshot.run_id != run_id:
        raise ValueError("Current pointer and manifest disagree")
    return snapshot


def _replace_current(out_dir: Path, run_id: str) -> None:
    # Temp file and destination share a filesystem. Replace only after validation.
    name = None
    try:
        with NamedTemporaryFile(dir=out_dir, prefix=".current-", delete=False) as stream:
            name = Path(stream.name)
            stream.write(canonical_json({"schema_version": SCHEMA_VERSION, "run_id": run_id}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, out_dir / "current.json")
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def publish_snapshot(out_dir: Path, identity: dict, writer: Callable[[Path], dict]) -> AnalysisSnapshot:
    """writer fills a private staging directory and returns manifest metadata.

    An identical run is recomputed and its artifact hashes must match. Concurrent
    writers may both calculate, but never overwrite an existing run directory.
    This guards process failures; it is not a power-loss durability guarantee.
    """
    out_dir = Path(out_dir).resolve()
    runs = out_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_id = identity_hash(identity)
    target = runs / run_id
    with TemporaryDirectory(dir=runs, prefix=".pending-") as temporary:
        staging = Path(temporary) / "snapshot"
        staging.mkdir()
        metadata = writer(staging)
        actual = {p.name for p in staging.iterdir()}
        if actual != ARTIFACTS:
            raise ValueError("Writer did not produce the required artifact set")
        hashes = {name: file_hash(staging / name) for name in sorted(ARTIFACTS)}
        manifest = {**metadata, "schema_version": SCHEMA_VERSION, "run_id": run_id,
                    "identity": identity, "artifacts": hashes}
        (staging / "manifest.json").write_bytes(canonical_json(manifest))
        open_snapshot(staging)
        try:
            staging.rename(target)
        except OSError:
            if not target.is_dir():
                raise
            existing = open_snapshot(target)
            old = json.loads(existing.artifact("manifest.json").read_text(encoding="utf-8"))
            if existing.run_id != run_id or old["artifacts"] != hashes:
                raise ValueError("Identical run identity produced different artifacts")
        result = open_snapshot(target)
        _replace_current(out_dir, run_id)
        return result
