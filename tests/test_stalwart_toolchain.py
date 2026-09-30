#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Offline extraction boundaries for the explicitly fetched Rust toolchain."""

import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_stalwart_toolchain as stage


class ArchiveTests(unittest.TestCase):
    def archive(self, entries):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                archive.addfile(item)
        buffer.seek(0)
        return tarfile.open(fileobj=buffer, mode="r:")

    def test_accepts_only_bounded_regular_payload_with_parents(self):
        with self.archive([("rustc-v", tarfile.DIRTYPE),
                           ("rustc-v/rustc", tarfile.DIRTYPE),
                           ("rustc-v/rustc/bin", tarfile.DIRTYPE),
                           ("rustc-v/rustc/bin/rustc", tarfile.REGTYPE)]) as archive:
            self.assertEqual(len(stage.preflight(archive, "rustc-v")), 4)

    def test_rejects_traversal_links_missing_parents_and_duplicates(self):
        for path, kind in [("rustc-v/../escape", tarfile.REGTYPE),
                           ("/rustc-v/rooted", tarfile.REGTYPE),
                           ("rustc-v/link", tarfile.SYMTYPE),
                           ("rustc-v/link", tarfile.LNKTYPE),
                           ("rustc-v/missing/file", tarfile.REGTYPE),
                           ("rustc-v", tarfile.DIRTYPE)]:
            with self.subTest(path=path, kind=kind), self.archive(
                    [("rustc-v", tarfile.DIRTYPE), (path, kind)]) as archive:
                with self.assertRaises(ValueError):
                    stage.preflight(archive, "rustc-v")

    def test_inventory_rejects_symlink_and_detects_changed_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "toolchain").mkdir()
            (output / "notices").mkdir()
            path = output / "toolchain/rustc"
            path.write_bytes(b"original")
            first = stage.inventory(output)
            path.write_bytes(b"modified")
            self.assertNotEqual(first, stage.inventory(output))
            (output / "toolchain/escape").symlink_to("/tmp")
            with self.assertRaises(ValueError):
                stage.inventory(output)


if __name__ == "__main__":
    unittest.main()
