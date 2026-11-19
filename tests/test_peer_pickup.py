# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/peer-mail"))
import pickup


class PickupJournal(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pending = pickup.private_dir(self.root / "pending")
        self.stage = pickup.private_dir(self.root / "fetch")
        self.item = pickup.private_dir(self.stage / ("a" * 64))

    def put(self, directory, name, data=b"keep"):
        path = directory / name
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def test_unexpected_files_leave_whole_fetch_in_place(self):
        payload = self.put(self.item, "payload")
        keep = self.put(self.item, "owner-notes")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            pickup.adopt(self.stage, self.pending)
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertEqual(payload.read_bytes(), b"keep")
        keep.unlink()
        keep = self.put(self.stage, "owner-notes")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            pickup.adopt(self.stage, self.pending)
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertTrue(payload.exists())

    def test_interrupted_fetch_releases_only_core_scratch(self):
        self.put(self.item, "payload")
        self.put(self.item, ".tmpABC234", b"")
        cache = pickup.private_dir(pickup.private_dir(self.stage / ".mailbox-ciphertext-ABC234") / "cache")
        self.put(cache, ".volparossa-index-v1")
        self.put(cache, "d" * 64)
        pickup.adopt(self.stage, self.pending)
        self.assertFalse(self.stage.exists())
        self.assertEqual(list(self.pending.iterdir()), [])

    def test_foreign_content_and_links_survive_retirement(self):
        retired = pickup.private_dir(self.root / "retiring")
        packet = self.put(retired, "payload")
        foreign = self.put(retired, "original")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            pickup.remove_files(retired, pickup.retiring_files(retired))
        foreign.rename(self.root / "original")
        (retired / "import-token").symlink_to(self.root / "original")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            pickup.remove_files(retired, pickup.retiring_files(retired))
        self.assertTrue(packet.is_file())
        self.assertEqual((self.root / "original").read_bytes(), b"keep")
