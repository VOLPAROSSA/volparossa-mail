"""Offline source staging tests; no Cargo, mail server, credentials or network."""
# SPDX-License-Identifier: GPL-3.0-only

import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_stalwart as source


def archive_bytes(additions):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        root = tarfile.TarInfo(source.PREFIX)
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for name, kind, contents in additions:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(contents) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "../../outside"
            archive.addfile(member, io.BytesIO(contents) if kind == tarfile.REGTYPE else None)
    return data.getvalue()


class StalwartSourceTests(unittest.TestCase):
    def test_exact_pin_original_notices_and_unexecuted_recipe(self):
        value = source.manifest()
        self.assertEqual(value["revision"], source.REVISION)
        self.assertEqual(value["community_build"]["upstream_default_features"], ["rocks", "enterprise"])
        self.assertFalse(value["community_build"]["compiled"])
        self.assertFalse(value["community_build"]["minimum_supported_compiler_verified"])
        self.assertIn("--no-default-features", source.BUILD_COMMAND)
        self.assertEqual(source.BUILD_COMMAND[-2:], ["--features", "sqlite"])
        self.assertEqual(value["source_verified_api"]["jmap_api"], "/jmap/")

    def test_archive_paths_links_special_files_and_duplicates_rejected(self):
        cases = [
            ("/outside", tarfile.REGTYPE),
            (source.PREFIX + "/../outside", tarfile.REGTYPE),
            (source.PREFIX + "/./file", tarfile.REGTYPE),
            (source.PREFIX + "/a//file", tarfile.REGTYPE),
            (source.PREFIX + "/a\\file", tarfile.REGTYPE),
            (source.PREFIX + "/a\nfile", tarfile.REGTYPE),
            ("wrong-root/file", tarfile.REGTYPE),
            (source.PREFIX + "/link", tarfile.SYMTYPE),
            (source.PREFIX + "/hard", tarfile.LNKTYPE),
            (source.PREFIX + "/pipe", tarfile.FIFOTYPE),
            (source.PREFIX + "/device", tarfile.CHRTYPE),
        ]
        for name, kind in cases:
            with self.subTest(name=name, kind=kind):
                raw = archive_bytes([(name, kind, b"x")])
                with tarfile.open(fileobj=io.BytesIO(raw)) as archive, self.assertRaises(ValueError):
                    source.archive_members(archive)
        raw = archive_bytes([(source.PREFIX + "/file", tarfile.REGTYPE, b"a")] * 2)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive, self.assertRaises(ValueError):
            source.archive_members(archive)

    def test_archive_size_count_and_missing_parent_limits(self):
        raw = archive_bytes([(source.PREFIX + "/file", tarfile.REGTYPE, b"ab")])
        for setting, limit in [("MAX_ENTRIES", 1), ("MAX_FILE_BYTES", 1), ("MAX_TOTAL_BYTES", 1)]:
            with patch.object(source, setting, limit), tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                with self.assertRaises(ValueError):
                    source.archive_members(archive)
        raw = archive_bytes([(source.PREFIX + "/absent/file", tarfile.REGTYPE, b"a")])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive, self.assertRaises(ValueError):
            source.archive_members(archive)

    def test_workspace_only_fresh_destination(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(source, "ROOT", Path(temporary)):
            root = Path(temporary)
            (root / "build").mkdir()
            self.assertEqual(source.workspace_path(root / "build/stage", fresh=True), root / "build/stage")
            for target in [root, root / "build", root / "outside"]:
                with self.assertRaises(ValueError):
                    source.workspace_path(target, fresh=True)
            (root / "build/link").symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                source.workspace_path(root / "build/link/stage", fresh=True)
            (root / "build/stage").mkdir()
            with self.assertRaises(ValueError):
                source.workspace_path(root / "build/stage", fresh=True)

    def test_verified_source_roundtrip_and_mutation_rejection(self):
        raw = archive_bytes([(source.PREFIX + "/file", tarfile.REGTYPE, b"source")])
        value = {"release": "fixture", "source_files": {}, "archive": {
            "entries": 2, "regular_files": 1, "unpacked_bytes": 6,
        }}
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(source, "ROOT", Path(temporary)), \
                patch.object(source, "ARCHIVE_SIZE", len(raw)), \
                patch.object(source, "ARCHIVE_SHA256", hashlib.sha256(raw).hexdigest()), \
                patch.object(source, "manifest", return_value=value), \
                patch.object(source.urllib.request, "build_opener", side_effect=AssertionError("network forbidden")):
            root = Path(temporary)
            (root / "build").mkdir()
            archive = root / "archive.tar.gz"
            archive.write_bytes(raw)
            stage = root / "build/stage"
            result = source.prepare(stage, archive_path=archive)
            self.assertFalse(result["compiled"])
            self.assertFalse(result["server_executed"])
            self.assertEqual(result, source.validate(stage))
            with self.assertRaises(ValueError):
                source.prepare(stage, archive_path=archive)
            file = stage / "source/file"
            file.write_bytes(b"change")
            with self.assertRaises(ValueError):
                source.validate(stage)
            file.write_bytes(b"source")
            extra = stage / "source/extra"
            extra.write_bytes(b"unexpected")
            with self.assertRaises(ValueError):
                source.validate(stage)
            extra.unlink()
            file.unlink()
            file.symlink_to(archive)
            with self.assertRaises(ValueError):
                source.validate(stage)

    def test_corrupt_archive_and_implicit_download_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "wrong.tar.gz"
            archive.write_bytes(b"unverified")
            with self.assertRaises(ValueError):
                source.verify_archive(archive)
            with patch.object(source, "manifest", return_value={}):
                with self.assertRaises(ValueError):
                    source.prepare(Path(temporary) / "target")

    def test_fetch_stream_pin_and_size_bound_without_network(self):
        class Response(io.BytesIO):
            def geturl(self):
                return source.URL

        class Opener:
            def open(self, request, timeout):
                if timeout != 30:
                    raise AssertionError("download socket must have a finite timeout")
                return Response(b"pinned")

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(source.urllib.request, "build_opener", return_value=Opener()), \
                patch.object(source, "ARCHIVE_SIZE", 6), \
                patch.object(source, "ARCHIVE_SHA256", hashlib.sha256(b"pinned").hexdigest()):
            output = Path(temporary) / "archive"
            source.fetch_archive(output)
            self.assertEqual(output.read_bytes(), b"pinned")
            with self.assertRaises(FileExistsError):
                source.fetch_archive(output)
            with patch.object(source, "ARCHIVE_SIZE", 5), self.assertRaises(ValueError):
                source.fetch_archive(Path(temporary) / "too-large")


if __name__ == "__main__":
    unittest.main()
