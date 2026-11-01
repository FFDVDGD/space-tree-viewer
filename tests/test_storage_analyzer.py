"""Synthetic reports only: python -B -m unittest discover -s tests -v"""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.storage_analyzer.export_storage_analyzer import build_document, select_report


class StorageAnalyzerTests(unittest.TestCase):
    def setUp(self):
        # Unordered, sparse IDs; two shares on volume2 and one on volume1.
        self.records = [
            (7, {"子目录": ["/volume2/共享/子目录", 31, 0, 2, 0, 20, [], {}]}),
            (20, {"共享": ["/volume2/共享", 103, 0, 5, 0, 20, [7], {}]}),
            (0, {"data": ["/volume1/data", 11, 0, 1, 0, 0, [], {}]}),
            (4, {"zero": ["/volume2/zero", 0, 0, 3, 0, 4, [], {}]}),
        ]

    def convert(self, records=None):
        return build_document(self.records if records is None else records,
                              "test-nas", "profile", "2026-11-01_15-11-23")

    def test_logical_totals_directory_hierarchy_and_zero_byte_files(self):
        document = self.convert()
        first, second = document["datasets"]
        self.assertEqual((first["path"], first["value"], first["files"], first["folders"]),
                         ("/volume1", 11, 1, 1))
        self.assertEqual((second["value"], second["files"], second["folders"]), (103, 8, 3))
        shared, zero = second["children"]
        child, direct = shared["children"]
        self.assertEqual((child["name"], child["value"], child["folders"]), ("子目录", 31, 0))
        self.assertEqual((direct["kind"], direct["value"], direct["logical"], direct["files"]),
                         ("aggregate", 72, 72, 3))
        self.assertEqual((zero["children"][0]["value"], zero["children"][0]["files"]), (0, 3))
        self.assertEqual(document["source"]["logical_bytes"], 114)
        self.assertEqual(document["source"]["directories"], 4)
        self.assertEqual(document["source"]["aggregates"], 4)
        self.assertEqual(document["source"]["metric"], "logical")
        self.assertNotIn("free", first)
        self.assertIn("逻辑大小", first["name"])

    def test_inconsistent_reports_fail_instead_of_silently_changing_totals(self):
        for field, value in [(1, 104), (3, 6), (5, 0), (6, [999])]:
            with self.subTest(field=field):
                records = deepcopy(self.records)
                records[0][1]["子目录"][field] = value
                with self.assertRaises(ValueError):
                    self.convert(records)
        with self.assertRaises(ValueError):
            self.convert(self.records + [self.records[0]])
        with self.assertRaises(ValueError):
            self.convert(self.records[1:])
        orphan = (9, {"orphan": ["/volume3/orphan", 1, 0, 1, 0, 99, [], {}]})
        with self.assertRaises(ValueError):
            self.convert(self.records + [orphan])

    def test_depth_limit_includes_volume_and_aggregate_nodes(self):
        for depth in [255, 256]:
            records = []
            for i in range(depth):
                path = "/volume1/root" + "/x" * i
                records.append((i, {"x": [path, 1, 0, 1, 0, max(0, i - 1),
                                          [i + 1] if i + 1 < depth else [], {}]}))
            if depth == 255:
                self.assertEqual(self.convert(records)["source"]["directories"], 255)
            else:
                with self.assertRaises(ValueError):
                    self.convert(records)

    def test_latest_completed_report_and_explicit_historical_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary)
            old = history / "2026-10-31_23-59-59"
            latest = history / "2026-11-01_15-11-23"
            incomplete = history / "2026-11-02_00-00-00"
            for report in [old, latest, incomplete]:
                report.mkdir()
                (report / "INFO").write_text('version="2"', encoding="utf-8")
                (report / "folder.db").touch()
            for report in [old, latest]:
                (report / "INFO").write_text('version="2"\nconsumed_time=1\n', encoding="utf-8")
                (report / "report.html").touch()
            self.assertEqual(select_report(history), latest.resolve())
            self.assertEqual(select_report(old), old.resolve())
            with self.assertRaises(ValueError):
                select_report(incomplete)
            # An HTML file alone is not enough while the elapsed-time marker is missing.
            (incomplete / "report.html").touch()
            self.assertEqual(select_report(history), latest.resolve())
            with self.assertRaises(ValueError):
                select_report(incomplete)

    def test_unsupported_version_cli_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary)
            (report / "INFO").write_text('version="3"\nconsumed_time=1\n', encoding="utf-8")
            (report / "report.html").touch()
            (report / "folder.db").touch()
            output = report / "existing.json.gz"
            output.write_bytes(b"keep existing data")
            script = Path(__file__).resolve().parents[1] / "tools/storage_analyzer/export_storage_analyzer.py"
            result = subprocess.run([sys.executable, "-B", str(script), "--report", str(report),
                                     "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Unsupported Storage Analyzer report version: 3", result.stderr)
            self.assertEqual(output.read_bytes(), b"keep existing data")


if __name__ == "__main__":
    unittest.main()
