"""End-to-end contracts and failure recovery, using synthetic data only."""
import csv
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from moneygraph.config import PipelineConfig
from moneygraph.io.exports import CLUSTER_COLUMNS, NODE_COLUMNS, TOP_COLUMNS, kzt
from moneygraph.io.snapshots import ARTIFACTS, open_current, open_snapshot
from moneygraph.pipeline import run_pipeline


def fixture(directory: Path) -> set[int]:
    """Large IDs, duplicate operations, an isolate, and a depth-four boundary."""
    directory.mkdir()
    gids = [2**53 + i for i in range(1, 26)]
    nodes = pd.DataFrame({"gid": gids, "depth": [0, 0, 1, 2, 3, 4] + [1]*18 + [0],
                          "is_seed": [True, True] + [False]*22 + [True]})
    pairs = [(0, 2), (1, 2), (2, 3), (3, 4), (4, 5)] + [(2, i) for i in range(6, 24)]
    tx = pd.DataFrame([{"src": gids[s], "dst": gids[d], "sum_kzt": 5000.01, "date": "2026-07-12"}
                       for s, d in pairs + [pairs[0]]])
    edges = tx.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"),
                                                         n_tx=("sum_kzt", "size"))
    edges["depth"] = [max(1, int(nodes.set_index("gid").loc[g, "depth"])) for g in edges.dst]
    for name, frame in (("nodes", nodes), ("edges", edges), ("transactions", tx)):
        frame.to_parquet(directory / f"{name}.parquet", index=False)
    return set(gids)


