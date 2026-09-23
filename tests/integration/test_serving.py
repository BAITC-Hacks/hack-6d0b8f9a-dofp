"""Opt-in real snapshot -> unmodified role-C API, including the actual --serve CLI.

Set MONEYGRAPH_API_TESTS=1 and MONEYGRAPH_DATA_DIR, and make codex/api-ui
importable through a combined checkout or PYTHONPATH. Once enabled, missing
dependencies fail rather than silently skip the integration gate.
"""
import csv
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

from moneygraph.pipeline import run_pipeline


@unittest.skipUnless(os.environ.get("MONEYGRAPH_API_TESTS") == "1", "Enable MONEYGRAPH_API_TESTS=1")
class ServingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from moneygraph.api import create_app
        cls.temp = TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.data = Path(os.environ["MONEYGRAPH_DATA_DIR"])
        cls.out = Path(cls.temp.name) / "out"
        cls.snapshot = run_pipeline(cls.data, cls.out)
        cls.client = TestClient(create_app(cls.snapshot.directory))
        cls.addClassCleanup(cls.client.close)
        cls.prefix = f"/api/v1/runs/{cls.snapshot.run_id}"

    def test_real_summary_cards_paths_and_transactions(self):
        summary = self.client.get("/api/v1/runs/current").json()
        self.assertEqual(summary["run_id"], self.snapshot.run_id)
        self.assertEqual(summary["schema_version"], "moneygraph.snapshot.v1")
        self.assertFalse(summary["data"]["demo"])
        self.assertEqual(summary["data"]["stats"]["n_nodes"], 2248)
        self.assertEqual(summary["data"]["period"], {"start": "2026-07-01", "end": "2026-07-31"})
        graph = json.loads(self.snapshot.artifact("graph.json").read_text())
        explanations = {r["features"]["gid"]: r for r in
                        map(json.loads, self.snapshot.artifact("explanations.jsonl").read_text().splitlines())}
        selected = {next(n["gid"] for n in graph["nodes"] if n[flag])
                    for flag in ("is_seed", "depth_boundary", "isolated")}
        selected.update(n["gid"] for n in sorted(graph["nodes"], key=lambda n: -n["priority_score"])[:3])
        for gid in selected:
            with self.subTest(gid=gid):
                response = self.client.get(f"{self.prefix}/nodes/{gid}")
                self.assertEqual(response.status_code, 200)
                node = response.json()["data"]
                source = explanations[gid]
                self.assertEqual(node["role"], source["assignment"]["role"])
                self.assertEqual(node["role_score"], source["assignment"]["role_score"])
                self.assertEqual(node["priority_score"], source["priority_score"])
                self.assertEqual(node["rule_details"]["assignment"]["caps"], source["assignment"]["caps"])
                self.assertTrue(set(source["assignment"]["limitations"]) <= set(node["warnings"]))
                self.assertAlmostEqual(sum(v["value"] for v in node["contributions"]), node["priority_score"])
                neighborhood = self.client.get(f"{self.prefix}/graph", params={"gid": gid, "hops": 1})
                self.assertEqual(neighborhood.status_code, 200)
                self.assertIn(gid, {n["gid"] for n in neighborhood.json()["data"]["nodes"]})
                tx = self.client.get(f"{self.prefix}/nodes/{gid}/transactions?limit=500").json()["data"]
                self.assertTrue(tx["available"])
                self.assertEqual(tx["total"], source["features"]["in_tx"] + source["features"]["out_tx"]
                                 - sum(e["n_tx"] for e in graph["edges"] if e["src"] == e["dst"] == gid))

    def test_exports_are_exact_bytes_of_the_pinned_snapshot(self):
        self.client.get(f"{self.prefix}/nodes?role=terminal")
        for name in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
            response = self.client.get(f"{self.prefix}/exports/{name}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["x-run-id"], self.snapshot.run_id)
            self.assertEqual(response.content, self.snapshot.artifact(name).read_bytes())

    def test_cli_serves_real_http_and_local_frontend(self):
        # Bind an unused loopback port; this process owns and terminates its server.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        root = Path(__file__).resolve().parents[2]
        log_path = Path(self.temp.name) / "server.log"
        with log_path.open("w+") as log:
            process = subprocess.Popen([sys.executable, "-B", "-m", "moneygraph", "run",
                                        "--data", str(self.data), "--out", str(self.out),
                                        "--serve", "--port", str(port)], cwd=root, stdout=log, stderr=log)
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = monotonic()+25
                while monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(log_path.read_text())
                    try:
                        with urlopen(base+"/health", timeout=.3) as response:
                            health = json.load(response)
                        break
                    except (URLError, TimeoutError):
                        sleep(.05)
                else:
                    self.fail("Server readiness timeout: " + log_path.read_text())
                self.assertEqual(health["run_id"], self.snapshot.run_id)
                with urlopen(base+"/", timeout=2) as response:
                    html = response.read().decode()
                assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
                self.assertTrue(assets, "Role C's built frontend must be available")
                for asset in assets:
                    with urlopen(base+asset, timeout=2) as response:
                        self.assertGreater(len(response.read()), 0)
                with urlopen(base+self.prefix+"/exports/nodes_roles.csv", timeout=2) as response:
                    self.assertEqual(response.read(), self.snapshot.artifact("nodes_roles.csv").read_bytes())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_cli_checks_hashes_before_calling_the_api_factory(self):
        from moneygraph.__main__ import main
        # Separate copy so other tests continue to use an immutable good snapshot.
        from shutil import copytree
        from moneygraph.io.snapshots import AnalysisSnapshot
        damaged = Path(self.temp.name) / "damaged"
        copytree(self.snapshot.directory, damaged)
        (damaged / "top_nodes.csv").write_text("corrupt")
        with patch("moneygraph.pipeline.run_pipeline", return_value=AnalysisSnapshot(damaged, self.snapshot.run_id)), \
             patch("moneygraph.api.create_app") as factory:
            code = main(["run", "--data", str(self.data), "--out", str(self.out), "--serve"])
            self.assertEqual(code, 2)
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
