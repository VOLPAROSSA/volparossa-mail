# SPDX-License-Identifier: GPL-3.0-only
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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

    def test_watch_stops_while_another_owner_or_its_own_helper_is_active(self):
        profile = pickup.private_dir(self.root / "profile")
        private_state = pickup.private_dir(self.root / "state")
        journal = pickup.private_dir(private_state / "pickup")
        config = {"server": "http://127.0.0.1:8080", "username": "owner", "accountId": "a", "mailboxId": "inbox",
                  "controlSocket": str(self.root / "unavailable"), "identity": str(self.put(self.root, "identity")),
                  "passphraseFile": str(self.put(self.root, "passphrase"))}
        invitation = self.put(self.root, "invitation")
        connection = self.put(self.root, "connection", json.dumps(config).encode())
        marker = self.root / "active-helper"
        helper = self.put(self.root, "core", ("#!" + sys.executable + "\nimport os, time\nfrom pathlib import Path\n"
                          + "Path(" + repr(str(marker)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n").encode())
        helper.chmod(0o700)
        args = [sys.executable, "-B", Path(pickup.__file__).with_name("accept.py"), "--watch", "10", "--invitation",
                invitation, "--profile", profile, "--state-dir", private_state, "--connection", connection, "--core", helper]
        for blocked in ("other owner", "own helper"):
            with self.subTest(blocked=blocked):
                fd = os.open(journal, os.O_DIRECTORY)
                try:
                    if blocked == "other owner":
                        fcntl.flock(fd, fcntl.LOCK_EX)
                    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL) as child:
                        try:
                            child.stdin.write(b"test\n")
                            child.stdin.close()
                            link = Path("/proc") / str(child.pid) / "fd"
                            end = time.monotonic() + 5
                            while True:
                                if blocked == "other owner":
                                    try:
                                        if any(os.readlink(f) == str(journal) for f in link.iterdir()):
                                            break
                                    except FileNotFoundError:
                                        pass  # An unrelated descriptor closed during the observation.
                                elif marker.is_file() and marker.stat().st_size:
                                    break
                                self.assertIsNone(child.poll())
                                self.assertLess(time.monotonic(), end)
                                time.sleep(0.02)
                            child.terminate()
                            self.assertEqual(child.wait(timeout=3), 0)
                        finally:
                            if child.poll() is None:
                                child.kill()
                finally:
                    os.close(fd)
                if blocked == "other owner":
                    self.assertFalse(marker.exists())
                else:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(marker.read_text()), 0)