def csv_rows(snapshot, name):
    with snapshot.artifact(name).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        return reader.fieldnames, rows


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.out = self.root / "data", self.root / "out"
        self.gids = fixture(self.data)

    def test_full_snapshot_round_trip_and_exact_ids(self):
        snapshot = run_pipeline(self.data, self.out)
        self.assertEqual(open_current(self.out), snapshot)
        columns, nodes = csv_rows(snapshot, "nodes_roles.csv")
        self.assertEqual(tuple(columns), NODE_COLUMNS)
        self.assertEqual({int(n["gid"]) for n in nodes}, self.gids)
        self.assertEqual(len(nodes), len(self.gids))
        self.assertTrue(all(all(v != "" for v in n.values()) for n in nodes))
        self.assertTrue(all(len(n["evidence"]) <= 200 for n in nodes))
        columns, clusters = csv_rows(snapshot, "clusters.csv")
        self.assertEqual(tuple(columns), CLUSTER_COLUMNS)
        self.assertEqual(sum(int(n["n_nodes"]) for n in clusters), len(self.gids))
        self.assertEqual(sum(int(n["n_seed"]) for n in clusters), 3)
        for row in clusters:
            self.assertTrue(set(json.loads(row["top_gids"])) <= {n["gid"] for n in nodes
                                                                  if n["cluster_id"] == row["cluster_id"]})
        columns, top = csv_rows(snapshot, "top_nodes.csv")
        self.assertEqual(tuple(columns), TOP_COLUMNS)
        self.assertEqual(len(top), 25)
        self.assertEqual([int(n["rank"]) for n in top], list(range(1, 26)))
        self.assertEqual(top, sorted(top, key=lambda n: (-float(n["priority_score"]), int(n["gid"]))))
        by_gid = {n["gid"]: n for n in nodes}
        for row in top:
            self.assertEqual(row["priority_score"], by_gid[row["gid"]]["priority_score"])
            self.assertEqual(row["role"], by_gid[row["gid"]]["role"])
        graph = json.loads(snapshot.artifact("graph.json").read_text())
        self.assertEqual({n["gid"] for n in graph["nodes"]}, {str(g) for g in self.gids})
        self.assertTrue(all(isinstance(e["sum_minor"], str) for e in graph["edges"]))
        for n in graph["nodes"]:
            self.assertTrue(all(isinstance(g, str) for route in n["seed_paths"] for g in route))
            self.assertEqual(n["role"], by_gid[n["gid"]]["role"])
            if n["depth_boundary"]:
                self.assertNotIn(n["role"], ("terminal", "transit"))
            if n["isolated"]:
                self.assertIsNone(n["observed_flow_ratio"])
                self.assertEqual(n["priority_score"], 0)
        tx = pd.read_parquet(snapshot.artifact("transactions.parquet"))
        self.assertEqual(len(tx), 24)
        self.assertEqual(set(tx.tx_ref), set(range(24)))
        self.assertEqual(sum(tx.sum_minor), 24*500001)
        self.assertEqual(kzt(9007199254740993), "90071992547409.93")
        manifest = json.loads(snapshot.artifact("manifest.json").read_text())
        self.assertEqual(set(manifest["artifacts"]), ARTIFACTS)
        explanations = [json.loads(line) for line in snapshot.artifact("explanations.jsonl").read_text().splitlines()]
        self.assertEqual({e["features"]["gid"] for e in explanations}, set(by_gid))
        self.assertTrue(all(isinstance(e["features"]["in_minor"], str) for e in explanations))

    def test_identical_run_reuses_immutable_snapshot(self):
        first = run_pipeline(self.data, self.out)
        before = {p.name: p.read_bytes() for p in first.directory.iterdir()}
        second = run_pipeline(self.data, self.out)
        self.assertEqual(first, second)
        self.assertEqual(before, {p.name: p.read_bytes() for p in second.directory.iterdir()})
        changed = run_pipeline(self.data, self.out, config=replace(PipelineConfig(), seed=43))
        self.assertNotEqual(first.run_id, changed.run_id)
        self.assertEqual(open_current(self.out), changed)
        self.assertEqual(open_snapshot(first.directory), first)

    def test_failed_write_keeps_previous_current(self):
        previous = run_pipeline(self.data, self.out)
        before = (self.out / "current.json").read_bytes()
        def broken(directory, **kwargs):
            (directory / "nodes_roles.csv").write_text("partial")
            raise OSError("simulated disk failure")
        with patch("moneygraph.pipeline.write_outputs", side_effect=broken):
            with self.assertRaisesRegex(OSError, "simulated disk"):
                run_pipeline(self.data, self.out, config=replace(PipelineConfig(), seed=43))
        self.assertEqual(before, (self.out / "current.json").read_bytes())
        self.assertEqual(open_current(self.out), previous)
        self.assertFalse(list((self.out / "runs").glob(".pending-*")))

    def test_invalid_inputs_do_not_publish(self):
        previous = run_pipeline(self.data, self.out)
        edges = pd.read_parquet(self.data / "edges.parquet")
        edges.loc[0, "sum_kzt"] += 1
        edges.to_parquet(self.data / "edges.parquet", index=False)
        with self.assertRaisesRegex(ValueError, "amounts or counts"):
            run_pipeline(self.data, self.out)
        self.assertEqual(open_current(self.out), previous)

    def test_corrupted_old_snapshot_is_not_silently_repaired(self):
        previous = run_pipeline(self.data, self.out)
        before = (self.out / "current.json").read_bytes()
        previous.artifact("top_nodes.csv").write_text("damaged")
        with self.assertRaisesRegex(ValueError, "checksum"):
            run_pipeline(self.data, self.out)
        self.assertEqual(before, (self.out / "current.json").read_bytes())

    def test_failed_pointer_update_leaves_previous_readable(self):
        previous = run_pipeline(self.data, self.out)
        with patch("moneygraph.io.snapshots.os.replace", side_effect=OSError("pointer failure")):
            with self.assertRaisesRegex(OSError, "pointer failure"):
                run_pipeline(self.data, self.out, config=replace(PipelineConfig(), seed=43))
        self.assertEqual(open_current(self.out), previous)
        self.assertFalse(list(self.out.glob(".current-*")))

    def test_cli_success_and_invalid_period_exit_codes(self):
        command = [sys.executable, "-B", "-m", "moneygraph", "run", "--data", str(self.data),
                   "--out", str(self.out)]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(open_current(self.out).run_id, result.stdout)
        failed = subprocess.run(command + ["--period-start", "2026-08-01"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("period_start", failed.stderr)

    def test_all_isolates_still_export_complete_records(self):
        for name in ("edges", "transactions"):
            path = self.data / f"{name}.parquet"
            pd.read_parquet(path).iloc[:0].to_parquet(path, index=False)
        snapshot = run_pipeline(self.data, self.out)
        _, nodes = csv_rows(snapshot, "nodes_roles.csv")
        self.assertEqual(len(nodes), 25)
        self.assertTrue(all(n["role"] == "peripheral" and float(n["priority_score"]) == 0 for n in nodes))
        self.assertEqual(len(pd.read_parquet(snapshot.artifact("transactions.parquet"))), 0)
        self.assertEqual(len(csv_rows(snapshot, "clusters.csv")[1]), 25)


@unittest.skipUnless(os.environ.get("MONEYGRAPH_DATA_DIR"), "Local case data not supplied")
class ProvidedDatasetPipelineTests(unittest.TestCase):
    def test_exported_dataset_meets_required_sizes_and_money(self):
        from decimal import Decimal
        with TemporaryDirectory() as out:
            snapshot = run_pipeline(Path(os.environ["MONEYGRAPH_DATA_DIR"]), Path(out))
            _, nodes = csv_rows(snapshot, "nodes_roles.csv")
            _, clusters = csv_rows(snapshot, "clusters.csv")
            _, top = csv_rows(snapshot, "top_nodes.csv")
            self.assertEqual(len(nodes), 2248)
            self.assertEqual(len({r["gid"] for r in nodes}), 2248)
            self.assertEqual(sum(int(r["n_seed"]) for r in clusters), 81)
            self.assertEqual(sum(int(r["n_nodes"]) for r in clusters), 2248)
            self.assertEqual(len(top), 30)
            manifest = json.loads(snapshot.artifact("manifest.json").read_text())
            internal = sum(int(Decimal(r["sum_kzt_internal"])*100) for r in clusters)
            self.assertEqual(internal + int(manifest["graph_metadata"]["communities"]["intercluster_minor"]),
                             36589001201)
            self.assertEqual(len(pd.read_parquet(snapshot.artifact("transactions.parquet"))), 4840)


if __name__ == "__main__":
    unittest.main()
