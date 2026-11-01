"""Synthetic Android streams only; no device or private file list required."""
import io
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest

from tools.android.export_android import read_scan


def stream(records, errors=0):
    data = bytearray(b"ASTREE1\n")
    count = 0
    for path, mode, blocks, size, inode, links in records:
        raw = path.encode("utf-8", "surrogateescape")
        data.extend(struct.pack("<IIQQQQQ", len(raw), mode, blocks, size, 123, inode, links))
        data.extend(raw)
        count += int(mode != 0)
    data.extend(struct.pack("<IIQQQQQ", 0, 0, errors, count, 0, 0, 0))
    return bytes(data)


class AndroidTests(unittest.TestCase):
    def test_allocated_boundaries_sparse_files_hardlinks_and_lossless_aggregation(self):
        directory, regular = stat.S_IFDIR | 0o700, stat.S_IFREG | 0o600
        records = [
            ("/data", directory, 8, 4096, 1, 2),
            ("/data/below", regular, 99, 50001, 2, 1),
            ("/data/equal", regular, 100, 51111, 3, 1),
            ("/data/above", regular, 101, 52001, 4, 1),
            ("/data/tiny", directory, 8, 4096, 5, 2),
            ("/data/tiny/中文\t\n\"\\\udcff", regular, 1, 123, 6, 1),
            ("/data/sparse", regular, 1, 300000000, 7, 1),
            ("/data/hardlink", regular, 100, 133333, 8, 2),
            ("/data/hardlink-copy", regular, 100, 133333, 8, 2),
            ("/data/link", stat.S_IFLNK | 0o777, 0, 80, 9, 1),
            ("/data/empty", regular, 0, 0, 10, 1),
            ("/data/equal-folder", directory, 8, 4096, 11, 2),
            ("/data/equal-folder/content", regular, 92, 23456, 12, 1),
        ]
        tree, counts = read_scan(io.BytesIO(stream(records)), 51200)
        self.assertEqual((tree["value"], tree["logical"], tree["files"], tree["folders"]),
                         (265216, 300310025, 10, 2))
        retained = {child["path"] for child in tree["children"] if child["kind"] != "aggregate"}
        self.assertEqual(retained, {"/data/equal", "/data/above", "/data/hardlink", "/data/equal-folder"})
        small = next(child for child in tree["children"] if child["kind"] == "aggregate")
        self.assertEqual((small["value"], small["logical"], small["files"], small["folders"]),
                         (59904, 300050124, 6, 1))
        self.assertEqual((counts["hardlinks_deduplicated"], counts["symlinks"]), (1, 1))
        # Totals remain exact at every retained directory, including directory blocks.
        pending = [tree]
        while pending:
            node = pending.pop()
            if node["children"]:
                for field in ("value", "logical", "files"):
                    self.assertEqual(node[field], sum(child[field] for child in node["children"]))
                self.assertEqual(node["folders"], sum(child["folders"] + (child["kind"] == "directory")
                                                      for child in node["children"]))
            pending.extend(node["children"])
        # Names containing control bytes and invalid UTF-8 survive when retained.
        detailed, _ = read_scan(io.BytesIO(stream(records)), 1)
        tiny = next(child for child in detailed["children"] if child.get("path") == "/data/tiny")
        self.assertEqual(tiny["children"][0]["name"], "中文\t\n\"\\\udcff")

    def test_raw_depth_beyond_viewer_limit_is_aggregated_before_serializing(self):
        records = [("/data" + "/x" * i, stat.S_IFDIR | 0o700, 8, 4096, i + 1, 2)
                   for i in range(321)]
        records.append(("/data" + "/x" * 320 + "/zero", stat.S_IFREG | 0o600, 0, 0, 1000, 1))
        tree, counts = read_scan(io.BytesIO(stream(records)), 50_000_000)
        self.assertEqual((tree["value"], tree["files"], tree["folders"]), (1314816, 1, 320))
        self.assertEqual(counts["display_nodes"], 2)
        self.assertEqual(tree["children"][0]["folders"], 320)

    def test_incomplete_or_ambiguous_scans_are_rejected(self):
        root = ("/data", stat.S_IFDIR | 0o700, 8, 4096, 1, 2)
        valid = stream([root])
        cases = [valid[:-1], valid[:53], valid + b"su diagnostic", b"denied\n",
                 stream([root, ("/data/missing", 0, 13, 0, 0, 0)], errors=1),
                 stream([root, ("/data/bind-alias", root[1], 8, 4096, 1, 2)]),
                 stream([root, ("/elsewhere/file", stat.S_IFREG | 0o600, 1, 20, 2, 1)])]
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                read_scan(io.BytesIO(data), 50_000_000)
        # A successful adb exit must not conceal scanner error/counter mismatches.
        damaged = bytearray(valid)
        struct.pack_into("<Q", damaged, len(damaged) - 32, 99)
        with self.assertRaises(ValueError):
            read_scan(io.BytesIO(damaged), 50_000_000)

    def test_cli_preserves_existing_output_and_rejects_invalid_threshold_without_scanning(self):
        script = Path(__file__).resolve().parents[1] / "tools/android/export_android.py"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "keep.json.gz"
            output.write_bytes(b"keep my report")
            for arguments, expected in [(["--output", str(output)], "Refusing to overwrite"),
                                        (["--min-mb", "0"], "must be positive"),
                                        (["--min-mb", "-1"], "must be positive")]:
                result = subprocess.run([sys.executable, "-B", str(script)] + arguments,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertEqual(output.read_bytes(), b"keep my report")


if __name__ == "__main__":
    unittest.main()
