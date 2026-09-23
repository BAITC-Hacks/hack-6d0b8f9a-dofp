"""The publication boundary is also safe when two writers finish together."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from moneygraph.io.snapshots import ARTIFACTS, open_current, publish_snapshot


class SnapshotPublicationTests(unittest.TestCase):
    def test_concurrent_identical_writers_publish_one_snapshot(self):
        with TemporaryDirectory() as temporary:
            out = Path(temporary)
            barrier = Barrier(2)
            def writer(directory):
                for name in ARTIFACTS:
                    (directory / name).write_text(name, encoding="utf-8")
                barrier.wait(timeout=10)
                return {"counts": {}}
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(publish_snapshot, out, {"fixture": 1}, writer) for _ in range(2)]
                snapshots = [f.result(timeout=15) for f in futures]
            self.assertEqual(snapshots[0], snapshots[1])
            self.assertEqual(open_current(out), snapshots[0])
            self.assertEqual(len(list((out / "runs").iterdir())), 1)

    def test_changed_outputs_under_same_identity_are_rejected(self):
        with TemporaryDirectory() as temporary:
            out = Path(temporary)
            def writer(directory, text="first"):
                for name in ARTIFACTS:
                    (directory / name).write_text(text, encoding="utf-8")
                return {}
            first = publish_snapshot(out, {"fixture": 1}, writer)
            with self.assertRaisesRegex(ValueError, "different artifacts"):
                publish_snapshot(out, {"fixture": 1}, lambda path: writer(path, "changed"))
            self.assertEqual(open_current(out), first)


if __name__ == "__main__":
    unittest.main()
